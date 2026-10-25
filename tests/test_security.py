from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.policy.approvals import ApprovalManager
from app.server import create_app
from app.tools.registry import _ShellExecArgs, is_read_only_powershell


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVOS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVOS_HANDS", "stub")
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as c:
        yield c


def test_same_origin_request_allowed(client) -> None:
    resp = client.post("/debug/base64", json={"data": ""}, headers={"Origin": "http://127.0.0.1:8000"})
    assert resp.status_code == 200


def test_cross_origin_request_rejected(client) -> None:
    resp = client.post("/runs", json={"input_text": "hi"}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_dns_rebinding_host_rejected(client) -> None:
    resp = client.get("/health", headers={"Host": "evil.example:8000"})
    assert resp.status_code == 403


def test_cross_origin_websocket_rejected(client) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?session_id=x", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_text()


@pytest.mark.parametrize(
    "cmd",
    [
        "Get-ChildItem C:\\Users",
        "Get-Process | Sort-Object CPU -Descending | Select-Object -First 5",
        "ls $env:USERPROFILE",
        "Get-Content notes.txt; Test-Path foo",
    ],
)
def test_read_only_powershell_allowed(cmd: str) -> None:
    assert is_read_only_powershell(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        "Format-Volume -DriveLetter D",
        "Get-ChildItem | ForEach-Object { $_.Delete() }",
        "Get-ChildItem | Move-Item -Destination D:\\",
        "ls > out.txt",
        "Get-ChildItem -Path (Remove-Item x)",
        "[IO.File]::Delete('x')",
        "tools-uninstall.exe",
        "cmd /c type x",
        "",
    ],
)
def test_mutating_powershell_needs_approval(cmd: str) -> None:
    assert not is_read_only_powershell(cmd)


def test_approved_by_user_hidden_from_model_schema() -> None:
    assert "approved_by_user" not in _ShellExecArgs.model_json_schema()["properties"]


def test_approval_resolved_before_wait_is_not_lost() -> None:
    async def scenario() -> str:
        mgr = ApprovalManager()
        mgr.register(tool_call_id="t1")
        assert mgr.resolve(tool_call_id="t1", decision="approve")
        return await mgr.wait(tool_call_id="t1")

    assert asyncio.run(scenario()) == "approve"
