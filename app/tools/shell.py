from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Any


_DENY_SUBSTRINGS = [
    "Remove-Item",
    "Format-Volume",
    "Clear-Disk",
    "Set-ItemProperty",
    "New-ItemProperty",
    "reg add",
    "reg delete",
]


async def exec_powershell(*, command: str, cwd: str | None, timeout_s: int, approved_by_user: bool = False) -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": False, "error": "PowerShell execution is only supported on Windows in this MVP."}

    if not approved_by_user:
        for bad in _DENY_SUBSTRINGS:
            if bad.lower() in command.lower():
                return {"ok": False, "error": f"Command blocked by policy (matched: {bad})."}

    def _run() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )

    try:
        proc = await asyncio.to_thread(_run)
        return {
            "ok": True,
            "data": {
                "exit_code": proc.returncode,
                "stdout": proc.stdout.decode(errors="replace")[:20000],
                "stderr": proc.stderr.decode(errors="replace")[:20000],
            },
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timeout expired."}
    except Exception as e:
        return {"ok": False, "error": str(e)}

