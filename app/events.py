from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from app.storage.db import Storage


@dataclass(frozen=True)
class Event:
    type: str
    payload: dict[str, Any]


class WebSocketHub:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._lock = asyncio.Lock()
        self._sockets: dict[str, set[WebSocket]] = {}

    async def register(self, session_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._sockets.setdefault(session_id, set()).add(ws)

    async def unregister(self, session_id: str, ws: WebSocket) -> None:
        async with self._lock:
            sockets = self._sockets.get(session_id)
            if not sockets:
                return
            sockets.discard(ws)
            if not sockets:
                self._sockets.pop(session_id, None)

    async def publish(self, session_id: str, run_id: str | None, event: Event) -> None:
        await asyncio.to_thread(
            self._storage.add_event,
            session_id=session_id,
            run_id=run_id,
            type=event.type,
            payload=event.payload,
        )
        data = json.dumps({"type": event.type, "payload": event.payload})
        async with self._lock:
            sockets = list(self._sockets.get(session_id, set()))
        for ws in sockets:
            try:
                await ws.send_text(data)
            except Exception:
                # Best-effort delivery; the WS handler cleans up on disconnect.
                pass

