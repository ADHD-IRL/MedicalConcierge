# Personal Medical Concierge — System Architecture Plan

## 0. Problem framing

The user sees multiple uncoordinated doctors. Each doctor has a partial view: their
own notes, their own prescriptions, their own labs. Nobody holds the composite
picture. This system is that composite picture. It is a **single source of truth**
that:

1. **Ingests** everything — visit notes, discharge summaries, lab PDFs, photos of
   handwritten notes, photos of pill bottles, insurance EOBs, imaging reports.
2. **Normalizes** every drug, supplement, diagnosis, and lab value to standard
   vocabularies (RxNorm, ICD-10/SNOMED, LOINC) so the same thing said three
   different ways is recognized as one thing.
3. **Cross-checks** the normalized data against authoritative references to surface:
   - drug-drug interactions
   - drug-supplement interactions and nutrient depletion
   - duplicate therapy (two doctors prescribing the same class)
   - diagnoses that conflict with each other or with the medication list
   - documented contraindications (renal/hepatic dosing, age, pregnancy, allergies)
4. **Finds gaps**: symptoms mentioned in notes that were never diagnosed, labs that
   were flagged abnormal but never followed up, standard-of-care screenings that
   are overdue, and supplements that evidence supports for the user's specific
   conditions/deficiencies.
5. Presents all of this with an explicit **confidence score** per finding, because
   this system is a coordinator, not a diagnostician — every output should read like
   "here's what a careful second set of eyes would flag, and how sure it is," never
   an unqualified medical claim.
6. Lets the user **export** everything (their data, the findings, sourcing) at any
   time, and keeps everything **confidential** by default (self-hosted, encrypted,
   no ad-tech, minimal third-party data sharing).

This is not a diagnostic device and should never present itself as one. Every
finding is framed as "worth asking your doctor about," with citations back to the
source document and the reference database used.

---

## 1. High-level architecture

```
                      ┌─────────────────────────────────────────┐
                      │              Web UI (SPA)                │
                      │  Upload · Timeline · Med List · Findings  │
                      │  Confidence badges · Export · Search      │
                      └───────────────┬───────────────────────────┘
                                      │ HTTPS (local network / VPN only)
                      ┌───────────────▼───────────────────────────┐
                      │            API Gateway (FastAPI)           │
                      │  Auth · Rate limit · Audit log              │
                      └───────────────┬───────────────────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        │                             │                              │
┌───────▼────────┐          ┌─────────▼─────────┐          ┌─────────▼─────────┐
│ Ingestion Agent  │          │  Coordinator Agent │          │   Query / Chat     │
│ (multimodal OCR  │──────▶  │ (orchestrates the   │◀───────▶│   Agent (Q&A over  │
│  + extraction)   │  events │  specialist agents  │  events  │   the record set)  │
└───────┬─────────┘          │  below, merges,      │          └───────────────────┘
        │                    │  de-dupes, ranks)     │
        │                    └──────────┬───────────┘
        │                               │
        │              ┌────────────────┼────────────────────┬───────────────────┐
        │              │                │                    │                   │
  ┌─────▼──────┐ ┌──────▼───────┐ ┌───────▼────────┐ ┌────────▼────────┐ ┌────────▼────────┐
  │Normalization│ │  Medicine /   │ │  Interaction /  │ │  Diagnosis Gap   │ │ Supplement Gap   │
  │   Engine    │ │  Supplement   │ │  Interference   │ │  Analysis Agent  │ │ Analysis Agent   │
  │ (RxNorm,    │ │  Agents       │ │  Agent          │ │ (unaddressed     │ │ (evidence-based  │
  │  ICD-10/    │ │ (this doc's   │ │ (openFDA, KEGG,  │ │  symptoms, stale │ │  recs + nutrient │
  │  SNOMED map)│ │  MVP focus)   │ │  NatMed Pro)     │ │  labs, overdue   │ │  depletion,      │
  │             │ │               │ │                  │ │  screenings)     │ │  Examine matrix) │
  └─────┬──────┘ └──────┬───────┘ └───────┬────────┘ └────────┬────────┘ └────────┬────────┘
        │               │                 │                    │                   │
        └───────────────┴─────────────────┴────────────────────┴───────────────────┘
                                      │
                      ┌───────────────▼───────────────────────────┐
                      │        Data layer (Postgres + object       │
                      │        storage for source documents,       │
                      │        both encrypted at rest)              │
                      └───────────────┬───────────────────────────┘
                                      │
                      ┌───────────────▼───────────────────────────┐
                      │   Export service (FHIR bundle / PDF / CSV) │
                      └───────────────────────────────────────────┘
```

