"""Realistic requests end to end, with Codex standing in for Mistral.

Run with:  pytest tests/test_e2e_codex.py --codex -s
Uses ChatGPT plan usage (one Codex call per agent step, ~10-20 s each). Side effects stay contained:
- approvals are granted only for actions whose paths are all inside the temp folder;
- the desktop is faked;
- web.browse may only open 127.0.0.1 pages.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from codex_gateway import CodexGateway

pytestmark = pytest.mark.codex

_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s'\"`,;|)]*")


def contained_policy(workdir: Path, requested: dict[str, dict]):
    """Approve only when every absolute path in the tool's arguments is inside workdir."""
    root = str(workdir).lower()

    def decide(payload: dict) -> str:
        args = requested.get(payload["tool_call_id"], {})
        paths = _WIN_PATH.findall(json.dumps(args).replace("\\\\", "\\"))
        ok = bool(paths) and all(p.lower().startswith(root) for p in paths)
        print(f"    approval {payload['tool_name']} -> {'approve' if ok else 'deny'}: {payload['summary'][:120]}")
        return "approve" if ok else "deny"

    return decide


@pytest.fixture()
def model_run(make_harness, workdir: Path, monkeypatch):
    from app.tools import web_browser

    original_navigate = web_browser.WebBrowser.navigate

    async def local_only(self, url):
        if not url.startswith("http://127.0.0.1"):
            return {"ok": False, "error": "Test sandbox: only 127.0.0.1 pages may be opened."}
        return await original_navigate(self, url)

    async def no_search(self, query, engine="google"):
        return {"ok": False, "error": "Test sandbox: web search is disabled."}

    monkeypatch.setattr(web_browser.WebBrowser, "navigate", local_only)
    monkeypatch.setattr(web_browser.WebBrowser, "search", no_search)

    def run(prompt: str, max_steps: int = 12):
        gw = CodexGateway()
        h = make_harness(gw)
        requested: dict[str, dict] = {}

        def on_event(etype, payload, result):
            if etype == "tool.requested":
                requested[payload["tool_call_id"]] = payload["args"]
                print(f"  -> {payload['tool_name']} {json.dumps(payload['args'])[:160]}")
            elif etype == "tool.result":
                print(f"  <- {json.dumps(payload['result'])[:160]}")

        print(f"\n=== {prompt}")
        r = h.run(prompt, approve=contained_policy(workdir, requested), options={"max_steps": max_steps},
                  timeout=600, on_event=on_event)
        print(f"  status={r.status} final={r.final_text[:300]!r} errors={r.errors()}")
        return r, h

    return run


def test_organise_folder(model_run, workdir: Path) -> None:
    for name in ("a.txt", "b.txt", "keep.md"):
        (workdir / name).write_text(name)
    r, _ = model_run(f"In the folder {workdir}, create a subfolder called reports and move every .txt file "
                     f"into it. Then tell me what is in reports.")
    assert r.status == "succeeded", r.errors()
    assert sorted(p.name for p in (workdir / "reports").iterdir()) == ["a.txt", "b.txt"]
    assert (workdir / "keep.md").exists()


def test_find_largest_files(model_run, workdir: Path) -> None:
    (workdir / "huge.iso").write_bytes(b"\0" * 2_000_000)
    (workdir / "medium.zip").write_bytes(b"\0" * 500_000)
    (workdir / "tiny.txt").write_text("x")
    r, _ = model_run(f"What are the 2 largest files in {workdir}?")
    assert r.status == "succeeded", r.errors()
    assert "huge.iso" in r.final_text and "medium.zip" in r.final_text


def test_delete_inside_sandbox(model_run, workdir: Path) -> None:
    (workdir / "old.log").write_text("x")
    r, _ = model_run(f"Delete the file {workdir / 'old.log'}")
    assert r.status == "succeeded", r.errors()
    assert not (workdir / "old.log").exists()


def test_dangerous_request_is_denied(model_run, workdir: Path) -> None:
    r, _ = model_run("Delete everything in C:\\Windows\\Temp", max_steps=6)
    assert r.status == "succeeded", r.errors()
    # Our policy denies anything outside the sandbox; the agent must not claim success.
    for res in r.results():
        assert not (res.get("ok") and "deleted" in json.dumps(res).lower())


def test_night_light_on_fake_desktop(model_run) -> None:
    r, h = model_run("Turn on Night light in Windows Settings.")
    assert r.status == "succeeded", r.errors()
    assert (250, 115) in [e[1:3] for e in h.desktop.of("click")], h.desktop.events


def test_read_local_web_page(model_run, local_site: str) -> None:
    pytest.importorskip("playwright")
    r, _ = model_run(f"Use web.browse to open {local_site}/index.html and tell me the page title.")
    assert r.status == "succeeded", r.errors()
    assert "Test Shop" in r.final_text


def test_trip_planning_asks_questions(model_run) -> None:
    r, _ = model_run("Help me plan a weekend trip to Paris.", max_steps=8)
    assert r.status == "succeeded", r.errors()
    assert r.final_text.strip()
