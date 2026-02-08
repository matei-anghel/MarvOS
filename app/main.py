from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request
from contextlib import closing
from pathlib import Path

import inspect
import uvicorn
import webview

from app.server import create_app


def _find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def _run_server(port: int) -> None:
    app = create_app()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )
    server = uvicorn.Server(config=config)
    server.run()


def _get_app_icon() -> str | None:
    ui_dir = Path(__file__).resolve().parent.parent / "ui"
    icon_path = ui_dir / "favicon.ico"
    if icon_path.is_file():
        return str(icon_path)
    return None


class WindowApi:
    """Methods exposed to the front-end via ``window.pywebview.api``."""

    def __init__(self) -> None:
        self._window: webview.Window | None = None
        self._maximized = False

    def set_window(self, window: webview.Window) -> None:
        self._window = window

    def minimize(self) -> None:
        if self._window:
            self._window.minimize()

    def toggle_maximize(self) -> None:
        if self._window:
            if self._maximized:
                self._window.restore()
            else:
                self._window.maximize()
            self._maximized = not self._maximized

    def close_window(self) -> None:
        if self._window:
            self._window.destroy()

    def get_bounds(self) -> dict:
        """Return current window bounds for frameless resizing (best-effort)."""
        if not self._window:
            return {"ok": False}
        w = self._window

        # Use getattr with fallbacks - get_position/get_size may not exist on all platforms
        x = int(getattr(w, "x", 0) or 0)
        y = int(getattr(w, "y", 0) or 0)
        width = int(getattr(w, "width", 0) or 0)
        height = int(getattr(w, "height", 0) or 0)

        return {
            "ok": True,
            "x": int(x),
            "y": int(y),
            "width": int(width),
            "height": int(height),
            "maximized": bool(self._maximized),
        }

    def set_bounds(self, x: int, y: int, width: int, height: int) -> bool:
        """Set window bounds for frameless resizing."""
        if not self._window:
            return False
        try:
            # Move first (needed for north/west edge resizing), then resize.
            self._window.move(int(x), int(y))
            self._window.resize(int(width), int(height))
            return True
        except Exception:
            return False


def main() -> None:
    port = int(os.environ.get("MARVOS_PORT", "0")) or _find_free_port()
    thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    thread.start()

    # Wait briefly for the server to come up to avoid a blank webview.
    deadline = time.time() + 6.0
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.1)

    url = f"http://127.0.0.1:{port}/app"

    # Restrict window dragging to explicit drag regions (see `.pywebview-drag-region` in the UI).
    # Without this, `easy_drag` can make the entire app surface draggable.
    webview.settings["DRAG_REGION_SELECTOR"] = ".pywebview-drag-region"
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False

    api = WindowApi()
    icon_path = _get_app_icon()
    window_kwargs = {
        "width": 1200,
        "height": 820,
        "min_size": (900, 560),
        "js_api": api,
        "frameless": True,
        "resizable": True,
        # Use easy-drag with explicit drag regions, so only the titlebar drags.
        "easy_drag": False,
    }
    if icon_path and "icon" in inspect.signature(webview.create_window).parameters:
        window_kwargs["icon"] = icon_path

    window = webview.create_window("MarvOS", url, **window_kwargs)
    if window is None:
        raise RuntimeError("Failed to create webview window")
    api.set_window(window)
    webview.start()


if __name__ == "__main__":
    main()
