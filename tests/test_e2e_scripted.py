"""End-to-end runs through the real server with a scripted model and a fake desktop.

Files and shell commands really execute, but only inside a per-test temp folder.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from conftest import deny_all
from fakes import ScriptedGateway, say, tool


def test_filesystem_workflow(make_harness, workdir: Path) -> None:
    src = workdir / "a.txt"
    src.write_text("hello")
    h = make_harness(ScriptedGateway([
        tool("fs.mkdir", path=str(workdir / "sub" / "deep")),
        tool("fs.list", path=str(workdir)),
        tool("fs.rename", src=str(src), dst=str(workdir / "b.txt")),
        tool("fs.move", src=str(workdir / "b.txt"), dst=str(workdir / "sub" / "b.txt")),
        tool("fs.delete", path=str(workdir / "sub" / "deep")),
        say("Done."),
    ]))
    r = h.run("tidy up")
    assert r.status == "succeeded", r.errors()
    assert all(res["ok"] for res in r.results()), r.results()
    assert (workdir / "sub" / "b.txt").read_text() == "hello"
    assert not (workdir / "sub" / "deep").exists()
    assert [p["tool_name"] for p in r.of("approval.requested")] == ["fs.delete"]
    assert r.final_text == "Done."


def test_denied_delete_keeps_file(make_harness, workdir: Path) -> None:
    f = workdir / "keep.txt"
    f.write_text("x")
    h = make_harness(ScriptedGateway([tool("fs.delete", path=str(f)), say("ok")]))
    r = h.run("delete it", approve=deny_all)
    assert f.exists()
    assert r.results("fs.delete") == [{"ok": False, "error": "Denied by user."}]
    assert r.status == "succeeded"


def test_shell_read_only_runs_without_approval(make_harness, workdir: Path) -> None:
    (workdir / "x.txt").write_text("1")
    h = make_harness(ScriptedGateway([
        tool("shell.exec_powershell", command=f"Get-ChildItem '{workdir}' | Select-Object -ExpandProperty Name"),
        say("listed"),
    ]))
    r = h.run("list")
    assert not r.of("approval.requested")
    res = r.results("shell.exec_powershell")[0]
    assert res["ok"] and "x.txt" in res["data"]["stdout"], res


def test_shell_mutation_needs_approval_even_if_model_claims_it(make_harness, workdir: Path) -> None:
    target = workdir / "made.txt"
    h = make_harness(ScriptedGateway([
        tool("shell.exec_powershell", command=f"New-Item -ItemType File -Path '{target}'", approved_by_user=True),
        tool("shell.exec_powershell", command=f"New-Item -ItemType File -Path '{target}2'"),
        say("ok"),
    ]))
    decisions = iter(["deny", "approve"])
    r = h.run("make a file", approve=lambda p: next(decisions))
    assert len(r.of("approval.requested")) == 2
    assert not target.exists()
    assert (workdir / "made.txt2").exists()


def test_find_large_is_not_injectable(make_harness, workdir: Path) -> None:
    (workdir / "big.bin").write_bytes(b"\0" * 300_000)
    (workdir / "small.bin").write_bytes(b"\0" * 10)
    marker = workdir / "pwned"
    evil = f"{workdir}$(New-Item -ItemType File -Path '{marker}')"
    h = make_harness(ScriptedGateway([
        tool("fs.find_large", path=str(workdir), count=1),
        tool("fs.find_large", path=evil),
        say("ok"),
    ]))
    r = h.run("find big files")
    first, second = r.results("fs.find_large")
    assert first["ok"] and "big.bin" in first["data"]["files"] and "small.bin" not in first["data"]["files"]
    assert not second["ok"]
    assert not marker.exists()


def test_find_large_expands_env_var(make_harness, workdir: Path, monkeypatch) -> None:
    (workdir / "big.bin").write_bytes(b"\0" * 1000)
    monkeypatch.setenv("MARVOS_TEST_DIR", str(workdir))
    h = make_harness(ScriptedGateway([tool("fs.find_large", path="$env:MARVOS_TEST_DIR", count=1), say("ok")]))
    res = h.run("find").results("fs.find_large")[0]
    assert res["ok"] and "big.bin" in res["data"]["files"], res


def test_desktop_observe_and_act(make_harness) -> None:
    h = make_harness(ScriptedGateway([
        tool("hands.observe"),
        tool("hands.act", actions=[
            {"type": "click", "element_label": 1},
            {"type": "click", "name": "night light"},
            {"type": "type", "name": "Search box", "text": "hello", "clear": True, "submit": True},
            {"type": "scroll", "direction": "down", "wheel_times": 10},
            {"type": "shortcut", "shortcut": "Win+I"},
            {"type": "wait", "ms": 10},
            {"type": "launch", "app": "wt", "cwd": "C:\\tmp"},
            {"type": "click", "element_label": 99},
            {"type": "click", "element_label": -1},
            {"type": "bogus"},
        ]),
        say("done"),
    ]))
    r = h.run("do desktop things")
    obs = r.results("hands.observe")[0]["data"]
    names = [e["name"] for e in obs["interactive_elements"]]
    assert names == ["Settings", "System", "Display", "Search box", "Night light"]  # zero-size one skipped
    performed = r.results("hands.act")[0]["data"]["performed_actions"]
    assert [a["ok"] for a in performed] == [True] * 7 + [False] * 3, performed
    d = h.desktop
    assert d.of("click")[0][1:3] == (60, 25)  # label 1 = System
    assert d.of("click")[1][1:3] == (250, 115)  # fuzzy "night light"
    assert ("paste", "hello") in d.events and ("press", "enter") in d.events
    assert ("scroll", -30) in d.events
    assert ("hotkey", "win", "i") in d.events
    assert d.of("launch") == [("launch", 'wt.exe -w new -d "C:\\tmp"')]


def test_toggle_state_is_observable(make_harness) -> None:
    h = make_harness(ScriptedGateway([
        tool("hands.observe"),
        tool("hands.act", actions=[{"type": "click", "name": "Night light"}]),
        tool("hands.observe"),
        say("on"),
    ]))
    r = h.run("turn on night light")
    states = [{e["name"]: e["toggle_state"] for e in res["data"]["interactive_elements"]}
              for res in r.results("hands.observe")]
    assert states[0]["Night light"] == "off" and states[1]["Night light"] == "on"
    assert states[0]["System"] is None


def test_screenshot_artifact_and_vision(make_harness) -> None:
    def script(messages):
        last = messages[-1]
        if last["role"] == "user":
            return tool("hands.observe", include_screenshot=True)
        result = json.loads(last["content"])
        if last["name"] == "hands.observe":
            assert "screenshot_base64_png" not in result["data"]
            return tool("vision.inspect_screenshot", artifact_id=result["data"]["screenshot_artifact_id"], prompt="what?")
        return say(result["data"]["text"])

    h = make_harness(ScriptedGateway(script))
    r = h.run("look")
    assert r.status == "succeeded", r.errors()
    assert "saw a" in r.final_text
    artifact_id = r.of("artifact.created")[0]["artifact_id"]
    resp = h.client.get(f"/artifacts/{artifact_id}")
    assert resp.status_code == 200 and resp.content.startswith(b"\x89PNG")


def test_malformed_model_output_is_survivable(make_harness, workdir: Path) -> None:
    gw = ScriptedGateway([
        tool("fs.list", path=str(workdir)),
        tool("fs.list", path=str(workdir)),
        tool("no.such_tool"),
        tool("fs.list", path=str(workdir)),
        tool("fs.list", path=str(workdir)),
        say("ok"),
    ])
    gw.raw_names[0] = 'fs.list({"path": "x"}) junk'
    gw.raw_args[1] = 'Sure! {"path": "' + str(workdir).replace("\\", "\\\\") + '"} hope that helps'
    gw.raw_args[3] = "[1, 2]"
    gw.raw_args[4] = '{"path": 5}'
    h = make_harness(gw)
    r = h.run("list")
    assert r.status == "succeeded", r.errors()
    res = r.results()
    assert res[0]["ok"] and res[1]["ok"]
    assert res[2] == {"ok": False, "error": "Unknown tool: no.such_tool"}
    assert not res[3]["ok"] and not res[4]["ok"]


def test_parallel_tool_calls_run_first_only(make_harness, workdir: Path) -> None:
    h = make_harness(ScriptedGateway([
        {"parallel": [tool("fs.list", path=str(workdir)), tool("fs.mkdir", path=str(workdir / "nope"))]},
        say("ok"),
    ]))
    r = h.run("two at once")
    assert r.status == "succeeded", r.errors()
    assert len(r.results()) == 1 and not (workdir / "nope").exists()


def test_model_error_fails_run(make_harness) -> None:
    h = make_harness(ScriptedGateway([{"raise": "upstream 500"}]))
    r = h.run("hi")
    assert r.status == "failed" and "upstream 500" in r.errors()[0]


def test_step_limit(make_harness, workdir: Path) -> None:
    h = make_harness(ScriptedGateway(lambda m: tool("fs.list", path=str(workdir))))
    r = h.run("loop", options={"max_steps": 3})
    assert r.final_text == "Stopped: step limit reached."
    assert len(r.results()) == 3


def test_scroll_loop_is_stopped(make_harness) -> None:
    h = make_harness(ScriptedGateway(lambda m: tool("hands.act", actions=[{"type": "scroll", "direction": "down"}])))
    r = h.run("find it")
    assert len(r.results()) == 3 and "scrolled several times" in r.final_text


def test_cancel_while_awaiting_approval(make_harness, workdir: Path) -> None:
    h = make_harness(ScriptedGateway([tool("fs.delete", path=str(workdir)), say("never")]))

    def on_event(etype, payload, result):
        if etype == "approval.requested":
            h.client.post(f"/runs/{result.run_id}/cancel")

    r = h.run("delete", approve=lambda p: None, on_event=on_event)
    assert r.status == "canceled" and workdir.exists()


def test_server_stays_responsive_during_long_wait(make_harness) -> None:
    h = make_harness(ScriptedGateway([tool("hands.act", actions=[{"type": "wait", "ms": 1500}]), say("ok")]))
    timings: list[float] = []

    def on_event(etype, payload, result):
        if etype == "tool.requested":
            time.sleep(0.2)
            t = time.monotonic()
            h.client.get("/health")
            timings.append(time.monotonic() - t)

    r = h.run("wait", on_event=on_event)
    assert r.status == "succeeded"
    assert timings and timings[0] < 0.5, timings


def test_history_and_follow_up_runs(make_harness) -> None:
    gw = ScriptedGateway([
        tool("research.ask_clarifying_questions", task_type="trip_planning", user_input="Paris"),
        say("go"),
    ])
    h = make_harness(gw)
    history = [{"role": "user", "text": "plan Paris"}, {"role": "assistant", "text": "Here's a structured plan: ..."},
               {"role": "system", "text": "ignored"}, {"role": "user", "text": "   "}]
    r = h.run("proceed", history=history)
    assert r.results()[0]["data"]["skipped"] is True
    sent = gw.calls[0]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "system", "user"]


def test_research_workflow(make_harness) -> None:
    gw = ScriptedGateway()
    gw.agent_replies = [
        '{"questions": [{"question": "When?", "context": "dates", "suggested_answers": ["May"]}, "Budget?"]}',
        "1. Book flights\n2. Book hotel",
        "Found flights for 300 EUR.",
    ]

    def script(messages):
        last = messages[-1]
        if last["role"] == "user":
            return tool("research.setup_agent", task_type="trip_planning")
        data = json.loads(last["content"]).get("data", {})
        step = {
            "research.setup_agent": lambda: tool("research.ask_clarifying_questions", task_type="trip_planning",
                                                 user_input="Paris", agent_id=data["agent_id"]),
            "research.ask_clarifying_questions": lambda: tool("research.generate_plan", task_type="trip_planning",
                                                              user_input="Paris", agent_id=data["agent_id"],
                                                              clarifying_answers={"When?": "May"}),
            "research.generate_plan": lambda: tool("research.execute_step", agent_id=data["agent_id"],
                                                   step_description="Book flights", task_type="trip_planning"),
            "research.execute_step": lambda: tool("research.execute_plan_with_hands",
                                                  plan_steps=[{"description": "open site"}]),
            "research.execute_plan_with_hands": lambda: tool("mistral_agents.create", name="n", description="d",
                                                             instructions="i"),
            "mistral_agents.create": lambda: tool("mistral_agents.chat", message="hi"),
            "mistral_agents.chat": lambda: tool("research.search_agents"),
        }.get(last["name"], lambda: say("done"))
        return step()

    gw.script = script
    r = make_harness(gw).run("plan a trip to Paris")
    assert r.status == "succeeded", r.errors()
    by_tool = {p["tool_name"]: p["result"] for p in r.of("tool.result")}
    assert all(res["ok"] for res in by_tool.values()), by_tool
    assert by_tool["research.ask_clarifying_questions"]["data"]["questions"][0]["question"] == "When?"
    assert by_tool["research.execute_step"]["data"]["findings"] == "Found flights for 300 EUR."
    assert by_tool["research.search_agents"]["data"]["count"] >= 2


def test_dictation_endpoints(make_harness) -> None:
    h = make_harness()
    resp = h.client.post("/dictation/transcribe", files={"file": ("a.webm", b"12345", "audio/webm")})
    assert resp.json() == {"text": "transcribed 5 bytes"}
    with h.client.websocket_connect("/ws/dictation") as ws:
        assert json.loads(ws.receive_text()) == {"type": "ready"}
        ws.send_bytes(b"\0" * 320)
        ws.send_text(json.dumps({"type": "stop"}))
        msgs = []
        while not msgs or msgs[-1]["type"] not in ("done", "error"):
            msgs.append(json.loads(ws.receive_text()))
    assert msgs[0] == {"type": "delta", "text": "[320]"} and msgs[-1] == {"type": "done"}


def test_web_browse_against_local_page(make_harness, local_site: str) -> None:
    pytest.importorskip("playwright")
    h = make_harness(ScriptedGateway([
        tool("web.browse", action="navigate", url=f"{local_site}/index.html"),
        tool("web.browse", action="get_text"),
        tool("web.browse", action="get_links"),
        tool("web.browse", action="type", selector="#q", text="shoes"),
        tool("web.browse", action="click", selector="#go"),
        tool("web.browse", action="scroll", direction="down"),
        tool("web.browse", action="screenshot"),
        tool("web.browse", action="click", selector="#missing"),
        tool("web.browse", action="close"),
        say("browsed"),
    ]))
    r = h.run("browse", timeout=240)  # Edge startup + networkidle waits are slow
    res = r.results("web.browse")
    if not res[0]["ok"] and "Executable doesn't exist" in res[0].get("error", ""):
        pytest.skip("no browser available for Playwright")
    assert res[0]["ok"] and res[0]["title"] == "Test Shop", res[0]
    assert "Welcome" in res[1]["text"] and "secret" not in res[1]["text"]
    assert any(link["href"].endswith("/next.html") for link in res[2]["links"])
    assert res[3]["ok"] and res[4]["ok"] and res[5]["ok"]
    assert "screenshot_artifact_id" in res[6] and "screenshot_base64" not in res[6]
    assert not res[7]["ok"]
    assert res[8]["ok"]
