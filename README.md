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
- **Full system architecture & hosting plan:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **MVP: multimodal medicine & supplement ingestion agents:** [`docs/MVP_INGESTION_AGENTS.md`](docs/MVP_INGESTION_AGENTS.md)
- **Running the MVP locally:** [`backend/README.md`](backend/README.md)
- **Self-hosted deployment (Docker Compose + reverse proxy):** [`deploy/README.md`](deploy/README.md)

This is not a diagnostic device. Every finding is meant to be brought to a
real doctor, not acted on alone.
