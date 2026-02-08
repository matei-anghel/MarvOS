from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class AppConfig:
    app_name: str = "MarvOS Agent"
    api_host: str = "127.0.0.1"
    # Model for general chat queries (creative, fast)
    default_chat_model: str = "labs-mistral-small-creative"
    # Model for agent tool calls (reasoning)
    default_agent_model: str = "devstral-small-latest"
    # Model for vision/screenshot handling
    default_vision_model: str = "mistral-medium-latest"
    # Realtime streaming transcription (lowest latency, PCM S16LE 16kHz).
    default_audio_model_realtime: str = "voxtral-mini-transcribe-realtime-2602"
    # File-based transcription fallback (Voxtral Mini Transcribe V2).
    default_audio_model: str = "voxtral-mini-2602"
    max_steps_default: int = 30
    shell_timeout_s: int = 30
    # Specialized agent configuration for research tasks
    specialized_agent_model: str = "mistral-large-latest"
    specialized_agent_max_tokens: int = 4096
    specialized_agent_capabilities: List[str] = field(default_factory=lambda: ["image", "search", "code"])

