from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Type

from pydantic import BaseModel, Field

from app.config import AppConfig
from app.events import Event, WebSocketHub
from app.hands.hands_iface import Hands
from app.llm.mistral_gateway import MistralGateway
from app.storage.db import Storage


class _HandsObserveArgs(BaseModel):
    include_screenshot: bool = False
    ui_tree_depth: int = Field(default=5, ge=0, le=20)


class _HandsActArgs(BaseModel):
    actions: list[dict[str, Any]]


class _ShellExecArgs(BaseModel):
    command: str
    cwd: str | None = None
    timeout_s: int = Field(default=30, ge=1, le=600)


class _FsListArgs(BaseModel):
    path: str


class _FsMkdirArgs(BaseModel):
    path: str
    exist_ok: bool = True


class _FsMoveArgs(BaseModel):
    src: str
    dst: str
    overwrite: bool = False


class _FsRenameArgs(BaseModel):
    src: str
    dst: str


class _FsDeleteArgs(BaseModel):
    path: str


class _VisionInspectArgs(BaseModel):
    artifact_id: str
    prompt: str


class _AgentsCreateArgs(BaseModel):
    name: str
    description: str
    instructions: str
    model: str | None = None


class _AgentsChatArgs(BaseModel):
    agent_id: str | None = None
    conversation_id: str | None = None
    message: str


class _DictationTranscribeArgs(BaseModel):
    audio_bytes_base64: str
    file_name: str = "audio.webm"
    language: str | None = None


class _UserConfirmArgs(BaseModel):
    prompt: str


