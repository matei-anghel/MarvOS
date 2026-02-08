from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir


def get_data_dir() -> Path:
    configured = os.environ.get("MARVOS_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()

    # Windows: %LOCALAPPDATA%/MarvOS/Agent
    # macOS/Linux (dev): platform default
    return Path(user_data_dir(appname="MarvOS", appauthor="MarvOS")) / "agent"


def ensure_data_dirs() -> dict[str, Path]:
    base = get_data_dir()
    artifacts = base / "artifacts"
    base.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    return {"base": base, "artifacts": artifacts}

