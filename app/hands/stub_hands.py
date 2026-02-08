from __future__ import annotations

from typing import Any

from app.hands.hands_iface import Hands
from app.hands.screenshot import capture_png_base64


class StubHands(Hands):
    def __init__(self, note: str | None = None) -> None:
        self._note = note or "StubHands in use (no UI automation)."

    async def observe(self, *, include_screenshot: bool, ui_tree_depth: int) -> dict[str, Any]:
        screenshot = capture_png_base64() if include_screenshot else None
        return {
            "backend": "stub",
            "active_app": None,
            "active_window_title": None,
            "ui_tree": None,
            "cursor": None,
            "screenshot_base64_png": screenshot,
            "note": self._note,
        }

    async def act(self, *, actions: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "success": False,
            "performed_actions": [],
            "notes": "StubHands cannot act on the desktop.",
            "requested_actions": actions,
        }

