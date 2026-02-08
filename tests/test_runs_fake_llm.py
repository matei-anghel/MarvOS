from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.server import create_app


def _read_run_status(db_path: Path, run_id: str) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        return None if row is None else str(row[0])
    finally:
        conn.close()


def test_run_completes_with_fake_llm(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MARVOS_FAKE_LLM", "1")
    monkeypatch.setenv("MARVOS_DATA_DIR", str(tmp_path))

    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={"session_id": "test-session", "input_text": "hello", "mode": "chat", "options": {"max_steps": 5}},
        )
        assert resp.status_code == 200
        run_id = resp.json()["run_id"]

        db_path = tmp_path / "app.db"
        deadline = time.time() + 2.0
        status = None
        while time.time() < deadline:
            status = _read_run_status(db_path, run_id)
            if status in {"succeeded", "failed", "canceled"}:
                break
            time.sleep(0.05)

        assert status == "succeeded"


def test_dictation_transcribe_fake(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MARVOS_FAKE_LLM", "1")
    monkeypatch.setenv("MARVOS_DATA_DIR", str(tmp_path))

    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/dictation/transcribe",
            files={"file": ("audio.webm", b"dummy", "audio/webm")},
        )
        assert resp.status_code == 200
        assert "text" in resp.json()

