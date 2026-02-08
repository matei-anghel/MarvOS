from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class _Pending:
    future: asyncio.Future[str]


class ApprovalManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending: dict[str, _Pending] = {}

    async def wait(self, *, tool_call_id: str) -> str:
        async with self._lock:
            pending = self._pending.get(tool_call_id)
            if pending is None:
                pending = _Pending(future=asyncio.get_event_loop().create_future())
                self._pending[tool_call_id] = pending
        return await pending.future

    def resolve(self, *, tool_call_id: str, decision: str, note: str = "") -> bool:
        if decision not in {"approve", "deny"}:
            return False

        pending = self._pending.get(tool_call_id)
        if pending is None:
            return False
        if pending.future.done():
            return False
        pending.future.set_result(decision)
        self._pending.pop(tool_call_id, None)
        return True

