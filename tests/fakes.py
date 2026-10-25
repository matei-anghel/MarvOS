"""Test doubles: a scripted stand-in for the Mistral gateway and a fake Windows desktop.

Nothing here is used by the app itself; tests inject these through the server's
`_mistral_from_env` / `_hands_from_env` seams.
"""
from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

from PIL import Image

from app.llm.mistral_gateway import AgentChatResult


# ── Model ─────────────────────────────────────────────────────────────────────


def tool(_name: str, **args: Any) -> dict[str, Any]:
    return {"tool": _name, "args": args}


def say(text: str) -> dict[str, Any]:
    return {"text": text}


def validate_history(messages: list[dict[str, Any]]) -> None:
    """Reject histories the real chat API would reject with a 400."""
    if not messages or messages[0]["role"] != "system":
        raise AssertionError("history must start with a system message")
    pending: set[str] = set()
    for i, m in enumerate(messages):
        role = m.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise AssertionError(f"message {i}: bad role {role!r}")
        if role == "tool":
            if m.get("tool_call_id") not in pending:
                raise AssertionError(f"message {i}: tool result for unknown call {m.get('tool_call_id')!r}")
            pending.discard(m["tool_call_id"])
            continue
        if pending:
            raise AssertionError(f"message {i}: tool calls {pending} were never answered")
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                json.loads(tc["function"]["arguments"])  # must be valid JSON
                pending.add(tc["id"])
            if not m.get("content") and not m.get("tool_calls"):
                raise AssertionError(f"message {i}: empty assistant message")
    if pending:
        raise AssertionError(f"tool calls {pending} were never answered before the next model call")


Step = dict[str, Any]
Script = list[Step] | Callable[[list[dict[str, Any]]], Step]


@dataclass
class ScriptedGateway:
    """Plays back a fixed list of steps (or asks a callback) instead of calling Mistral.

    Each chat_stream call consumes one step: either a tool call or a final text reply.
    Tool-call arguments are streamed in small fragments, like the real API does.
    """

    script: Script = field(default_factory=list)
    calls: list[list[dict[str, Any]]] = field(default_factory=list)
    agent_replies: list[str] = field(default_factory=list)
    raw_names: dict[int, str] = field(default_factory=dict)  # step index -> malformed name to send
    raw_args: dict[int, str] = field(default_factory=dict)  # step index -> malformed args to send

    def _next(self, messages: list[dict[str, Any]]) -> Step:
        if callable(self.script):
            return self.script(messages)
        idx = len(self.calls) - 1
        if idx >= len(self.script):
            return say("(script exhausted)")
        return self.script[idx]

    async def _next_async(self, messages: list[dict[str, Any]], tools: Any) -> Step:
        return self._next(messages)

    async def chat_stream(self, *, model: str, messages: list[dict[str, Any]], tools: Any, tool_choice: str):
        snapshot = json.loads(json.dumps(messages))
        self.calls.append(snapshot)
        validate_history(snapshot)
        step = await self._next_async(snapshot, tools)
        idx = len(self.calls) - 1

        if "text" in step:
            text = step["text"]
            for i in range(0, len(text), 7):
                yield {"delta": SimpleNamespace(content=text[i:i + 7], tool_calls=None)}
            return

        if "raise" in step:
            raise RuntimeError(step["raise"])

        # {"parallel": [tool(...), tool(...)]} sends several calls in one reply.
        calls = step.get("parallel") or [step]
        for n, call in enumerate(calls):
            name = self.raw_names.get(idx, call["tool"]) if n == 0 else call["tool"]
            args = self.raw_args.get(idx, json.dumps(call["args"])) if n == 0 else json.dumps(call["args"])
            first = SimpleNamespace(index=n, id=f"call{idx}_{n}", function=SimpleNamespace(name=name, arguments=""))
            yield {"delta": SimpleNamespace(content=None, tool_calls=[first])}
            for i in range(0, len(args), 5):
                frag = SimpleNamespace(index=n, id=None, function=SimpleNamespace(name=None, arguments=args[i:i + 5]))
                yield {"delta": SimpleNamespace(content=None, tool_calls=[frag])}

    # Non-chat endpoints used by individual tools.

    async def inspect_screenshot(self, *, model: str, screenshot_png_bytes: bytes, prompt: str) -> dict[str, Any]:
        assert screenshot_png_bytes.startswith(b"\x89PNG")
        return {"text": f"saw a {len(screenshot_png_bytes)}-byte screenshot"}

    async def transcribe_audio(self, *, model: str, file_content: bytes, file_name: str, language: str | None) -> str:
        return f"transcribed {len(file_content)} bytes"

    async def transcribe_realtime(self, *, model: str, audio_stream: Any, sample_rate: int = 16000):
        total = 0
        async for chunk in audio_stream:
            total += len(chunk)
            yield {"type": "delta", "text": f"[{len(chunk)}]"}
        yield {"type": "done"}

    _agents: list[dict[str, Any]] = field(default_factory=list)

    async def create_cloud_agent(self, *, name: str, description: str, instructions: str, model: str) -> str:
        return await self.create_cloud_agent_with_capabilities(
            name=name, description=description, instructions=instructions, model=model
        )

    async def create_cloud_agent_with_capabilities(self, *, name: str, description: str, instructions: str,
                                                   model: str = "", max_tokens: int = 0,
                                                   capabilities: list[str] | None = None) -> str:
        agent_id = f"ag_{len(self._agents)}"
        self._agents.append({"id": agent_id, "name": name, "description": description, "model": model,
                             "created_at": "2026-01-01T00:00:00Z"})
        return agent_id

    async def list_cloud_agents(self) -> list[dict[str, Any]]:
        return list(self._agents)

    async def chat_with_cloud_agent(self, *, agent_id: str, conversation_id: str | None, message: str) -> AgentChatResult:
        reply = self.agent_replies.pop(0) if self.agent_replies else "Noted."
        return AgentChatResult(agent_id=agent_id, conversation_id=conversation_id or "conv_1", reply_text=reply)


