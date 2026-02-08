from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Hands(ABC):
    @abstractmethod
    async def observe(self, *, include_screenshot: bool, ui_tree_depth: int) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def act(self, *, actions: list[dict[str, Any]]) -> dict[str, Any]:
        raise NotImplementedError

