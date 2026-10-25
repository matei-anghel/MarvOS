"""Offline check of the Codex test gateway's plumbing (no CLI calls)."""
from __future__ import annotations

from pathlib import Path

import codex_gateway
from codex_gateway import CodexGateway, _parse_step


def test_parse_step_variants() -> None:
    assert _parse_step('```json\n{"tool": "fs.list", "arguments": {"path": "C:\\\\x"}}\n```') == {
        "tool": "fs.list", "args": {"path": "C:\\x"}}
    assert _parse_step('{"final": "done"}') == {"text": "done"}
    assert _parse_step("no json here") == {"text": "no json here"}


def test_gateway_drives_a_run(make_harness, workdir: Path, monkeypatch) -> None:
    replies = iter([f'{{"tool": "fs.list", "arguments": {{"path": "{str(workdir).replace(chr(92), chr(92) * 2)}"}}}}',
                    '{"final": "listed"}'])
    prompts: list[str] = []
    monkeypatch.setattr(codex_gateway, "ask_model", lambda prompt, *a, **k: prompts.append(prompt) or next(replies))
    h = make_harness(CodexGateway())
    r = h.run("list it")
    assert r.status == "succeeded" and r.final_text == "listed", r.errors()
    assert "# Tools" in prompts[0] and "fs.list" in prompts[0]
    assert "TOOL RESULT (fs.list)" in prompts[1]
