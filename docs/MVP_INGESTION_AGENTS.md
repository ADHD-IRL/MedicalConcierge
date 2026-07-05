# MVP: Multimodal Medicine & Supplement Ingestion Agents

Goal: the fastest path to a **fully functional** (not a mock/demo) agent pair
that can take a PDF, a photo of a handwritten note, or a photo of a pill
bottle, and produce structured, normalized, confidence-scored medication and
supplement records. This is the foundation everything else in
`ARCHITECTURE.md` builds on.

## Why this is the right thing to build first, and how to build it fast

The fastest path to "actually works on messy real-world input" is to **not
build a custom OCR/NER pipeline at all** and instead use a multimodal LLM
(Claude, vision-capable) as the extractor, with two disciplines that keep it
reliable enough to trust:

1. **Force structured output via tool-calling**, not "ask for JSON and hope."
   The Anthropic API lets you define a tool input schema and force the model
   to call it — the response is schema-validated at the API level, so there is
   no free-text parsing to get wrong.
2. **Never let the LLM's reading of a drug name be the final word.** Every
   name the model extracts gets normalized against RxNorm's
   `approximateTerm` endpoint, which is built to handle exactly this kind of
   fuzzy/misspelled/brand-vs-generic input. If RxNorm can't find a confident
   match, the record is kept but flagged `unnormalized` rather than guessed.

This combination — vision LLM for extraction, RxNorm for ground-truth
normalization, tool-calling for structural reliability — gets you a genuinely
functional MVP in one implementation pass, without waiting on a licensed
database (NatMed Pro) or building custom OCR/handwriting models.

## Pipeline

```
file (pdf/jpg/png/heic)
   │
   ▼
file_loader.load_as_images()        # PDF → one PNG per page (PyMuPDF rasterization)
   │                                 # image formats → pass through as-is
   ▼
multimodal_extractor.extract()      # Claude vision + forced tool-call
   │                                 # → list[ExtractedMedication | ExtractedSupplement]
   │                                 #   each with extraction_confidence + raw text
   ▼
normalization.rxnorm_client         # approximateTerm.json per extracted name
   │                                 # → rxcui + canonical name + match score
   ▼
agents.common.process_document      # unified pass: combine + compute overall confidence
   │
   ▼
storage.store                        # SQLite, one row per normalized record
   │
   ▼
api routes (/ingest, /records, /export)
```

### Why PDFs are rasterized instead of sent as text

Real-world medical PDFs are frequently scanned images with no text layer
(faxed records, scanned handwritten forms), so extracting a text layer first
and falling back to images only sometimes would add complexity for no
reliability gain. Rasterizing every page to an image and always going through
the vision path is simpler and uniformly reliable — a printed PDF page is
just an easy image for the vision model to read.

### Multimodal extraction prompt design

The extraction call is given **all pages/images from one document** in a
single request (multi-image messages are supported), plus a system prompt
that:
- States the source type if known (`bottle_label`, `handwritten_note`,
  `printed_document`) to calibrate the model's own confidence reporting.
- Instructs it to extract *every* medication/supplement mention, including
  ones that are crossed out, discontinued, or ambiguous — and to say so via
  the `ambiguities` field rather than omitting or silently resolving them.
- Instructs it to never infer a drug identity it isn't confident about from
  context; if a bottle label is partially obscured, extract what's legible
  and flag the rest as low confidence rather than guessing the full name.
- Requires a `raw_text` field per record — the verbatim text the model read
  — so a human reviewer can always check the extraction against what's
  literally on the page, independent of how the model interpreted it.

### Confidence, concretely (MVP implementation)

- `extraction_confidence` (0–1, set by the model per-record, anchored to
  explicit rubric text in the prompt: 0.9+ only for clearly printed/typed
  text with no ambiguity; sub-0.5 for illegible handwriting or guessed
  abbreviations).
- `normalization_confidence` (0–1, derived from RxNorm's approximate-match
  score, normalized to 0–1).
- `overall_confidence = extraction_confidence * normalization_confidence`
  (deliberately multiplicative — a perfectly-read name that fails to
  normalize, or a well-normalized guess from an illegible scrawl, should both
  pull the overall score down).
- Anything below a configurable threshold (default 0.6) is marked
  `needs_review` in storage and should be surfaced first in the UI's Inbox.

## What's stubbed vs. production-ready in this MVP

| Piece | MVP state | Production upgrade path |
|---|---|---|
| Vision extraction | Real Claude API call, tool-forced schema | Same, tune prompt/model over time |
| PDF handling | Real (PyMuPDF rasterization) | Same; could add native PDF text-layer extraction as a fast-path when present |
| RxNorm normalization | Real API client (approximateTerm + property lookup) | Same; add UMLS auth for restricted endpoints if needed |
| Supplement normalization | RxNorm first, then a small curated local synonym table (~40 common supplements) | Replace/augment local table with TRC NatMed Pro API once licensed |
| Interaction checking | Curated local rules engine (`app/interactions/`): well-documented drug-drug, drug-supplement, vitamin/mineral, duplicate-therapy, and nutrient-depletion rules, screened deterministically after every ingest | Swap/augment the rule source with openFDA labels + TRC NatMed Pro behind the same rule interface (see ARCHITECTURE.md §2.3) |
| Storage | Local SQLite, unencrypted (dev-grade) | Encrypt at rest (SQLCipher or filesystem-level) before real PHI goes in |
| UI | Single static HTML page, upload + JSON table | Full SPA/timeline per ARCHITECTURE.md §4 |
| Auth | None (localhost dev) | Passkey/WebAuthn single-user auth before any network exposure |

## Running it

See `backend/README.md` for setup/run instructions. Requires an
`ANTHROPIC_API_KEY`. RxNorm calls require outbound HTTPS to
`rxnav.nlm.nih.gov` (no API key needed — it's a free public NLM API).

## Extending to the rest of the system

The medicine/supplement agents already write fully-normalized RxCUI-tagged
records to storage, which is exactly the input the Interaction Agent and
Supplement Gap Agent from `ARCHITECTURE.md` need — those can be built next as
new consumers of the same record store without touching this ingestion code.