Everything is **event-driven around a single canonical record store**. Ingestion
never talks directly to the findings agents — it writes normalized records, and
every downstream agent (interaction checker, gap analysis, supplement analysis)
subscribes to "new/changed record" events and re-runs its own analysis
incrementally. This means:

- Adding a new document re-triggers only the checks that could be affected by it.
- Every finding stores which record version(s) produced it, so re-running is
  idempotent and explainable.

---

## 2. Core components

### 2.1 Normalization Engine (foundation layer)

The mandatory first hop for anything extracted from a document, before it's
allowed to touch any cross-referencing logic.

| Domain | Standard | Source |
|---|---|---|
| Medications & supplements | RxNorm (RxCUI) | RxNorm REST API (NLM, free) |
| Diagnoses/problems | ICD-10-CM → SNOMED CT map | UMLS / NLM Clinical Table Search API |
| Labs | LOINC | LOINC API / regenstrief |
| Allergies | RxNorm + UNII (for substances) | RxNorm + FDA UNII |

Everything the ingestion agent extracts as free text gets a normalization pass
that attaches a `(code_system, code, canonical_name, match_confidence)` tuple. If
normalization fails to find a confident match, the record is still stored — just
flagged `unnormalized`, and excluded from automated cross-checks until reviewed
(never silently dropped or silently guessed).

### 2.2 Medicine & Supplement Agents (this MVP's focus — see part 2 below)

Read raw documents/images, extract every medication/supplement mention with
dosage, frequency, route, prescriber, and date, and hand off to the
Normalization Engine. Full detail in `docs/MVP_INGESTION_AGENTS.md`.

### 2.3 Interaction / Interference Agent

Once medications+supplements are normalized RxCUIs:

- **openFDA** (`/drug/label`, `/drug/event`) for official prescribing info,
  contraindications, boxed warnings, and real-world adverse event signal.
- **KEGG DRUG / KEGG pathway** for mechanistic interactions (shared CYP450
  metabolism, shared receptor targets) — this is what lets the system explain
  *why* two things interact, not just *that* they do.
  - Practical note: **DrugBank** and RxNorm's own `interaction` API predecessor
    (NLM retired its own interaction API in 2024) are worth evaluating too;
    DrugBank's academic/personal-use license is often more directly usable than
    KEGG's raw pathway data for a "will these two things fight" check. Plan for
    the interaction agent to be a pluggable adapter over more than one source,
    since none of these APIs alone has full coverage.
  - **TRC Healthcare NatMed Pro** is the right long-term source for
    drug-supplement interactions and nutrient depletion (their differentiator).
    It's a paid/licensed API, so the MVP ships without it and instead uses a
    curated local table (`supplement_terms.py` in the MVP) with hooks to swap in
    a real NatMed Pro client later.
- Cross-doctor duplicate-therapy detection: group active medications by
  therapeutic class (RxNorm has class relationships) and flag same-class
  overlaps prescribed by different sources.

### 2.4 Diagnosis Gap Analysis Agent

- Extracts every diagnosis/problem mention (visit notes, discharge summaries)
  and normalizes to ICD-10/SNOMED.
