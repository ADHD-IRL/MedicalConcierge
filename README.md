# Medical Concierge

A personal medical management system: a single self-hosted coordinator for
someone dealing with multiple uncoordinated doctors, diagnoses, and
medications. It ingests medical documents (visit notes, discharge summaries,
prescriptions, photos of pill bottles, handwritten notes), normalizes
everything to standard medical vocabularies, and cross-checks for
interactions, duplicate therapy, unaddressed symptoms, and beneficial
supplements — every finding scored with an explicit confidence level and
traceable back to its source.

- **Windows desktop — easiest way to run it:** [`docs/WINDOWS_SETUP.md`](docs/WINDOWS_SETUP.md) (double-click `Start-MedicalConcierge.bat`)
- **New developer? Start here:** [`docs/DEVELOPER_GUIDE.pdf`](docs/DEVELOPER_GUIDE.pdf) — how the whole app works, end to end, including how Docker is used (source: [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md), rebuild with `docs/build_dev_guide.py`)
- **The SME panel (29 experts, 8 adversarial rounds):** [`backend/data/panel_registry.json`](backend/data/panel_registry.json) — the roster and protocol as data; see §5.9 of the developer guide
- **Lab ingestion and the answer loop:** §5.10 of the developer guide — labs ride the same vision pass; the panel's open questions become trackable, and answering them makes the next review converge
- **Full system architecture & hosting plan:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **MVP: multimodal medicine & supplement ingestion agents:** [`docs/MVP_INGESTION_AGENTS.md`](docs/MVP_INGESTION_AGENTS.md)
- **Running the MVP locally:** [`backend/README.md`](backend/README.md)
- **Self-hosted deployment (Docker Compose + reverse proxy):** [`deploy/README.md`](deploy/README.md)

This is not a diagnostic device. Every finding is meant to be brought to a
real doctor, not acted on alone.
