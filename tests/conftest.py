from __future__ import annotations

import http.server
import json
import os
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from fakes import FakeDesktop, ScriptedGateway

TERMINAL = {"succeeded", "failed", "canceled"}

ApprovalPolicy = Callable[[dict[str, Any]], str | None]  # None = leave it pending


def approve_all(_: dict[str, Any]) -> str:
    return "approve"


def deny_all(_: dict[str, Any]) -> str:
    return "deny"


@dataclass
class RunResult:
    run_id: str
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    watchdog_fired: bool = False

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [p for t, p in self.events if t == kind]

    @property
    def status(self) -> str:
        return self.of("run.status")[-1]["status"]

    @property
    def final_text(self) -> str:
        finals = self.of("assistant.final")
        return finals[-1]["text"] if finals else ""

    def results(self, tool_name: str | None = None) -> list[dict[str, Any]]:
        return [p["result"] for p in self.of("tool.result") if tool_name in (None, p["tool_name"])]

    def errors(self) -> list[str]:
        return [p["message"] for p in self.of("error")]


class Harness:
    """Drives the real server over HTTP + WebSocket, with the model and desktop faked."""

    def __init__(self, client: TestClient, gateway: Any, desktop: FakeDesktop | None, workdir: Path) -> None:
        self.client = client
        self.gateway = gateway
        self.desktop = desktop
        self.workdir = workdir
        self.session_id = "test-session"

    def run(self, text: str, *, approve: ApprovalPolicy = approve_all, options: dict | None = None,
            history: list | None = None, timeout: float = 60.0, on_event: Callable | None = None) -> RunResult:
        with self.client.websocket_connect(f"/ws?session_id={self.session_id}") as ws:
            time.sleep(0.05)  # let the server register the socket before events start
            resp = self.client.post("/runs", json={
                "session_id": self.session_id, "input_text": text,
                "options": options or {}, "history": history or [],
            })
            assert resp.status_code == 200, resp.text
            result = RunResult(run_id=resp.json()["run_id"])

            # A hung run would block ws.receive forever; cancel it after `timeout`.
            def fire() -> None:
                result.watchdog_fired = True
                self.client.post(f"/runs/{result.run_id}/cancel")
            timer = threading.Timer(timeout, fire)
            timer.start()
            try:
                while True:
                    msg = json.loads(ws.receive_text())
                    etype, payload = msg["type"], msg["payload"]
                    if payload.get("run_id") not in (None, result.run_id):
                        continue
                    result.events.append((etype, payload))
                    if on_event:
                        on_event(etype, payload, result)
                    if etype == "approval.requested" and (decision := approve(payload)) is not None:
                        r = self.client.post(f"/runs/{result.run_id}/approval",
                                             json={"tool_call_id": payload["tool_call_id"], "decision": decision})
                        assert r.json()["ok"], f"approval not accepted for {payload}"
                    if etype == "run.status" and payload["status"] in TERMINAL:
                        break
            finally:
                timer.cancel()
        assert not result.watchdog_fired, f"run hung; events: {[t for t, _ in result.events]}"
        return result


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir()
    return d


@pytest.fixture()
def make_harness(monkeypatch, tmp_path: Path, workdir: Path):
    """Factory: make_harness(gateway, desktop=True) -> Harness running the real app."""
    clients: list[TestClient] = []

    def make(gateway: Any = None, *, desktop: bool = True) -> Harness:
        gateway = gateway or ScriptedGateway()
        fake_desktop = FakeDesktop() if desktop else None
        monkeypatch.setenv("MARVOS_DATA_DIR", str(tmp_path / "data"))
        # TestClient's websocket_connect always sends Host: testserver.
        monkeypatch.setenv("MARVOS_ALLOWED_HOSTS", "testserver")
        import app.server as server

        monkeypatch.setattr(server, "_mistral_from_env", lambda: gateway)
        if fake_desktop:
            fake_desktop.install(monkeypatch)
            from app.hands.windows_mcp_adapter import WindowsMCPHands
            monkeypatch.setattr(server, "_hands_from_env", lambda: WindowsMCPHands())
        else:
            monkeypatch.setenv("MARVOS_HANDS", "stub")
        client = TestClient(server.create_app(), base_url="http://127.0.0.1:8000")
        client.__enter__()
        clients.append(client)
        return Harness(client, gateway, fake_desktop, workdir)

    yield make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture()
def local_site(tmp_path: Path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        "<html><head><title>Test Shop</title><script>var secret=1;</script></head><body>"
        "<h1>Welcome</h1><a href='/next.html'>Next page</a>"
        "<input id='q'><button id='go' onclick=\"document.title='clicked '+document.getElementById('q').value\">Go</button>"
        "<div style='height:3000px'></div></body></html>"
    )
    (site / "next.html").write_text("<html><head><title>Next</title></head><body>Second</body></html>")
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "codex: end-to-end run with Codex as the model")


def pytest_addoption(parser) -> None:
    parser.addoption("--codex", action="store_true", help="run end-to-end scenarios with Codex as the model")


def pytest_collection_modifyitems(config, items) -> None:
    if config.getoption("--codex") or os.environ.get("MARVOS_CODEX") == "1":
        return
    skip = pytest.mark.skip(reason="needs --codex (uses ChatGPT plan usage)")
    for item in items:
        if "codex" in item.keywords:
            item.add_marker(skip)
