from __future__ import annotations

import os
import re
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
            "backend": "windows-use",
            "active_app": None,
            "active_window_title": title,
            "ui_tree": ui_tree,
            "cursor": None,
            "screenshot_base64_png": screenshot,
        }

    def _parse_hotkey(self, action: dict[str, Any]) -> list[str]:
        keys = action.get("keys")
        if isinstance(keys, list) and keys:
            return [str(k).strip().lower() for k in keys if str(k).strip()]

        value = action.get("value")
        if not isinstance(value, str) or not value.strip():
            return []

        parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", value.strip()) if p.strip()]
        normalized: list[str] = []
        for p in parts:
            if p in {"control", "ctl"}:
                p = "ctrl"
            elif p in {"windows", "win"}:
                # pyautogui supports winleft/winright; winleft is the safest default.
                p = "winleft"
            elif p == "esc":
                p = "escape"
            normalized.append(p)
        return normalized

    def _uia_find_descendant_by_name(self, root: Any, name: str, *, max_depth: int = 5, max_nodes: int = 600) -> Any | None:
        want = (name or "").strip().lower()
        if not want:
            return None

        queue: list[tuple[Any, int]] = [(root, 0)]
        visited = 0
        while queue and visited < max_nodes:
            ctrl, depth = queue.pop(0)
            visited += 1
            if ctrl is None:
                continue
            try:
                ctrl_name = (getattr(ctrl, "Name", None) or "").strip()
            except Exception:
                ctrl_name = ""

            if ctrl_name:
                n = ctrl_name.lower()
                if n == want or want in n:
                    return ctrl

            if depth >= max_depth:
                continue
            try:
                children = ctrl.GetChildren() or []
            except Exception:
                children = []
            for c in children[:50]:
                queue.append((c, depth + 1))
        return None

    def _uia_find_by_path(self, path: list[str]) -> Any | None:
        try:
            import uiautomation as auto  # type: ignore
        except Exception:
            return None

        root = auto.GetForegroundControl()
        if root is None:
            return None

        segments = [str(s).strip() for s in path if str(s).strip()]
        if not segments:
            return None

        try:
            root_name = (getattr(root, "Name", None) or "").strip().lower()
        except Exception:
            root_name = ""

        if root_name and segments and root_name == segments[0].lower():
            segments = segments[1:]

        ctrl: Any = root
        for seg in segments:
            found = self._uia_find_descendant_by_name(ctrl, seg)
            if found is None:
                return None
            ctrl = found
        return ctrl

    def _uia_click_control_center(self, ctrl: Any) -> tuple[bool, str | None]:
        try:
            rect = getattr(ctrl, "BoundingRectangle", None)
            if rect is None:
                return False, "missing BoundingRectangle"
            left = int(getattr(rect, "left", getattr(rect, "Left", 0)))
            top = int(getattr(rect, "top", getattr(rect, "Top", 0)))
            right = int(getattr(rect, "right", getattr(rect, "Right", 0)))
            bottom = int(getattr(rect, "bottom", getattr(rect, "Bottom", 0)))
            if right <= left or bottom <= top:
                return False, f"invalid rect {left},{top},{right},{bottom}"

            x = int((left + right) / 2)
            y = int((top + bottom) / 2)

            import pyautogui  # type: ignore

            try:
                ctrl.SetFocus()
            except Exception:
                pass
            pyautogui.click(x, y)
            return True, None
        except Exception as e:
            return False, str(e)

    async def act(self, *, actions: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            import pyautogui  # type: ignore
        except Exception as e:
            return {"success": False, "notes": f"pyautogui import failed: {e}", "performed_actions": []}

        performed: list[dict[str, Any]] = []
        for action in actions:
            # Some models emit shorthand actions like {click: "Night Light"}.
            kind = str(action.get("type") or "")
            if kind == "focus_window":
                # Best-effort: focus via Alt+Tab is not deterministic; we rely on other methods.
                performed.append({"type": kind, "ok": True, "note": "focus_window is best-effort in MVP."})
                continue
            if kind == "click":
                # Supported forms:
                # - {type:"click", x:..., y:...}
                # - {type:"click", path:[...]}  (best-effort via UIAutomation)
                # - {type:"click", name:"..."} or {type:"click", query:"..."}
                try:
                    if "x" in action and "y" in action:
                        pyautogui.click(int(action["x"]), int(action["y"]))
                        performed.append({"type": kind, "ok": True})
                        continue

                    ctrl = None
                    if isinstance(action.get("path"), list):
                        ctrl = self._uia_find_by_path([str(s) for s in (action.get("path") or [])])
                    if ctrl is None:
                        q = action.get("name") or action.get("query")
                        if isinstance(q, str) and q.strip():
                            ctrl = self._uia_find_by_path([q.strip()])

                    if ctrl is None:
                        performed.append({"type": kind, "ok": False, "error": "click requires x/y or resolvable path/name"})
                        continue

                    ok, err = self._uia_click_control_center(ctrl)
                    performed.append({"type": kind, "ok": ok, **({"error": err} if err else {})})
                except Exception as e:
                    performed.append({"type": kind, "ok": False, "error": str(e)})
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
                try:
                    keys = self._parse_hotkey(action)
                    if not keys:
                        performed.append({"type": kind, "ok": False, "error": "hotkey requires keys[] or value"})
                        continue
                    pyautogui.hotkey(*keys)
                    performed.append({"type": kind, "ok": True, "keys": keys})
                except Exception as e:
                    performed.append({"type": kind, "ok": False, "error": str(e)})
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

            # Shorthand: {click: "Some control"}
            if not kind and isinstance(action.get("click"), str) and action.get("click"):
                q = str(action.get("click"))
                ctrl = self._uia_find_by_path([q])
                if ctrl is None:
                    performed[-1] = {"type": "click", "ok": False, "error": f"could not resolve click target: {q}"}
                else:
                    ok, err = self._uia_click_control_center(ctrl)
                    performed[-1] = {"type": "click", "ok": ok, **({"error": err} if err else {})}

        return {"success": all(a.get("ok") for a in performed), "performed_actions": performed, "notes": None}
