from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request
from contextlib import closing

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
    webview.create_window("MarvOS Agent", url, width=1200, height=820)
    webview.start()


if __name__ == "__main__":
    main()
