from __future__ import annotations

from typing import Any


class FakeMistralGateway:
    async def chat_complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str,
        parallel_tool_calls: bool,
    ) -> dict[str, Any]:
        # Always respond with a short final message (no tool calls).
        user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
        content = f"(FAKE LLM) You said: {user.get('content') if user else ''}"
        return {"message": {"role": "assistant", "content": content}}

    async def inspect_screenshot(
        self,
        *,
        model: str,
        screenshot_png_bytes: bytes,
        prompt: str,
    ) -> dict[str, Any]:
        return {"text": "(FAKE VISION) Screenshot received."}

    async def transcribe_audio(
        self,
        *,
        model: str,
        file_content: bytes,
        file_name: str,
        language: str | None,
    ) -> str:
        return "(FAKE TRANSCRIPT)"

    async def create_cloud_agent(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        model: str,
    ) -> str:
        return "fake-agent-id"

    async def chat_with_cloud_agent(
        self,
        *,
        agent_id: str,
        conversation_id: str | None,
        message: str,
    ):
        return type(
            "FakeAgentChatResult",
            (),
            {"agent_id": agent_id, "conversation_id": conversation_id or "fake-conv", "reply_text": "(FAKE AGENT)"},
        )()

