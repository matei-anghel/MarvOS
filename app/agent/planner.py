from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.tools.registry import ToolRegistry


@dataclass(frozen=True)
class PromptPack:
    system: str
    developer: str


def build_prompts(tools: ToolRegistry) -> PromptPack:
    tool_names = ", ".join(sorted(tools.tools.keys()))
    system = (
        "You are a Windows automation assistant inside a desktop app. "
        "You can call tools to observe the desktop and act with mouse/keyboard/shell.\n\n"
        "Rules:\n"
        "- Be truthful: never claim you performed an action unless the tool result confirms it.\n"
        "- Prefer safety: if an action may change system settings, delete files, or run shell commands, "
        "call user.confirm first (the app will gate it).\n"
        "- Use at most ONE tool call per step.\n"
        "- Before using hands.act, call hands.observe at least once for grounding.\n"
    )
    developer = (
        "Available tools: "
        f"{tool_names}\n\n"
        "When you want to do something on the computer, call the appropriate tool. "
        "Tool results will be provided as tool messages.\n\n"
        "If the user asks for a demo, you can proceed step-by-step using observe/act/shell, "
        "and use vision.inspect_screenshot when screenshots are available.\n\n"
        "Return normal assistant text when you are done, otherwise emit tool calls."
    )
    return PromptPack(system=system, developer=developer)


def to_message(role: str, content: Any, *, name: str | None = None, tool_call_id: str | None = None) -> dict:
    msg: dict[str, Any] = {"role": role, "content": content}
    if name is not None:
        msg["name"] = name
    if tool_call_id is not None:
        msg["tool_call_id"] = tool_call_id
    return msg


def tool_result_message(*, tool_name: str, tool_call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "name": tool_name,
        "tool_call_id": tool_call_id,
        "content": json.dumps(result, ensure_ascii=False),
    }
