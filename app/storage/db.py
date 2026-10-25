from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class StoredArtifact:
    id: str
    kind: str
    mime: str
    path: str


class Storage:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        # sqlite3's own context manager commits/rolls back but never closes the connection.
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                  id TEXT PRIMARY KEY,
                  created_at TEXT NOT NULL,
                  settings_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  input_text TEXT NOT NULL,
                  options_json TEXT NOT NULL,
                  message_history_json TEXT NOT NULL,
                  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_id TEXT NOT NULL,
                  run_id TEXT,
                  ts TEXT NOT NULL,
                  type TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                  id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  run_id TEXT,
                  kind TEXT NOT NULL,
                  mime TEXT NOT NULL,
                  path TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS kv (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                """
            )

    def ensure_session(self, session_id: str) -> None:
        with self._lock, self._transaction() as conn:
            row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                return
            conn.execute(
                "INSERT INTO sessions (id, created_at, settings_json) VALUES (?, ?, ?)",
                (session_id, _utc_now_iso(), json.dumps({})),
            )

    def put_kv(self, key: str, value: str) -> None:
        with self._lock, self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, _utc_now_iso()),
            )

    def get_kv(self, key: str) -> str | None:
        with self._lock, self._transaction() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            return None if row is None else str(row["value"])

    def delete_kv(self, key: str) -> None:
        """Delete a key-value pair from storage."""
        with self._lock, self._transaction() as conn:
            conn.execute("DELETE FROM kv WHERE key = ?", (key,))

    def create_run(
        self,
        *,
        run_id: str,
        session_id: str,
        input_text: str,
        status: str,
        options: dict[str, Any],
        message_history: list[dict[str, Any]],
    ) -> None:
        with self._lock, self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                  id, session_id, status, created_at, updated_at, input_text,
                  options_json, message_history_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    session_id,
                    status,
                    _utc_now_iso(),
                    _utc_now_iso(),
                    input_text,
                    json.dumps(options),
                    json.dumps(message_history),
                ),
            )

    def update_run(
        self,
        *,
        run_id: str,
        status: str | None = None,
        message_history: list[dict[str, Any]] | None = None,
    ) -> None:
        fields: list[str] = ["updated_at = ?"]
        params: list[Any] = [_utc_now_iso()]
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if message_history is not None:
            fields.append("message_history_json = ?")
            params.append(json.dumps(message_history))
        params.append(run_id)

        with self._lock, self._transaction() as conn:
            conn.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", params)

    def add_event(self, *, session_id: str, run_id: str | None, type: str, payload: dict[str, Any]) -> None:
        with self._lock, self._transaction() as conn:
            conn.execute(
                "INSERT INTO events (session_id, run_id, ts, type, payload_json) VALUES (?, ?, ?, ?, ?)",
                (session_id, run_id, _utc_now_iso(), type, json.dumps(payload)),
            )

    def add_artifact(
        self,
        *,
        artifact_id: str,
        session_id: str,
        run_id: str | None,
        kind: str,
        mime: str,
        path: str,
    ) -> None:
        with self._lock, self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (id, session_id, run_id, kind, mime, path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, session_id, run_id, kind, mime, path, _utc_now_iso()),
            )

    def get_artifact(self, artifact_id: str) -> StoredArtifact | None:
        with self._lock, self._transaction() as conn:
            row = conn.execute(
                "SELECT id, kind, mime, path FROM artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                return None
            return StoredArtifact(
                id=str(row["id"]),
                kind=str(row["kind"]),
                mime=str(row["mime"]),
                path=str(row["path"]),
            )

