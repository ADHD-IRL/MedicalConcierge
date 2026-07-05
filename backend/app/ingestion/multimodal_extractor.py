"""Multimodal extraction: sends page/photo images to a vision-capable Claude
model and forces a structured tool call so the response is schema-valid by
construction (no free-text-then-regex parsing).
"""

from __future__ import annotations

import base64
import json
import logging

import anthropic

from app.config import get_settings
from app.schemas import ExtractedItem, RecordKind, SourceType  # noqa: F401 (SourceType used in schema enum)

_TOOL_NAME = "record_extraction"

# The API caps total request size at ~32 MB. Base64 inflates bytes by ~4/3,
# so 15 MB of raw image bytes per request (~20 MB encoded) leaves ample
# headroom for the prompt. Long documents are sent as several requests and
# the extracted items merged.
BATCH_MAX_BYTES = 15_000_000
# Dense pages (e.g. a facility medication chart) can each yield many items,
# and every item costs output tokens - keep batches small so one response
# never approaches the output ceiling.
BATCH_MAX_IMAGES = 5
MAX_OUTPUT_TOKENS = 16384


class ExtractionTruncated(RuntimeError):
    """A single page produced more extraction output than the model can
    return in one response. Practically unreachable after batch splitting;
    surfaced as a clear error rather than silently dropping records."""

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "raw_text": {"type": "string"},
        "name_as_written": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": [k.value for k in RecordKind],
            "description": "medicine = prescription or OTC drug; supplement = "
            "vitamin, mineral, herbal, or other dietary supplement.",
        },
        "dosage": {"type": ["string", "null"]},
        "form": {"type": ["string", "null"]},
        "route": {"type": ["string", "null"]},
        "frequency": {"type": ["string", "null"]},
        "prescriber_or_source": {"type": ["string", "null"]},
        "date_documented": {"type": ["string", "null"]},
        "source_type": {
            "type": "string",
            "enum": [s.value for s in SourceType],
        },
        "extraction_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "ambiguities": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "raw_text",
        "name_as_written",
        "kind",
        "source_type",
        "extraction_confidence",
        "ambiguities",
    ],
}

_TOOL_DEFINITION = {
    "name": _TOOL_NAME,
    "description": "Report every medication or supplement mention found in the provided images.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": _ITEM_SCHEMA},
        },
        "required": ["items"],
    },
}

_CONFIDENCE_RUBRIC = """
Confidence rubric for `extraction_confidence` (0.0-1.0):
- 0.9-1.0: clearly printed/typed text, no ambiguity in name, dose, or frequency.
- 0.6-0.89: legible but with minor ambiguity (e.g. a typical abbreviation, a
  slightly unclear digit) that you resolved with reasonable confidence.
- 0.3-0.59: partially legible handwriting, a guessed abbreviation, or a
  partially obscured label where you filled in a plausible reading.
- 0.0-0.29: mostly illegible; you are essentially guessing.
Never silently resolve genuine ambiguity — record it in `ambiguities` even
when you still report your best-guess field values.
"""


def _system_prompt() -> str:
    return f"""You are a meticulous medical records transcriptionist. You will be shown
one or more images that are all pages/photos of a single document (a visit
note, a medication chart, a prescription, a pill or supplement bottle label,
or a handwritten note).

Extract every distinct medication AND dietary supplement mention using the
`{_TOOL_NAME}` tool. Classify each item's `kind`: 'medicine' for prescription
and OTC drugs, 'supplement' for vitamins, minerals, herbal, and other dietary
supplements. Set each item's `source_type` from what you actually see on the
page it appears on (printed_document, handwritten_note, bottle_label, ...).

Rules that prevent the most common transcription errors:
- Emit exactly ONE item per distinct prescription or supplement. When a line
  shows a brand and generic name together (e.g. "Eliquis (apixaban)"), that
  is ONE item - put the full text in raw_text and the primary name in
  name_as_written, never two items.
- When a dose has been changed on the page (crossed out, overwritten,
  annotated), emit ONE item with the CURRENT dose, and describe the
  superseded/crossed-out value in `ambiguities` - never separate items for
  old and new doses of the same medication.
- Include items marked discontinued or crossed out entirely (note that in
  `ambiguities` rather than omitting them).
- After your first pass, re-scan every page specifically for vitamins,
  minerals, herbal products, and OTC supplements - these are the most
  commonly missed items on mixed medication charts.

For each item, always include the verbatim `raw_text` you read, exactly as
written, in addition to the structured fields. If a field isn't present on the
document, leave it null rather than guessing. Never infer a drug's full
identity from context alone if the visible text doesn't support it -- report
what's legible and flag the rest in `ambiguities`.
{_CONFIDENCE_RUBRIC}
If there are no relevant items in the image(s), call the tool with an empty
`items` list.
"""


