"""Local SQLite storage for normalized records.

MVP-grade: plain SQLite, no encryption at rest. Before real PHI goes in,
follow docs/ARCHITECTURE.md section 5 (encrypt at rest, e.g. SQLCipher or
filesystem-level encryption, self-hosted only).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.schemas import LabResult, NormalizedRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source_filename TEXT,
    overall_confidence REAL NOT NULL,
    needs_review INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS labs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    collected_date TEXT,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


class RecordStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def save_all(self, records: list[NormalizedRecord]) -> None:
        with self._connect() as conn:
            for record in records:
                conn.execute(
                    "INSERT OR REPLACE INTO records "
                    "(id, kind, source_filename, overall_confidence, needs_review, created_at, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.id,
                        record.kind.value,
                        record.source_filename,
                        record.overall_confidence,
                        int(record.needs_review),
                        record.created_at.isoformat(),
                        record.model_dump_json(),
                    ),
                )

    def clear_all(self) -> None:
        """Deletes every stored record and lab result. Only called by the
        reset flow, after the archive PDF has been generated."""
        with self._connect() as conn:
            conn.execute("DELETE FROM records")
            conn.execute("DELETE FROM labs")

    # --- lab results ---------------------------------------------------------

    def save_labs(self, labs: list[LabResult]) -> None:
        if not labs:
            return
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO labs (id, name, collected_date, created_at, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (lab.id, lab.name, lab.collected_date, lab.created_at.isoformat(),
                     lab.model_dump_json())
                    for lab in labs
                ],
            )

    def list_labs(self) -> list[LabResult]:
        """Newest collection date first, so the panel sees current values at
        the top and can tell when a result is too old to rely on."""
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM labs").fetchall()
        labs = [LabResult.model_validate(json.loads(r[0])) for r in rows]
        labs.sort(key=lambda l: (l.collected_date or "", l.created_at.isoformat()), reverse=True)
        return labs

    def list_all(self, kind: str | None = None) -> list[NormalizedRecord]:
        query = "SELECT payload FROM records"
        params: tuple = ()
        if kind is not None:
            query += " WHERE kind = ?"
            params = (kind,)
        query += " ORDER BY created_at DESC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [NormalizedRecord.model_validate(json.loads(row[0])) for row in rows]
