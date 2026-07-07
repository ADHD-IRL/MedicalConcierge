"""Curated medication/supplement list with append-only history and named
baselines, in the same SQLite file as the raw records.

Design rules:
- History is append-only: every mutation writes an event with a full item
  snapshot. Nothing in `list_history` is ever updated or deleted.
- Items are never hard-deleted; "I no longer take this" is status=stopped,
  which preserves it for baseline comparison and the historical record.
- Ingestion may only CREATE items or log 'observed' events - it never
  overwrites fields the user may have edited on screen.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.schemas import (
    Baseline,
    BaselineDiff,
    ChangedItem,
    FieldChange,
    ItemStatus,
    ListHistoryEvent,
    MedListItem,
    NormalizedRecord,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS list_items (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    match_key TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS list_history (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS baselines (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""

EDITABLE_FIELDS = ("name", "dosage", "frequency", "notes", "status")
_COMPARED_FIELDS = ("name", "dosage", "frequency", "status", "notes")


def _match_key(kind: str, ingredient_rxcui: str | None, rxcui: str | None, name: str) -> str:
    """Identity used to decide 'is this the same medicine'. Preference order:
    ingredient RxCUI (brand and generic share it - 'Eliquis' and 'apixaban'
    must be ONE item), then the concept RxCUI, then the lowercased name."""
    if ingredient_rxcui:
        return f"{kind}:ing:{ingredient_rxcui}"
    if rxcui:
        return f"{kind}:rxcui:{rxcui}"
    return f"{kind}:name:{name.strip().lower()}"


class MedListStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    # --- reads ---------------------------------------------------------------

    def list_items(self, include_stopped: bool = True) -> list[MedListItem]:
        query = "SELECT payload FROM list_items"
        if not include_stopped:
            query += " WHERE status = 'active'"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        items = [MedListItem.model_validate(json.loads(r[0])) for r in rows]
        items.sort(key=lambda i: (i.status.value, (i.canonical_name or i.name).lower()))
        return items

    def get_item(self, item_id: str) -> MedListItem | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM list_items WHERE id = ?", (item_id,)
            ).fetchone()
        return MedListItem.model_validate(json.loads(row[0])) if row else None

    def history(self, item_id: str | None = None) -> list[ListHistoryEvent]:
        query, params = "SELECT payload FROM list_history", ()
        if item_id:
            query += " WHERE item_id = ?"
            params = (item_id,)
        query += " ORDER BY timestamp DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [ListHistoryEvent.model_validate(json.loads(r[0])) for r in rows]

    def list_baselines(self) -> list[Baseline]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM baselines ORDER BY created_at"
            ).fetchall()
        return [Baseline.model_validate(json.loads(r[0])) for r in rows]

    def get_baseline(self, baseline_id: str) -> Baseline | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM baselines WHERE id = ?", (baseline_id,)
            ).fetchone()
        return Baseline.model_validate(json.loads(row[0])) if row else None

    # --- writes --------------------------------------------------------------

    def _save_item(self, conn: sqlite3.Connection, item: MedListItem) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO list_items (id, status, match_key, payload) "
            "VALUES (?, ?, ?, ?)",
            (
                item.id,
                item.status.value,
                _match_key(item.kind.value, item.ingredient_rxcui, item.rxcui,
                           item.canonical_name or item.name),
                item.model_dump_json(),
            ),
        )

    def _log(self, conn: sqlite3.Connection, item: MedListItem, action: str, detail: str) -> None:
        event = ListHistoryEvent(item_id=item.id, action=action, detail=detail, item_snapshot=item)
        conn.execute(
            "INSERT INTO list_history (id, item_id, timestamp, payload) VALUES (?, ?, ?, ?)",
            (event.id, event.item_id, event.timestamp.isoformat(), event.model_dump_json()),
        )

    def add_item(self, item: MedListItem, detail: str = "Added manually") -> MedListItem:
        with self._connect() as conn:
            self._save_item(conn, item)
            self._log(conn, item, "created", detail)
        self._ensure_original_baseline()
        return item

    def update_item(self, item_id: str, updates: dict) -> MedListItem:
        item = self.get_item(item_id)
        if item is None:
            raise KeyError(item_id)

        changes: list[str] = []
        action = "updated"
        for field in EDITABLE_FIELDS:
            if field not in updates or updates[field] is None:
                continue
            new_value = updates[field]
            old_value = getattr(item, field)
            old_str = old_value.value if isinstance(old_value, ItemStatus) else old_value
            if str(new_value) == (old_str or ""):
                continue
            if field == "status":
                new_status = ItemStatus(new_value)
                action = "stopped" if new_status == ItemStatus.stopped else "reactivated"
                item.status = new_status
            else:
                setattr(item, field, new_value or None)
            changes.append(f"{field}: '{old_str or ''}' -> '{new_value}'")

        if not changes:
            return item

        item.updated_at = datetime.utcnow()
        with self._connect() as conn:
            self._save_item(conn, item)
            self._log(conn, item, action, "; ".join(changes))
        return item

    def sync_from_records(self, records: list[NormalizedRecord]) -> list[MedListItem]:
        """Called after every ingest. Creates list items for medicines/
        supplements not yet on the list; for ones already present, appends an
        'observed' history event (never overwriting user-edited fields)."""

        existing = {
            _match_key(i.kind.value, i.ingredient_rxcui, i.rxcui, i.canonical_name or i.name): i
            for i in self.list_items()
        }
        created: list[MedListItem] = []
        with self._connect() as conn:
            for record in records:
                display = record.normalization.canonical_name or record.extracted.name_as_written
                key = _match_key(
                    record.kind.value,
                    record.normalization.ingredient_rxcui,
                    record.normalization.rxcui,
                    display,
                )
                if key in existing:
                    current = existing[key]
                    seen = []
                    if record.extracted.dosage and record.extracted.dosage != current.dosage:
                        seen.append(f"dose read as '{record.extracted.dosage}' (list says '{current.dosage or '?'}')")
                    if record.extracted.frequency and record.extracted.frequency != current.frequency:
                        seen.append(f"frequency read as '{record.extracted.frequency}' (list says '{current.frequency or '?'}')")
                    detail = f"Seen again in '{record.source_filename}'"
                    if seen:
                        detail += " with differences - " + "; ".join(seen)
                    self._log(conn, current, "observed", detail)
                    continue
                item = MedListItem(
                    kind=record.kind,
                    name=record.extracted.name_as_written,
                    canonical_name=record.normalization.canonical_name,
                    rxcui=record.normalization.rxcui,
                    ingredient_rxcui=record.normalization.ingredient_rxcui,
                    ingredient_name=record.normalization.ingredient_name,
                    dosage=record.extracted.dosage,
                    frequency=record.extracted.frequency,
                    source_record_id=record.id,
                )
                self._save_item(conn, item)
                self._log(conn, item, "created", f"Added from document '{record.source_filename}'")
                existing[key] = item
                created.append(item)
        if created:
            self._ensure_original_baseline()
        return created

    def clear_all(self) -> None:
        """Deletes the entire list, its history, and all baselines. Only
        called by the reset flow, after the archive PDF has been generated."""
        with self._connect() as conn:
            conn.execute("DELETE FROM list_items")
            conn.execute("DELETE FROM list_history")
            conn.execute("DELETE FROM baselines")

    # --- baselines -----------------------------------------------------------

    def create_baseline(self, name: str) -> Baseline:
        baseline = Baseline(name=name, items=self.list_items())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO baselines (id, created_at, payload) VALUES (?, ?, ?)",
                (baseline.id, baseline.created_at.isoformat(), baseline.model_dump_json()),
            )
        return baseline

    def _ensure_original_baseline(self) -> None:
        if not self.list_baselines():
            self.create_baseline("Original baseline")

    def compare_to_baseline(self, baseline_id: str) -> BaselineDiff:
        baseline = self.get_baseline(baseline_id)
        if baseline is None:
            raise KeyError(baseline_id)

        current = {i.id: i for i in self.list_items()}
        base = {i.id: i for i in baseline.items}

        # Only active newcomers count as "added"; an item that was both added
        # and stopped since the baseline is a net no-op for comparison.
        added = [
            i for item_id, i in current.items()
            if item_id not in base and i.status == ItemStatus.active
        ]
        stopped: list[MedListItem] = []
        changed: list[ChangedItem] = []
        unchanged = 0

        for item_id, base_item in base.items():
            now = current.get(item_id)
            if now is None:
                continue  # items are never hard-deleted; defensive only
            if now.status == ItemStatus.stopped and base_item.status == ItemStatus.active:
                stopped.append(now)
                continue
            field_changes = [
                FieldChange(
                    field=f,
                    before=_as_str(getattr(base_item, f)),
                    after=_as_str(getattr(now, f)),
                )
                for f in _COMPARED_FIELDS
                if _as_str(getattr(base_item, f)) != _as_str(getattr(now, f))
            ]
            if field_changes:
                changed.append(ChangedItem(item=now, changes=field_changes))
            else:
                unchanged += 1

        return BaselineDiff(
            baseline_id=baseline.id,
            baseline_name=baseline.name,
            baseline_created_at=baseline.created_at,
            added=added,
            stopped=stopped,
            changed=changed,
            unchanged_count=unchanged,
        )


def _as_str(value) -> str | None:
    if value is None:
        return None
    return value.value if isinstance(value, ItemStatus) else str(value)


def item_to_record(item: MedListItem) -> NormalizedRecord:
    """Adapts a curated list item to the NormalizedRecord shape the screening
    engine and PDF builder consume. Curated items are user-confirmed, so they
    carry full confidence."""

    from app.schemas import ExtractedItem, RxNormMatch, SourceType

    return NormalizedRecord(
        id=item.id,
        kind=item.kind,
        extracted=ExtractedItem(
            raw_text=item.notes or item.name,
            name_as_written=item.name,
            dosage=item.dosage,
            frequency=item.frequency,
            source_type=SourceType.other,
            extraction_confidence=1.0,
            ambiguities=[],
        ),
        normalization=RxNormMatch(
            rxcui=item.rxcui,
            canonical_name=item.canonical_name,
            ingredient_rxcui=item.ingredient_rxcui,
            ingredient_name=item.ingredient_name,
            match_score=100.0 if item.canonical_name else 0.0,
            normalization_confidence=1.0 if item.canonical_name else 0.0,
            source="med_list",
        ),
        overall_confidence=1.0,
        needs_review=False,
        source_filename="medication list",
        created_at=item.created_at,
    )
