from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    awaiting_approval = "awaiting_approval"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class RunOptions(BaseModel):
    max_steps: int = Field(default=30, ge=1, le=500)
    include_screenshots: bool = False
    auto_approve_medium: bool = True


class RunCreateRequest(BaseModel):
    session_id: str | None = None
    input_text: str
    mode: Literal["chat", "demo"] = "chat"
    options: RunOptions = Field(default_factory=RunOptions)


class ApprovalDecision(BaseModel):
    tool_call_id: str
    decision: Literal["approve", "deny"]
    note: str = ""


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
