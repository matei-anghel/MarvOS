"""Test-only model gateway that asks Codex (via the `codex` CLI) instead of Mistral.

Codex runs read-only in an empty temp folder and uses the CLI's ChatGPT login. It
refuses to run with OPENAI_API_KEY set so tests never bill the API.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.llm.mistral_gateway import AgentChatResult
from fakes import ScriptedGateway, Step, say

_PROTOCOL = (
    "You are the decision engine of a desktop agent under test. Read the agent's instructions, "
    "the tool list and the conversation, then decide the agent's NEXT move. "
    "Reply with exactly one JSON object and nothing else: either "
    '{"tool": "<tool name>", "arguments": {...}} to call one tool, or '
    '{"final": "<message to the user>"} when the task is done or you need to ask the user something.'
)


def _render(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
    parts = ["# Agent instructions\n" + "\n\n".join(m["content"] for m in messages if m["role"] == "system")]
    parts.append("# Tools\n" + "\n".join(
        f"- {t['function']['name']}: {t['function']['description']}\n  parameters: "
        f"{json.dumps(t['function']['parameters'].get('properties', {}))}"
        for t in tools or []
    ))
    convo = []
    for m in messages:
        if m["role"] == "user":
            convo.append(f"USER: {m['content']}")
        elif m["role"] == "assistant":
            calls = [f"{tc['function']['name']}({tc['function']['arguments']})" for tc in m.get("tool_calls") or []]
            convo.append("AGENT: " + (m.get("content") or "") + (f" [called {', '.join(calls)}]" if calls else ""))
        elif m["role"] == "tool":
            convo.append(f"TOOL RESULT ({m['name']}): {m['content'][:6000]}")
    parts.append("# Conversation so far\n" + "\n".join(convo))
    parts.append("# Your next move (one JSON object only)")
    return "\n\n".join(parts)


def ask_model(prompt: str, system: str = _PROTOCOL, timeout: float = 300) -> str:
    if os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is set; refusing so tests don't use API billing.")
    exe = shutil.which("codex")
    if not exe:
        raise RuntimeError("codex CLI not found")
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    # Codex is an agent; run it read-only in an empty folder and ask for a plain JSON answer.
    with tempfile.TemporaryDirectory() as empty:
        out = Path(empty) / "last.txt"
        proc = subprocess.run(
            [exe, "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "-C", empty,
             "-c", 'model_reasoning_effort="low"', "--color", "never", "-o", str(out), "-"],
            input=f"{system}\nDo not run commands or use tools; answer directly.\n\n{prompt}",
            capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env,
        )
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"codex CLI failed: {(proc.stderr or proc.stdout)[-500:]}")
        return out.read_text(encoding="utf-8")


def _parse_step(text: str) -> Step:
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            obj = json.loads(match.group(0))
            if "tool" in obj:
                return {"tool": str(obj["tool"]), "args": obj.get("arguments") or {}}
            if "final" in obj:
                return say(str(obj["final"]))
        except json.JSONDecodeError:
            pass
    return say(text.strip() or "(empty reply)")


@dataclass
class CodexGateway(ScriptedGateway):
    transcript: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.transcript = []

    async def _next_async(self, messages: list[dict[str, Any]], tools: Any) -> Step:
        reply = await asyncio.to_thread(ask_model, _render(messages, tools))
        step = _parse_step(reply)
        self.transcript.append(json.dumps(step)[:300])
        return step

    async def chat_with_cloud_agent(self, *, agent_id: str, conversation_id: str | None, message: str) -> AgentChatResult:
        reply = await asyncio.to_thread(
            ask_model, message, "You are a concise travel and research assistant. You cannot browse; give your best answer."
        )
        return AgentChatResult(agent_id=agent_id, conversation_id=conversation_id or "conv_1", reply_text=reply)