- Cross-references the medication list against the diagnosis list: flags
  medications with no corresponding indication on file ("why is this person on
  a statin — no lipid diagnosis anywhere?") and diagnoses with no corresponding
  treatment or follow-up.
- Scans note text for symptom mentions that never became a diagnosis, and labs
  flagged abnormal with no subsequent action, using an LLM extraction pass over
  visit notes plus a rules layer for objective lab flags (out-of-range values
  are deterministic, not inferred).
- Flags overdue standard-of-care screening (age/sex-appropriate — e.g., A1c
  cadence for a diabetes diagnosis, colonoscopy interval) against a small local
  ruleset, not a diagnostic engine.

### 2.5 Supplement Gap / Benefit Agent

- For every active medication, checks the nutrient-depletion table
  (NatMed Pro in production, curated stub in MVP) and proposes the depleted
  nutrient as a candidate supplement, sourced back to the specific mechanism.
- For every diagnosis, checks an evidence matrix (Examine.com's human-trial
  grading is the right long-term source; likely needs a negotiated license or
  manual curation since Examine doesn't offer a public API) for supplements
  with credible evidence for that condition.
- Every suggestion carries an explicit evidence-grade confidence (e.g., "strong
  human trial evidence" vs. "preliminary/mechanistic only") — this agent should
  be the most conservative of all of them, since it's the one most likely to be
  read as unsolicited medical advice.

### 2.6 Coordinator Agent

Thin orchestration layer: listens for new records, fans out to the relevant
specialist agents, de-duplicates overlapping findings (e.g., both the
interaction agent and gap agent flagging the same missing-indication issue),
ranks findings by severity × confidence, and writes the merged finding set that
the UI reads. This is the only component that needs to reason about "the whole
picture" — everything else is a narrow specialist.

### 2.7 Query/Chat Agent

A conversational layer over the canonical record store for ad hoc questions
("what did Dr. Chen prescribe in March?", "show me every time my magnesium
came up abnormal"). Read-only against the same data the other agents use;
answers always cite the source record.

---

## 3. Confidence framework

Every extracted fact and every derived finding carries confidence, and the two
are handled differently:

**Extraction confidence** (did we read the source correctly?)
- Set by the extracting agent based on image/OCR clarity, ambiguity in
  handwriting, and whether the model had to guess at an abbreviation.
- Buckets: `high` (printed, unambiguous) / `medium` (typed but ambiguous field,
  or clearly-written handwriting) / `low` (illegible handwriting, guessed
  abbreviation, cut-off text) — plus a raw 0–1 float for sorting/filtering.
- Low-confidence extractions are surfaced to the user for a one-tap
  confirm/correct before they're allowed to feed downstream checks silently —
  this human-in-the-loop step is what makes it safe to use an LLM for OCR on
  medical data.

**Finding confidence** (how sure are we this is a real issue?)
- A function of (a) how authoritative the source is (FDA label > adverse
  event report frequency > mechanistic-only KEGG pathway inference), (b) how
  confident the underlying extraction/normalization was, and (c) whether
  multiple independent sources agree.
- Every finding shown in the UI displays: the confidence badge, *why* (which
  sources agreed/disagreed), and a direct link back to the source
  document(s) and reference database entries used.
- Findings are never phrased as diagnoses. Standard phrasing: "Flagged for
  review: constraint X from source Y, confidence Z — worth confirming with
  your doctor."

---

## 4. Interface

- **Single-user web app**, responsive enough for phone use (photographing a
  pill bottle at the pharmacy counter is a primary use case).
- Core views: **Inbox** (documents awaiting confirmation of low-confidence
  extractions), **Timeline** (chronological record of every visit/document),
  **Medication & Supplement List** (current + historical, normalized, with
  active interaction/overlap flags inline), **Findings** (ranked, filterable by
  confidence and category), **Ask** (chat/query agent).
- Upload is drag-and-drop or mobile camera capture, accepts PDF/JPG/PNG/HEIC,
  processes asynchronously with a visible per-document status (queued →
  extracting → normalizing → cross-checking → done).
- No multi-tenant concerns (single user), which simplifies auth to a single
  strong credential (passkey/WebAuthn preferred) + optional TOTP, rather than
  building out RBAC.

---

## 5. Confidentiality & security

This is the highest-sensitivity data category there is, and it's explicitly
**not** going to live behind a vendor's multi-tenant SaaS by default:

- **Self-hosted by default.** No third party ever holds the plaintext record
  store. See hosting recommendation below.
- **Encryption at rest** for both the database (Postgres with
  `pgcrypto`/full-disk encryption at minimum, transparent data encryption if
  available) and the object store holding original documents/images
  (age/AES-256 envelope encryption, keyed off a passphrase/keyfile that never
  touches the LLM vendor).
- **Encryption in transit**: TLS everywhere, including for the LAN-only
  deployment — self-signed or a private CA is fine since there's one user/one
  device set.
- **LLM vendor exposure is the one real privacy trade-off** — sending document
  images/text to Anthropic's (or another vendor's) API is the fastest way to
  get high-quality multimodal extraction, but it does mean PHI leaves the host.
  Mitigations, in order of preference:
  1. Use a vendor with a data-processing agreement that excludes prompts from
     training and sets a short/no retention window (Anthropic's API offers
     this contractually — confirm current terms before relying on it).
  2. Longer-term, offer a **local vision model** (e.g., a self-hosted
     Qwen2-VL/Llama-vision variant) as a drop-in replacement for the
     extraction call for users who want zero third-party exposure, accepting
     lower accuracy on messy handwriting as the trade-off.
  3. Redact/strip direct identifiers (name, DOB, MRN) from images before the
     vision call where feasible, re-attaching them locally after extraction.
- **Audit log** of every access/export, stored locally, not sent anywhere.
- **No analytics/telemetry** third parties, no ad SDKs, nothing that phones
  home besides the explicit reference-data APIs (RxNorm/openFDA/etc., which
  only ever receive drug/diagnosis *names*, never document images or PHI).
- **Backups** encrypted, stored on user-controlled storage (e.g., a second
  encrypted external drive or a personal encrypted cloud bucket the user
  already trusts), never on the app vendor's infrastructure because there is
  no app vendor — this is self-hosted software the user runs.

### 5.1 Export function

- One-click export of the full canonical record (all documents, extracted
  facts, findings, and confidence metadata) as:
  - A portable **FHIR R4 Bundle** (so it's importable into a real EHR/patient
    portal if the user ever wants to hand it to a new doctor).
  - A human-readable **PDF summary** (medication list, active findings,
    timeline) suitable for printing and bringing to an appointment.
  - Raw **JSON/CSV** for the data hoarder / power-user case, and as the
    system's own backup format.
- Export includes source document references so nothing is "found" that can't
  be traced back to its origin.

---

## 6. Software architecture recommendation

- **Backend**: Python (FastAPI). Python has the best ecosystem for both the
  medical-data libraries (fhir.resources, RxNorm/UMLS clients) and the
  LLM/agent tooling. Async throughout for I/O-bound extraction/API calls.
- **Agent orchestration**: keep it simple — a lightweight in-process event bus
  (e.g., a Postgres-backed job queue via `arq`/`procrastinate`, or just Celery
  if familiarity is higher) rather than a heavyweight agent framework. The
  "agents" here are mostly well-scoped functions with an LLM call inside a
  retry/validation loop, not autonomous long-horizon agents — don't
  over-engineer the orchestration layer for a single-user system.
- **Structured LLM output**: use tool-calling / forced function-call schemas
  (Pydantic-validated) for every extraction and finding-generation step, never
  free-text-then-regex-parse. This is what makes confidence scoring and
  re-running idempotent.
- **Database**: Postgres (canonical structured records, findings, audit log).
  Object storage (local disk or self-hosted MinIO / an encrypted bucket) for
  original source files.
- **Frontend**: a small React/Vite SPA (or, honestly, server-rendered
  HTMX/Jinja for something this scoped — fewer moving parts for a single-user
  app). Recommend starting with the simpler server-rendered option and only
  reaching for a full SPA if the UI complexity demands it.
- **Background workers**: a worker process pool for the async
  extract→normalize→cross-check pipeline, separate from the request-serving
  API process, so a slow OCR call on a 40-page discharge summary never blocks
  the UI.

## 7. Hosting architecture recommendation

Because this is explicitly personal, single-user, and privacy-critical, the
recommendation is **self-hosted, not cloud-SaaS**:

- **Primary recommendation: a home server / NAS (e.g., a small always-on Linux
  box, or a Synology/TrueNAS box with Docker support)** running the whole
  stack via **Docker Compose** (API, worker, Postgres, object storage,
  reverse proxy), reachable only over the home LAN plus a **personal VPN**
  (Tailscale/WireGuard) for remote/mobile access — never exposed directly to
  the public internet. This keeps 100% of the data under the user's physical
  control and avoids any cloud provider's compliance/subpoena surface
  entirely.
- **Fallback recommendation** if the user doesn't want to run home hardware: a
  small **self-managed VPS** (e.g., Hetzner/DigitalOcean) running the same
  Docker Compose stack, with full-disk encryption, restricted to VPN-only
  ingress (Tailscale works here too), and encrypted off-site backups. This is
  "self-hosted" in the sense that the user, not a SaaS vendor, controls the
  keys and the data — it's just renting the box instead of owning it.
- **Explicitly avoid**: multi-tenant medical-record SaaS platforms, or
  deploying this as a public web app — there is no product reason for this
  single-user tool to ever be internet-facing without a VPN in front of it.
- **The one external dependency that's hard to avoid**: the LLM vision API
  call for extraction, and the reference-data lookups (RxNorm/openFDA/KEGG/
  NatMed Pro). These are outbound calls with narrow, well-understood payloads
  (image bytes to the LLM vendor; drug/diagnosis names to the reference APIs)
  — document that scope explicitly to the user rather than pretending the
  system is 100% air-gapped.
- **Scaling note**: none needed. This is a single-user system; the hosting
  plan should optimize for *durability and privacy*, not throughput.

---

## 8. Build order (suggested)

1. Normalization Engine + Medicine/Supplement ingestion agents (multimodal) —
   **this is the MVP covered in `docs/MVP_INGESTION_AGENTS.md`**, because
   nothing else works without normalized medication data.
2. Canonical data store + basic timeline/medication-list UI (no findings yet)
   — get real data flowing and confirmable before building analysis on top of
   possibly-wrong extractions.
3. Interaction/Interference Agent (openFDA first — free and immediately
   useful; KEGG/DrugBank/NatMed Pro as licensed sources come online).
4. Export function (FHIR/PDF/CSV) — cheap to build once the data model is
   stable, and de-risks "what if I want to stop using this."
5. Diagnosis Gap Analysis Agent (needs diagnosis extraction/normalization,
   which is a similar but separate pipeline from medications).
6. Supplement Gap/Benefit Agent (depends on both 3 and 5 being in place).
7. Coordinator ranking/de-dup + Query/Chat agent last, once there's enough
   real findings volume to make ranking meaningful.