# ── Desktop ───────────────────────────────────────────────────────────────────


class _Rect:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left, self.top, self.right, self.bottom = left, top, right, bottom

    def width(self) -> int:
        return self.right - self.left

    def height(self) -> int:
        return self.bottom - self.top


class FakeControl:
    def __init__(self, type_name: str, name: str, rect: tuple[int, int, int, int], children=(), value: str = "",
                 toggle: int | None = None) -> None:
        self.ControlTypeName = type_name
        self.Name = name
        self.BoundingRectangle = _Rect(*rect)
        self.IsOffscreen = False
        self.IsEnabled = True
        self.HasKeyboardFocus = False
        self.AcceleratorKey = ""
        self._children = list(children)
        self._value = value
        self.toggle = toggle  # UIA ToggleState: 0 off, 1 on; None = not a toggle

    def GetChildren(self):
        return self._children

    def GetTogglePattern(self):
        return None if self.toggle is None else SimpleNamespace(ToggleState=self.toggle)

    def walk(self):
        yield self
        for c in self._children:
            yield from c.walk()

    def GetLegacyIAccessiblePattern(self):
        return SimpleNamespace(Value=self._value)


@dataclass
class FakeDesktop:
    """Records every mouse/keyboard/launch request instead of performing it."""

    events: list[tuple] = field(default_factory=list)
    windows: dict[int, str] = field(default_factory=lambda: {100: "Settings", 101: "Notepad"})
    foreground: int = 100
    root: FakeControl = field(default_factory=lambda: FakeControl("WindowControl", "Settings", (0, 0, 800, 600), [
        FakeControl("ButtonControl", "System", (10, 10, 110, 40)),
        FakeControl("ButtonControl", "Display", (10, 50, 110, 80)),
        FakeControl("EditControl", "Search box", (200, 10, 500, 40), value="old"),
        FakeControl("ButtonControl", "Night light", (200, 100, 300, 130), toggle=0),
        FakeControl("ButtonControl", "Hidden", (0, 0, 0, 0)),  # zero-size, must be skipped
    ]))

    def install(self, monkeypatch) -> None:
        d = self
        ui = types.ModuleType("uiautomation")
        ui.GetForegroundControl = lambda: d.root

        pg = types.ModuleType("pyautogui")
        pg.FAILSAFE = True
        pg.PAUSE = 0.1
        def click(x=None, y=None, button="left", clicks=1):
            d.events.append(("click", x, y, button, clicks))
            for c in d.root.walk():  # clicking a toggle flips it
                r = c.BoundingRectangle
                if c.toggle is not None and r.left <= x <= r.right and r.top <= y <= r.bottom:
                    c.toggle = 1 - c.toggle
        pg.click = click
        pg.press = lambda key: d.events.append(("press", key))
        pg.hotkey = lambda *keys: d.events.append(("hotkey",) + keys)
        pg.typewrite = lambda text, interval=0: d.events.append(("typewrite", text))
        pg.scroll = lambda n: d.events.append(("scroll", n))
        pg.moveTo = lambda x, y: d.events.append(("move", x, y))
        pg.screenshot = lambda: Image.new("RGB", (800, 600), "white")

        wg = types.ModuleType("win32gui")
        wg.GetForegroundWindow = lambda: d.foreground
        wg.GetWindowText = lambda h: d.windows.get(h, "")
        wg.IsWindowVisible = lambda h: h in d.windows
        wg.IsIconic = lambda h: False
        wg.ShowWindow = lambda h, cmd: d.events.append(("show", h, cmd))
        wg.SetForegroundWindow = lambda h: d.events.append(("focus", h))

        def enum_windows(cb, extra):
            for h in list(d.windows):
                cb(h, extra)
        wg.EnumWindows = enum_windows

        wp = types.ModuleType("win32process")
        for name, mod in {"uiautomation": ui, "pyautogui": pg, "win32gui": wg, "win32process": wp}.items():
            monkeypatch.setitem(sys.modules, name, mod)

        from app.hands import windows_mcp_adapter as adapter

        def popen(cmd, shell=False, **kw):
            d.events.append(("launch", cmd))
            new = max(d.windows) + 1
            d.windows[new] = "Windows PowerShell" if "wt" in str(cmd) else str(cmd)
            return SimpleNamespace(pid=new)

        monkeypatch.setattr(adapter, "subprocess", SimpleNamespace(Popen=popen, run=adapter.subprocess.run))
        monkeypatch.setattr(adapter.WindowsMCPHands, "_clipboard_type", lambda self, text: d.events.append(("paste", text)))
        monkeypatch.setattr(adapter.WindowsMCPHands, "_force_foreground", lambda self, h: d.events.append(("focus", h)) or True)

    def of(self, kind: str) -> list[tuple]:
        return [e for e in self.events if e[0] == kind]
