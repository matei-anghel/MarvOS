from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class _Pending:
    future: asyncio.Future[str]


class ApprovalManager:
    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}

    def register(self, *, tool_call_id: str) -> None:
        """Create the pending slot before the UI is told to ask, so a fast reply is not lost."""
        if tool_call_id not in self._pending:
            self._pending[tool_call_id] = _Pending(future=asyncio.get_running_loop().create_future())

    async def wait(self, *, tool_call_id: str) -> str:
        self.register(tool_call_id=tool_call_id)
        try:
            return await self._pending[tool_call_id].future
        finally:
            self._pending.pop(tool_call_id, None)

    def resolve(self, *, tool_call_id: str, decision: str, note: str = "") -> bool:
        if decision not in {"approve", "deny"}:
            return False

        pending = self._pending.get(tool_call_id)
        if pending is None:
            return False
        if pending.future.done():
            return False
        pending.future.set_result(decision)
        return True