def _batch_images(images: list[tuple[bytes, str]]) -> list[list[tuple[bytes, str]]]:
    """Split page images into request-sized batches so a long document never
    exceeds the API's total-request limit."""

    batches: list[list[tuple[bytes, str]]] = []
    current: list[tuple[bytes, str]] = []
    current_bytes = 0
    for image in images:
        size = len(image[0])
        if current and (current_bytes + size > BATCH_MAX_BYTES or len(current) >= BATCH_MAX_IMAGES):
            batches.append(current)
            current, current_bytes = [], 0
        current.append(image)
        current_bytes += size
    if current:
        batches.append(current)
    return batches


def extract_records(images: list[tuple[bytes, str]]) -> list[ExtractedItem]:
    """Runs vision + forced-tool-call extraction over all provided page
    images (in one or more batched requests) and returns validated
    ExtractedItem records - medicines and supplements together, each
    classified by the model. An optional verification pass per batch
    catches items the first pass missed."""

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    batches = _batch_images(images)
    multi_part = len(batches) > 1
    items: list[ExtractedItem] = []
    for batch in batches:
        batch_items = _extract_batch(client, settings, batch, multi_part)
        if settings.enable_verification_pass:
            batch_items += _verification_pass(client, settings, batch, batch_items)
        items.extend(batch_items)
    return items


def _image_blocks(batch: list[tuple[bytes, str]]) -> list[dict]:
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": base64.standard_b64encode(image_bytes).decode("utf-8"),
            },
        }
        for image_bytes, mime_type in batch
    ]


def _call(client: anthropic.Anthropic, settings, content: list[dict]) -> anthropic.types.Message:
    return client.messages.create(
        model=settings.extraction_model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=_system_prompt(),
        tools=[_TOOL_DEFINITION],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": content}],
    )


def _extract_batch(
    client: anthropic.Anthropic,
    settings,
    batch: list[tuple[bytes, str]],
    multi_part: bool,
) -> list[ExtractedItem]:
    """One extraction request. If the response hits the output-token ceiling
    (a very dense document), the batch is split in half and retried rather
    than silently returning a truncated - possibly empty - item list."""

    part_note = (
        " These pages are part of a longer document processed in sections - "
        "extract only what is visible here."
        if multi_part
        else ""
    )
    content = _image_blocks(batch) + [
        {"type": "text", "text": f"Extract every medication and supplement now.{part_note}"}
    ]

    response = _call(client, settings, content)

    if response.stop_reason == "max_tokens":
        if len(batch) > 1:
            mid = len(batch) // 2
            return _extract_batch(client, settings, batch[:mid], True) + _extract_batch(
                client, settings, batch[mid:], True
            )
        raise ExtractionTruncated(
            "One page contains more text than can be extracted in a single "
            "pass. Try uploading a clearer or cropped version of that page."
        )

    return _parse_response(response)


def _verification_pass(
    client: anthropic.Anthropic,
    settings,
    batch: list[tuple[bytes, str]],
    found: list[ExtractedItem],
) -> list[ExtractedItem]:
    """Second look at the same pages: 'here is what was already extracted -
    report ONLY what is missing.' Targets the whole-category-missed failure
    class (e.g. every supplement on a mixed chart being skipped). Best-effort:
    a failure here never sinks the upload."""

    already = "\n".join(
        f"- {i.name_as_written} ({i.dosage or 'no dose'})" for i in found
    ) or "(nothing was extracted)"
    content = _image_blocks(batch) + [
        {
            "type": "text",
            "text": "VERIFICATION PASS. The following items were already extracted "
            f"from these pages:\n{already}\n\nRe-examine every page, including "
            "margins and handwritten additions. Call the tool with ONLY items "
            "that appear on these pages but are MISSING from the list above - "
            "especially supplements, vitamins, and minerals. If nothing is "
            "missing, call the tool with an empty items list. Do NOT repeat "
            "items already listed.",
        }
    ]
    try:
        response = _call(client, settings, content)
        if response.stop_reason == "max_tokens":
            return []
        extras = _parse_response(response)
    except Exception:
        logging.getLogger(__name__).warning("Verification pass failed", exc_info=True)
        return []

    # Defensive de-dup: drop anything whose name matches an existing item.
    seen = {i.name_as_written.strip().lower() for i in found}
    fresh = [e for e in extras if e.name_as_written.strip().lower() not in seen]
    if fresh:
        logging.getLogger(__name__).info(
            "Verification pass recovered %d missed item(s): %s",
            len(fresh), ", ".join(i.name_as_written for i in fresh),
        )
    return fresh


def _parse_response(response: anthropic.types.Message) -> list[ExtractedItem]:
    for block in response.content:
        if block.type == "tool_use" and block.name == _TOOL_NAME:
            items: list[ExtractedItem] = []
            invalid = 0
            for raw in block.input.get("items", []):
                try:
                    items.append(ExtractedItem.model_validate(raw))
                except Exception:
                    invalid += 1
            if invalid:
                logging.getLogger(__name__).warning(
                    "Dropped %d malformed extraction item(s) out of %d",
                    invalid, invalid + len(items),
                )
            return items
    raise ValueError(
        f"Model did not return a '{_TOOL_NAME}' tool call: "
        f"{json.dumps([b.type for b in response.content])}"
    )
