"""Multimodal extraction: sends page/photo images to a vision-capable Claude
model and forces a structured tool call so the response is schema-valid by
construction (no free-text-then-regex parsing).
"""

from __future__ import annotations

import base64
import json

import anthropic

from app.config import get_settings
from app.schemas import ExtractedItem, RecordKind, SourceType

_TOOL_NAME = "record_extraction"

# The API caps total request size at ~32 MB. Base64 inflates bytes by ~4/3,
# so 15 MB of raw image bytes per request (~20 MB encoded) leaves ample
# headroom for the prompt. Long documents are sent as several requests and
# the extracted items merged.
BATCH_MAX_BYTES = 15_000_000
BATCH_MAX_IMAGES = 20

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "raw_text": {"type": "string"},
        "name_as_written": {"type": "string"},
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


def _system_prompt(record_kind: RecordKind) -> str:
    subject = "medications (prescription and OTC drugs)" if record_kind == RecordKind.medicine else "dietary supplements, vitamins, minerals, and herbal products"
    other = "supplements" if record_kind == RecordKind.medicine else "medications"
    return f"""You are a meticulous medical records transcriptionist. You will be shown
one or more images that are all pages/photos of a single document (a visit
note, a prescription, a pill bottle label, or a handwritten note).

Extract every distinct {subject} mention using the `{_TOOL_NAME}` tool. Do not
extract {other} — a separate pass handles those. Include items that are
crossed out, marked discontinued, or otherwise ambiguous; note that in
`ambiguities` rather than omitting the item.

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


def extract_records(
    images: list[tuple[bytes, str]],
    record_kind: RecordKind,
    source_type: SourceType = SourceType.other,
) -> list[ExtractedItem]:
    """Runs vision + forced-tool-call extraction over all provided page
    images (in one or more batched requests) and returns validated
    ExtractedItem records."""

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    batches = _batch_images(images)
    items: list[ExtractedItem] = []
    for index, batch in enumerate(batches, start=1):
        content: list[dict] = []
        for image_bytes, mime_type in batch:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": base64.standard_b64encode(image_bytes).decode("utf-8"),
                    },
                }
            )
        part_note = (
            f" These pages are part {index} of {len(batches)} of the same document; "
            "other parts are handled separately - extract only what is visible here."
            if len(batches) > 1
            else ""
        )
        content.append(
            {
                "type": "text",
                "text": f"The source_type for this document is '{source_type.value}' unless "
                f"individual items clearly indicate otherwise.{part_note} Extract now.",
            }
        )

        response = client.messages.create(
            model=settings.extraction_model,
            max_tokens=4096,
            system=_system_prompt(record_kind),
            tools=[_TOOL_DEFINITION],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": content}],
        )
        items.extend(_parse_response(response))

    return items


def _parse_response(response: anthropic.types.Message) -> list[ExtractedItem]:
    for block in response.content:
        if block.type == "tool_use" and block.name == _TOOL_NAME:
            raw_items = block.input.get("items", [])
            return [ExtractedItem.model_validate(item) for item in raw_items]
    raise ValueError(
        f"Model did not return a '{_TOOL_NAME}' tool call: "
        f"{json.dumps([b.type for b in response.content])}"
    )
