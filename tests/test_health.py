from __future__ import annotations

from fastapi.testclient import TestClient

from app.server import create_app


def test_health() -> None:
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