ToolHandler = Callable[[dict[str, Any], str, str], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    args_model: Type[BaseModel]
    handler: ToolHandler
    risk: str  # LOW | MEDIUM | HIGH
    requires_desktop_focus: bool = False

    @property
    def requires_approval(self) -> bool:
        return self.risk in {"HIGH", "MEDIUM"}

    def summarize(self, args: dict[str, Any]) -> str:
        if self.name == "user.confirm":
            return str(args.get("prompt") or "")[:400]
        if self.name == "shell.exec_powershell":
            return f"Run PowerShell: {args.get('command','')[:200]}"
        if self.name.startswith("fs."):
            return f"{self.name} {json.dumps(args)[:200]}"
        if self.name.startswith("hands.act"):
            return f"Desktop actions: {len(args.get('actions') or [])} step(s)"
        return f"{self.name}"

    def to_tool_schema(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        # Mistral expects OpenAI-style tool schema.
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


class ToolRegistry:
    def __init__(self, tools: dict[str, ToolDef]) -> None:
        self.tools = tools

    def to_mistral_tools(self) -> list[dict[str, Any]]:
        return [t.to_tool_schema() for t in self.tools.values()]

    async def execute(self, *, tool_name: str, args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        tool = self.tools.get(tool_name)
        if tool is None:
            return {"ok": False, "error": f"Unknown tool: {tool_name}"}
        parsed = tool.args_model(**args)
        return await tool.handler(parsed.model_dump(), session_id, run_id)


def build_default_registry(
    *,
    config: AppConfig,
    storage: Storage,
    hub: WebSocketHub,
    hands: Hands,
    mistral: MistralGateway,
) -> ToolRegistry:
    artifacts_dir = Path(os.environ.get("MARVOS_DATA_DIR", "")) / "artifacts" if os.environ.get("MARVOS_DATA_DIR") else None

    async def hands_observe(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        obs = await hands.observe(include_screenshot=args["include_screenshot"], ui_tree_depth=args["ui_tree_depth"])
        b64 = obs.pop("screenshot_base64_png", None)
        if b64:
            artifact_id = uuid.uuid4().hex
            # Persist artifact to the app's artifacts directory
            from app.storage.paths import ensure_data_dirs

            paths = ensure_data_dirs()
            path = paths["artifacts"] / f"{artifact_id}.png"
            path.write_bytes(__import__("base64").b64decode(b64))
            storage.add_artifact(
                artifact_id=artifact_id,
                session_id=session_id,
                run_id=run_id,
                kind="screenshot",
                mime="image/png",
                path=str(path),
            )
            await hub.publish(session_id, run_id, Event("artifact.created", {"run_id": run_id, "artifact_id": artifact_id, "kind": "screenshot"}))
            obs["screenshot_artifact_id"] = artifact_id
        return {"ok": True, "data": obs}

    async def hands_act(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        res = await hands.act(actions=args["actions"])
        return {"ok": True, "data": res}

    async def shell_exec(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        from app.tools.shell import exec_powershell

        return await exec_powershell(command=args["command"], cwd=args.get("cwd"), timeout_s=args["timeout_s"])

    async def fs_list(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        from app.tools.filesystem import list_dir

        return await list_dir(args["path"])

    async def fs_mkdir(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        from app.tools.filesystem import mkdir

        return await mkdir(args["path"], exist_ok=args["exist_ok"])

    async def fs_move(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        from app.tools.filesystem import move

        return await move(args["src"], args["dst"], overwrite=args["overwrite"])

    async def fs_rename(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        from app.tools.filesystem import rename

        return await rename(args["src"], args["dst"])

    async def fs_delete(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        from app.tools.filesystem import delete

        return await delete(args["path"])

    async def vision_inspect(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        artifact = storage.get_artifact(args["artifact_id"])
        if artifact is None:
            return {"ok": False, "error": "artifact not found"}
        png = Path(artifact.path).read_bytes()
        res = await mistral.inspect_screenshot(model=config.default_vision_model, screenshot_png_bytes=png, prompt=args["prompt"])
        return {"ok": True, "data": res}

    async def agents_create(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        agent_id = await mistral.create_cloud_agent(
            name=args["name"],
            description=args["description"],
            instructions=args["instructions"],
            model=args.get("model") or config.default_agent_model,
        )
        storage.put_kv("paris_agent_id", agent_id)
        return {"ok": True, "data": {"agent_id": agent_id}}

    async def agents_chat(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        agent_id = args.get("agent_id") or storage.get_kv("paris_agent_id")
        if not agent_id:
            return {"ok": False, "error": "No agent_id provided and none saved yet. Call mistral_agents.create first."}

        conv = args.get("conversation_id") or storage.get_kv("paris_conversation_id")
        res = await mistral.chat_with_cloud_agent(agent_id=agent_id, conversation_id=conv, message=args["message"])
        storage.put_kv("paris_conversation_id", res.conversation_id)
        return {"ok": True, "data": {"agent_id": res.agent_id, "conversation_id": res.conversation_id, "reply": res.reply_text}}

    async def dictation_transcribe(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        import base64

        try:
            audio = base64.b64decode(args["audio_bytes_base64"])
        except Exception:
            return {"ok": False, "error": "Invalid base64 audio."}
        text = await mistral.transcribe_audio(
            model=config.default_audio_model,
            file_content=audio,
            file_name=args.get("file_name") or "audio.webm",
            language=args.get("language"),
        )
        return {"ok": True, "data": {"text": text}}

    async def user_confirm(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        # No-op. Approval is enforced by the runner (this tool is HIGH risk).
        return {"ok": True, "data": {"confirmed": True, "prompt": args["prompt"]}}

    tools: dict[str, ToolDef] = {
        "hands.observe": ToolDef(
            name="hands.observe",
            description="Observe current desktop state (active window, UI tree, optional screenshot).",
            args_model=_HandsObserveArgs,
            handler=hands_observe,
            risk="LOW",
        ),
        "hands.act": ToolDef(
            name="hands.act",
            description="Perform GUI actions (click/type/hotkey/launch).",
            args_model=_HandsActArgs,
            handler=hands_act,
            risk="MEDIUM",
            requires_desktop_focus=True,
        ),
        "shell.exec_powershell": ToolDef(
            name="shell.exec_powershell",
            description="Execute a PowerShell command and return stdout/stderr/exit code.",
            args_model=_ShellExecArgs,
            handler=shell_exec,
            risk="HIGH",
        ),
        "fs.list": ToolDef(
            name="fs.list",
            description="List directory contents.",
            args_model=_FsListArgs,
            handler=fs_list,
            risk="LOW",
        ),
        "fs.mkdir": ToolDef(
            name="fs.mkdir",
            description="Create a directory.",
            args_model=_FsMkdirArgs,
            handler=fs_mkdir,
            risk="MEDIUM",
        ),
        "fs.move": ToolDef(
            name="fs.move",
            description="Move a file or directory.",
            args_model=_FsMoveArgs,
            handler=fs_move,
            risk="MEDIUM",
        ),
        "fs.rename": ToolDef(
            name="fs.rename",
            description="Rename a file or directory.",
            args_model=_FsRenameArgs,
            handler=fs_rename,
            risk="MEDIUM",
        ),
        "fs.delete": ToolDef(
            name="fs.delete",
            description="Delete a file or directory (HIGH RISK).",
            args_model=_FsDeleteArgs,
            handler=fs_delete,
            risk="HIGH",
        ),
        "vision.inspect_screenshot": ToolDef(
            name="vision.inspect_screenshot",
            description="Use a vision model to inspect a screenshot artifact and provide feedback.",
            args_model=_VisionInspectArgs,
            handler=vision_inspect,
            risk="LOW",
        ),
        "mistral_agents.create": ToolDef(
            name="mistral_agents.create",
            description="Create a persistent Mistral cloud agent (e.g., Paris trip planner) and save its ID.",
            args_model=_AgentsCreateArgs,
            handler=agents_create,
            risk="LOW",
        ),
        "mistral_agents.chat": ToolDef(
            name="mistral_agents.chat",
            description="Chat with a saved cloud agent (creates/continues a conversation).",
            args_model=_AgentsChatArgs,
            handler=agents_chat,
            risk="LOW",
        ),
        "dictation.transcribe": ToolDef(
            name="dictation.transcribe",
            description="Transcribe base64-encoded audio bytes using Voxtral.",
            args_model=_DictationTranscribeArgs,
            handler=dictation_transcribe,
            risk="LOW",
        ),
        "user.confirm": ToolDef(
            name="user.confirm",
            description="Ask the user to approve a sensitive action before proceeding.",
            args_model=_UserConfirmArgs,
            handler=user_confirm,
            risk="HIGH",
        ),
    }

    return ToolRegistry(tools)
