# Becoming an app

Where this is today, the fork in the road ahead, and which decisions have
already been made so that both branches stay open.

---

## 1. Where it is today

A single-user FastAPI service with a bundled web UI, launched by
double-clicking a `.bat` file on a Windows desktop. SQLite on local disk, no
accounts, no auth, no network exposure. The only outbound traffic is the
Anthropic API (document images, and the conversation) and RxNorm (drug names).

That is a deliberate posture, not an unfinished one. `ARCHITECTURE.md` §7
argues explicitly *against* multi-tenant medical-record SaaS for this tool,
and nothing below overrides that — it just makes the alternatives explicit.

The important structural fact: **everything is already an API**. The web UI is
a client of `/api`, not a privileged insider. A native client that spoke the
same endpoints would have the same capabilities, today, with no server
changes.

---

## 2. The fork

"An app" means two very different products, and the choice is a real one.

### Path A — a personal app for this person (and people like them)

A native iOS/Android client talking to the user's *own* server: either a home
box reachable over Tailscale/WireGuard, or a small VPS they control. Still
single-tenant, still their data on their hardware. The app is a better
interface — camera-native document capture, notifications before an
appointment, something in your pocket in the pharmacy queue.

- **Keeps** the privacy argument entirely intact. No one else's records ever
  touch the system.
- **Costs** the user a server and a VPN. Realistically this limits it to
  technical users or ones with a technical helper.
- **Regulatory surface**: small. The user is their own data controller.

### Path B — a product other people sign up for

Hosted, multi-tenant, accounts, billing. Reaches the people who need this most
and are least able to run a server.

- **Requires** everything Path A does, plus: authentication, per-tenant data
  isolation enforced at the storage layer, encryption at rest with
  per-tenant keys, audit logging, backup/restore, a privacy policy, and a
  deletion path that actually deletes.
- **Regulatory surface**: large, and it changes character. Holding other
  people's medical records commercially in the US means HIPAA applies, which
  means a **BAA with every subprocessor** — including the model provider.
  Anthropic offers one; it has to be in place *before* real patient data flows.
- **The honest warning**: this is where a weekend project becomes a company.
  The engineering is the small part.

**These are not sequential.** Path A is reachable from here in weeks. Path B
is a different undertaking, and the right time to decide is before building
auth, not after.

---

## 3. Decisions already made to keep both open

These were taken while building the assistant, because they are cheap now and
expensive later.

| Decision | Why it matters later |
|---|---|
| **`/api/v1` is served alongside `/api`** (`app/main.py`) | An installed native app pins to a versioned contract. A future breaking change ships as `/api/v2` while old installs keep working — impossible to retrofit once clients exist in the wild. |
| **Structured responses, never rendered markup** | `/api/chat` returns `answer`, `citations[]`, `actions[]`, `questions_for_clinician[]` as data. A SwiftUI or Compose client renders citations as chips and actions as confirm sheets natively. Had the server returned HTML, every native client would need a WebView or a rewrite. |
| **`owner_id` on `chat_messages` from its first migration** | The one column that is genuinely painful to retrofit, because it has to reach every query at once. It defaults to `'local'` and costs nothing today. |
| **All logic server-side** | The client stays thin. Model prompts, the panel, safety screens and the guard live on the server, so they can be fixed without an app-store release — which matters enormously for anything safety-related. |
| **Storage behind store classes** | `RecordStore` / `MedListStore` are the only things that touch SQL. Moving to Postgres is a change in two files, not two hundred. |

### The `owner_id` migration, when it comes

Only `chat_messages` carries it so far. The remaining tables (`records`,
`labs`, `list_items`, `list_history`, `baselines`, `observations`,
`panel_reviews`) need the same column before multi-tenancy is possible. In
SQLite this is non-destructive and fast:

```sql
ALTER TABLE records ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'local';
CREATE INDEX idx_records_owner ON records (owner_id);
```

The real work is not the DDL — it is that **every query must then filter on
it**, and a single missed `WHERE owner_id = ?` is a cross-patient data leak.
Do this as one change, with the store classes taking `owner_id` in their
constructor rather than per-method, so a forgotten filter is a type error
rather than a silent breach.

---

## 4. What Path A needs

Roughly in order:

1. **Document capture on device.** The camera is the single biggest UX win —
   photograph a bottle or a discharge summary where you are standing. The
   ingestion endpoint already accepts an image upload; this is client work.
2. **Auth, even single-user.** A device token or passcode. Not for
   multi-tenancy — so that a phone on a shared network is not an open door.
3. **A time model.** Appointments, due dates, lab staleness. This is what
   makes notifications meaningful, and it is the prerequisite for the
   assistant speaking first rather than only answering.
4. **Notifications.** "Your appointment is Thursday — here are the two things
   worth raising" is the moment this stops being a tool you remember to open.
5. **Offline read.** The list, the labs and the last panel review should be
   readable with no signal, in a pharmacy, in a basement clinic.
6. **Connection setup that a non-expert can complete.** Realistically a
   QR-code pairing flow against their own server. This is the step that
   decides whether Path A is usable by anyone who did not build it.

## 5. What Path B adds on top

1. **A BAA with Anthropic, in force before any real patient data.** Not last.
2. **Per-tenant isolation enforced in the store layer**, as above.
3. **Postgres**, because SQLite's single-writer model is the wrong shape for
   concurrent tenants.
4. **Key management and encryption at rest**, per tenant.
5. **Deletion that deletes**, including backups, within a stated window.
6. **A regulatory position written down.** The current product organizes a
   person's records and generates questions for clinicians — deliberately not
   diagnosis, not treatment recommendation. That boundary is what keeps this
   out of FDA device territory, and it is enforced in code in three places
   (`gov_safety` round 8, the assistant's response schema, and
   `app/assistant/guard.py`). If a feature ever crosses it, that is a
   regulatory decision and not a product one.

---

## 6. What not to do

- **Do not build auth before choosing the path.** Single-device auth and
  multi-tenant auth are different systems, and building the wrong one is worse
  than building neither.
- **Do not move to the cloud "temporarily" to make development easier.** The
  moment real records land on a shared host, the compliance obligations attach
  retroactively.
- **Do not let the native client hold logic.** Anything a client decides is
  something that cannot be fixed without a release — a bad property for a
  safety rule.
- **Do not add a vector database.** One person's record is small. The
  assistant builds its context by reading the whole thing
  (`app/assistant/retrieval.py`), which is simpler, cheaper, and has no
  retrieval-miss failure mode. Revisit only if a single user's history
  genuinely stops fitting.
