from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    app_name: str = "MarvOS Agent"
    api_host: str = "127.0.0.1"
    default_text_model: str = "mistral-large-latest"
    default_vision_model: str = "mistral-small-latest"
    default_audio_model: str = "voxtral-mini-latest"
    default_agent_model: str = "mistral-medium-2505"
    max_steps_default: int = 30
    shell_timeout_s: int = 30

