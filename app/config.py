from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    app_name: str = "MarvOS Agent"
    api_host: str = "127.0.0.1"
    # Unified model for chat, text, and vision (Mistral Large supports both)
    default_model: str = "mistral-large-latest"
    # Realtime streaming transcription (lowest latency, PCM S16LE 16kHz).
    default_audio_model_realtime: str = "voxtral-mini-transcribe-realtime-2602"
    # File-based transcription fallback (Voxtral Mini Transcribe V2).
    default_audio_model: str = "voxtral-mini-2602"
    default_agent_model: str = "devstral-latest"
    max_steps_default: int = 30
    shell_timeout_s: int = 30

