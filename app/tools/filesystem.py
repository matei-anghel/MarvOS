from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any


async def list_dir(path: str) -> dict[str, Any]:
    p = Path(path).expanduser()

    def _do() -> dict[str, Any]:
        if not p.exists():
            return {"ok": False, "error": "path not found"}
        if not p.is_dir():
            return {"ok": False, "error": "not a directory"}
        items = []
        for child in p.iterdir():
            try:
                stat = child.stat()
            except Exception:
                stat = None
            items.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "is_dir": child.is_dir(),
                    "size": getattr(stat, "st_size", None),
                }
            )
        return {"ok": True, "data": {"items": items}}

    return await asyncio.to_thread(_do)


async def mkdir(path: str, *, exist_ok: bool) -> dict[str, Any]:
    p = Path(path).expanduser()

    def _do() -> dict[str, Any]:
        p.mkdir(parents=True, exist_ok=exist_ok)
        return {"ok": True, "data": {"path": str(p)}}

    return await asyncio.to_thread(_do)


async def move(src: str, dst: str, *, overwrite: bool) -> dict[str, Any]:
    s = Path(src).expanduser()
    d = Path(dst).expanduser()

    def _do() -> dict[str, Any]:
        if not s.exists():
            return {"ok": False, "error": "src not found"}
        if d.exists():
            if not overwrite:
                return {"ok": False, "error": "dst exists"}
            if d.is_dir():
                shutil.rmtree(d)
            else:
                d.unlink()
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return {"ok": True, "data": {"src": str(s), "dst": str(d)}}

    return await asyncio.to_thread(_do)


async def rename(src: str, dst: str) -> dict[str, Any]:
    s = Path(src).expanduser()
    d = Path(dst).expanduser()

    def _do() -> dict[str, Any]:
        if not s.exists():
            return {"ok": False, "error": "src not found"}
        d.parent.mkdir(parents=True, exist_ok=True)
        s.rename(d)
        return {"ok": True, "data": {"src": str(s), "dst": str(d)}}

    return await asyncio.to_thread(_do)


async def delete(path: str) -> dict[str, Any]:
    p = Path(path).expanduser()

    def _do() -> dict[str, Any]:
        if not p.exists():
            return {"ok": False, "error": "path not found"}
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return {"ok": True, "data": {"deleted": str(p)}}

    return await asyncio.to_thread(_do)

