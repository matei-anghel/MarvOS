from __future__ import annotations

import os
import subprocess
import time
from typing import Any

from app.hands.hands_iface import Hands
from app.hands.screenshot import capture_png_base64


class WindowsUseHands(Hands):
    """
    "Hands" backend intended to be used alongside CursorTouch's Windows-Use.

    Windows-Use is currently an end-to-end automation agent package; for this MVP, we rely on the same
    Windows automation dependencies (pyautogui/uiautomation/pywin32) that Windows-Use installs.

    Install: pip install windows-use (or pip install -e ".[windows]").
    """

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("WindowsUseHands is only available on Windows.")
        try:
            import windows_use  # noqa: F401
        except Exception as e:
            raise RuntimeError('windows-use is not installed. Install with: pip install ".[windows]"') from e

    async def observe(self, *, include_screenshot: bool, ui_tree_depth: int) -> dict[str, Any]:
        title = None
        try:
            import win32gui  # type: ignore

            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
        except Exception:
            title = None

        ui_tree = None
        try:
            import uiautomation as auto  # type: ignore

            root = auto.GetForegroundControl()

            def walk(ctrl: Any, depth: int) -> Any:
                if depth <= 0 or ctrl is None:
                    return None
                kids = []
                try:
                    children = ctrl.GetChildren()
                except Exception:
                    children = []
                for c in children[:25]:
                    kids.append(
                        {
                            "name": getattr(c, "Name", None),
                            "control_type": getattr(c, "ControlTypeName", None),
                            "children": walk(c, depth - 1),
                        }
                    )
                return kids

            ui_tree = {"name": getattr(root, "Name", None), "control_type": getattr(root, "ControlTypeName", None), "children": walk(root, ui_tree_depth)}
        except Exception:
            ui_tree = None

        screenshot = capture_png_base64() if include_screenshot else None

        return {
            "active_app": None,
            "active_window_title": title,
            "ui_tree": ui_tree,
            "cursor": None,
            "screenshot_base64_png": screenshot,
        }

    async def act(self, *, actions: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            import pyautogui  # type: ignore
        except Exception as e:
            return {"success": False, "notes": f"pyautogui import failed: {e}", "performed_actions": []}

        performed: list[dict[str, Any]] = []
        for action in actions:
            kind = str(action.get("type") or "")
            if kind == "focus_window":
                # Best-effort: focus via Alt+Tab is not deterministic; we rely on other methods.
                performed.append({"type": kind, "ok": True, "note": "focus_window is best-effort in MVP."})
                continue
            if kind == "click":
                pyautogui.click(int(action["x"]), int(action["y"]))
                performed.append({"type": kind, "ok": True})
                continue
            if kind == "double_click":
                pyautogui.doubleClick(int(action["x"]), int(action["y"]))
                performed.append({"type": kind, "ok": True})
                continue
            if kind == "type":
                text = str(action.get("text") or "")
                pyautogui.write(text, interval=0.01)
                if bool(action.get("submit")):
                    pyautogui.press("enter")
                performed.append({"type": kind, "ok": True})
                continue
            if kind == "hotkey":
                keys = [str(k).lower() for k in (action.get("keys") or [])]
                if keys:
                    pyautogui.hotkey(*keys)
                performed.append({"type": kind, "ok": True})
                continue
            if kind == "wait":
                ms = int(action.get("ms") or 0)
                if ms > 0:
                    time.sleep(ms / 1000.0)
                performed.append({"type": kind, "ok": True})
                continue
            if kind == "launch":
                app = str(action.get("app") or "")
                if app:
                    subprocess.Popen(app, shell=True)
                    performed.append({"type": kind, "ok": True})
                else:
                    performed.append({"type": kind, "ok": False, "error": "missing app"})
                continue
            if kind == "drag_drop":
                pyautogui.moveTo(int(action["from_x"]), int(action["from_y"]))
                pyautogui.dragTo(int(action["to_x"]), int(action["to_y"]), duration=0.2)
                performed.append({"type": kind, "ok": True})
                continue
            performed.append({"type": kind, "ok": False, "error": "unknown action type"})

        return {"success": all(a.get("ok") for a in performed), "performed_actions": performed, "notes": None}
