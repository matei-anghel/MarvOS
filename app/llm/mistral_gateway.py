from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from io import BytesIO
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentChatResult:
    agent_id: str
    conversation_id: str
    reply_text: str


class MistralGateway:
    def __init__(self) -> None:
        api_key = os.environ.get("MISTRAL_API_KEY")
        self._client = None
        self._init_error: str | None = None
        if not api_key:
            self._init_error = "Missing env var MISTRAL_API_KEY."
            return
        try:
            from mistralai import Mistral  # type: ignore

            self._client = Mistral(api_key=api_key)
        except Exception as e:
            self._init_error = f"Failed to initialize mistralai client: {e}"

    def _require_client(self):
        if self._client is None:
            raise RuntimeError(self._init_error or "Mistral client unavailable.")
        return self._client

    async def chat_complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str,
        parallel_tool_calls: bool,
    ) -> dict[str, Any]:
        client = self._require_client()
        resp = await client.chat.complete_async(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
        )
        msg = resp.choices[0].message

        message: dict[str, Any] = {"role": "assistant", "content": getattr(msg, "content", "")}
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ]
        return {"message": message}

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str,
    ) -> AsyncIterator[dict[str, Any]]:
        client = self._require_client()

        # Use async streaming if available in the SDK version
        stream = await client.chat.stream_async(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
        async for chunk in stream:
            delta = chunk.data.choices[0].delta
            yield {"delta": delta}

    async def inspect_screenshot(
        self,
        *,
        model: str,
        screenshot_png_bytes: bytes,
        prompt: str,
    ) -> dict[str, Any]:
        b64 = base64.b64encode(screenshot_png_bytes).decode("ascii")
        data_url = f"data:image/png;base64,{b64}"

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": data_url},
                ],
            }
        ]

        client = self._require_client()
        resp = await client.chat.complete_async(model=model, messages=messages)
        msg = resp.choices[0].message
        return {"text": getattr(msg, "content", "")}

    # ── File-based transcription (Voxtral Mini Transcribe V2) ──────────

    async def transcribe_audio(
        self,
        *,
        model: str,
        file_content: bytes,
        file_name: str,
        language: str | None,
    ) -> str:
        """Transcribe an uploaded audio file using the standard (non-realtime) endpoint."""

        def _call() -> Any:
            file_obj = BytesIO(file_content)
            kwargs: dict[str, Any] = {
                "model": model,
                "file": {"content": file_obj, "file_name": file_name},
            }
            if language:
                kwargs["language"] = language
            client = self._require_client()
            return client.audio.transcriptions.complete(**kwargs)

        resp = await asyncio.to_thread(_call)
        return str(getattr(resp, "text", "") or getattr(resp, "transcription", "") or "")

    # ── Realtime streaming transcription ──────────────────────────────────

    async def transcribe_realtime(
        self,
        *,
        model: str,
        audio_stream: AsyncIterator[bytes],
        sample_rate: int = 16000,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream PCM S16LE audio to Mistral realtime and yield text-delta / done events.

        Yields dicts: {"type": "delta", "text": "..."} or {"type": "done"}.
        """
        from mistralai.models import (  # type: ignore
            AudioFormat,
            RealtimeTranscriptionError,
            RealtimeTranscriptionSessionCreated,
            TranscriptionStreamDone,
            TranscriptionStreamTextDelta,
        )

        client = self._require_client()
        audio_format = AudioFormat(encoding="pcm_s16le", sample_rate=sample_rate)

        async for event in client.audio.realtime.transcribe_stream(
            audio_stream=audio_stream,
            model=model,
            audio_format=audio_format,
        ):
            if isinstance(event, TranscriptionStreamTextDelta):
                yield {"type": "delta", "text": getattr(event, "text", "")}
            elif isinstance(event, TranscriptionStreamDone):
                yield {"type": "done"}
                break
            elif isinstance(event, RealtimeTranscriptionError):
                log.warning("Realtime transcription error: %s", event)
                yield {"type": "error", "text": str(event)}
                break
            elif isinstance(event, RealtimeTranscriptionSessionCreated):
                log.info("Realtime transcription session created.")
            # Unknown events are silently ignored.

    async def create_cloud_agent(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        model: str,
    ) -> str:
        def _call() -> Any:
            client = self._require_client()
            return client.beta.agents.create(
                model=model,
                name=name,
                description=description,
                instructions=instructions,
            )

        resp = await asyncio.to_thread(_call)
        return str(getattr(resp, "id"))

    async def chat_with_cloud_agent(
        self,
        *,
        agent_id: str,
        conversation_id: str | None,
        message: str,
    ) -> AgentChatResult:
        def _call_start() -> Any:
            client = self._require_client()
            return client.beta.conversations.start(agent_id=agent_id, inputs=message)

        def _call_append() -> Any:
            client = self._require_client()
            return client.beta.conversations.append(conversation_id=conversation_id, inputs=message)

        resp = await asyncio.to_thread(_call_append if conversation_id else _call_start)
        conv_id = str(getattr(resp, "conversation_id", None) or getattr(resp, "id", None) or "")
        outputs = getattr(resp, "outputs", None) or []

        reply_text = ""
        if outputs:
            last = outputs[-1]
            reply_text = str(getattr(last, "content", None) or getattr(last, "text", None) or last)

        return AgentChatResult(agent_id=agent_id, conversation_id=conv_id, reply_text=reply_text)
