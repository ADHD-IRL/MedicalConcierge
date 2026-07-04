# Medical Concierge — Ingestion MVP (backend)

Multimodal medicine & supplement ingestion agents. See
`../docs/MVP_INGESTION_AGENTS.md` for the design rationale and
`../docs/ARCHITECTURE.md` for the full system plan this fits into.

## Setup

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY
```

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 — upload a PDF, or a photo of a prescription,
handwritten note, or medicine/supplement bottle. Extracted records are
normalized against RxNorm and stored in the local SQLite file
(`medconcierge.sqlite3` by default).

Export everything at any time from the UI, or directly:

```bash
curl http://localhost:8000/api/export?format=csv -o export.csv
curl http://localhost:8000/api/export?format=json -o export.json
```

## Test

```bash
pytest
```

Tests mock the Anthropic vision call and the RxNorm HTTP client, so they run
without network access or an API key. Running the app itself requires a real
`ANTHROPIC_API_KEY` and outbound HTTPS access to `rxnav.nlm.nih.gov`.

## Notes

- This is an MVP for the ingestion agents specifically — no interaction
  checking, diagnosis gap analysis, or authentication yet (see
  `docs/ARCHITECTURE.md` build order).
- Storage is plain, unencrypted SQLite. Do not put real medical data on a
  shared/multi-user machine with this as-is — see
  `docs/ARCHITECTURE.md` section 5 before any real use.
