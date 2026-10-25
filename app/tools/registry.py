from __future__ import annotations

import base64
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Type

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from app.config import AppConfig
from app.events import Event, WebSocketHub
from app.hands.hands_iface import Hands
from app.llm.mistral_gateway import MistralGateway
from app.storage.db import Storage


class _HandsObserveArgs(BaseModel):
    include_screenshot: bool = False  # Screenshots are expensive - use only when needed
    ui_tree_depth: int = Field(default=10, ge=0, le=20)


class _HandsActArgs(BaseModel):
    actions: list[dict[str, Any]]


class _ShellExecArgs(BaseModel):
    command: str
    cwd: str | None = None
    timeout_s: int = Field(default=30, ge=1, le=600)
    # Set only by the runner after the user approves; hidden from the model's schema.
    approved_by_user: SkipJsonSchema[bool] = False


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


class _FsFindLargeArgs(BaseModel):
    path: str = Field(default="C:\\", description="Root path to search from (e.g., '$env:USERPROFILE' for home folder)")
    count: int = Field(default=10, ge=1, le=50, description="Number of largest files to return")


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


class _ResearchSearchAgentsArgs(BaseModel):
    """No arguments needed - lists all available agents."""
    pass


class _ResearchSetupAgentArgs(BaseModel):
    task_type: str = Field(description="Type of task: trip_planning, product_research, event_planning, or general_research")


class _WebBrowseArgs(BaseModel):
    action: str = Field(description="Action to perform: start, navigate, search, click, type, get_text, screenshot, scroll, get_links, close")
    url: str | None = Field(default=None, description="URL to navigate to (for navigate action)")
    query: str | None = Field(default=None, description="Search query (for search action)")
    selector: str | None = Field(default=None, description="CSS selector for click/type actions")
    text: str | None = Field(default=None, description="Text to type (for type action)")
    submit: bool = Field(default=False, description="Whether to press Enter after typing")
    engine: str = Field(default="google", description="Search engine: google or bing")
    direction: str = Field(default="down", description="Scroll direction: up or down")


class _ResearchAskQuestionsArgs(BaseModel):
    task_type: str
    user_input: str
    agent_id: str | None = None


class _ResearchGeneratePlanArgs(BaseModel):
    task_type: str
    user_input: str
    agent_id: str
    clarifying_answers: dict[str, str] = Field(default_factory=dict)


class _ResearchExecuteStepArgs(BaseModel):
    agent_id: str
    step_description: str
    task_type: str


class _ResearchExecutePlanWithHandsArgs(BaseModel):
    plan_steps: list[dict[str, Any]]
    stop_before_purchase: bool = True


ToolHandler = Callable[[dict[str, Any], str, str], Awaitable[dict[str, Any]]]


# Commands that only read state. Matched against the first word of every pipeline
# segment, so arguments cannot smuggle in other commands.
_READ_ONLY_PS_COMMANDS = {
    "select-object", "select", "where-object", "where", "?", "sort-object", "sort",
    "measure-object", "measure", "group-object", "group",
    "format-table", "ft", "format-list", "fl", "format-wide", "fw", "out-string",
    "write-host", "write-output", "echo", "convertto-json", "convertto-csv",
    "test-path", "resolve-path", "split-path", "join-path",
    "ls", "dir", "gci", "gc", "cat", "type", "gi", "gp", "gps", "ps", "gsv", "pwd",
    "hostname", "whoami",
}
# Constructs that can run arbitrary code inside an otherwise read-only command:
# script blocks, subexpressions/grouping, redirection, call operators, .NET
# static calls, method invocations, and escape characters.
_UNSAFE_PS_SYNTAX = re.compile(r"[{}()<>&@`]|::|\.\w+\s*\(")


def is_read_only_powershell(command: str) -> bool:
    cmd = command.strip()
    if not cmd or _UNSAFE_PS_SYNTAX.search(cmd):
        return False
    for segment in re.split(r"[|;\r\n]+", cmd):
        segment = segment.strip()
        if not segment:
            continue
        verb = segment.split()[0].lower()
        if verb.startswith("get-"):
            continue
        if verb not in _READ_ONLY_PS_COMMANDS:
            return False
    return True


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
    
    def requires_approval_for_args(self, args: dict[str, Any]) -> bool:
        """Check if approval is needed based on tool and arguments."""
        if self.risk == "LOW":
            return False
        # For PowerShell, skip approval only for plainly read-only commands
        if self.name == "shell.exec_powershell":
            cmd = str(args.get("command", ""))
            if is_read_only_powershell(cmd):
                print(f"[APPROVAL] PowerShell command auto-approved (read-only): {cmd[:100]}")
                return False
            print(f"[APPROVAL] PowerShell command requires approval: {cmd[:100]}")
            return True
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
        try:
            parsed = tool.args_model(**args)
        except Exception as e:
            return {"ok": False, "error": f"Invalid tool args: {e}"}
        return await tool.handler(parsed.model_dump(), session_id, run_id)


def build_default_registry(
    *,
    config: AppConfig,
    storage: Storage,
    hub: WebSocketHub,
    hands: Hands,
    mistral: MistralGateway,
) -> ToolRegistry:
    async def save_screenshot(b64: str, session_id: str, run_id: str) -> str:
        """Persist a base64 PNG to the artifacts directory and return its artifact id."""
        from app.storage.paths import ensure_data_dirs

        artifact_id = uuid.uuid4().hex
        path = ensure_data_dirs()["artifacts"] / f"{artifact_id}.png"
        path.write_bytes(base64.b64decode(b64))
        storage.add_artifact(
            artifact_id=artifact_id,
            session_id=session_id,
            run_id=run_id,
            kind="screenshot",
            mime="image/png",
            path=str(path),
        )
        await hub.publish(session_id, run_id, Event("artifact.created", {"run_id": run_id, "artifact_id": artifact_id, "kind": "screenshot"}))
        return artifact_id

    async def hands_observe(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        print(f"[REGISTRY DEBUG] hands_observe called with args: {args}")
        try:
            obs = await hands.observe(include_screenshot=args["include_screenshot"], ui_tree_depth=args["ui_tree_depth"])
            print(f"[REGISTRY DEBUG] hands.observe returned: {len(obs.get('interactive_elements', []))} elements, error: {obs.get('error', 'None')}")
            b64 = obs.pop("screenshot_base64_png", None)
            if b64:
                obs["screenshot_artifact_id"] = await save_screenshot(b64, session_id, run_id)
            print(f"[REGISTRY DEBUG] hands_observe returning success")
            return {"ok": True, "data": obs}
        except Exception as e:
            print(f"[REGISTRY DEBUG ERROR] hands_observe failed: {e}")
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    async def hands_act(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        res = await hands.act(actions=args["actions"])
        return {"ok": True, "data": res}

    async def shell_exec(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        from app.tools.shell import exec_powershell

        return await exec_powershell(
            command=args["command"],
            cwd=args.get("cwd"),
            timeout_s=args["timeout_s"],
            approved_by_user=bool(args.get("approved_by_user")),
        )

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

    async def fs_find_large(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        """Find the largest files efficiently using optimized PowerShell."""
        from app.tools.shell import exec_powershell
        
        path = args.get("path", "C:\\")
        count = int(args.get("count", 10))

        # The model often passes '$env:USERPROFILE'; expand that ourselves instead of letting
        # PowerShell evaluate the string, which would also run any $(...) in it.
        expanded = re.sub(r"(?i)\$env:(\w+)", lambda m: os.environ.get(m.group(1), m.group(0)), path)
        quoted_path = "'" + expanded.replace("'", "''") + "'"

        # Use direct Get-ChildItem on the specified path with error handling
        # This is simpler and more reliable than robocopy gymnastics
        cmd = f'''
$path = {quoted_path}
if (-not (Test-Path $path)) {{
    Write-Error "Path not found: $path"
    exit 1
}}

Get-ChildItem -Path $path -Recurse -File -ErrorAction SilentlyContinue |
    Select-Object FullName, Length |
    Sort-Object Length -Descending |
    Select-Object -First {count} |
    ForEach-Object {{
        $sizeStr = if ($_.Length -ge 1GB) {{ "{{0:N2}} GB" -f ($_.Length / 1GB) }} elseif ($_.Length -ge 1MB) {{ "{{0:N2}} MB" -f ($_.Length / 1MB) }} else {{ "{{0:N2}} KB" -f ($_.Length / 1KB) }}
        "File: " + $_.FullName + "`nSize: " + $sizeStr + "`n"
    }}
'''
        
        result = await exec_powershell(
            command=cmd,
            cwd=None,
            timeout_s=120,  # Give it enough time for large directories
            approved_by_user=True,  # This is a read-only operation
        )
        
        if result["ok"] and result["data"]["stdout"]:
            return {"ok": True, "data": {"files": result["data"]["stdout"], "path": path}}
        
        return {"ok": False, "error": f"Could not find large files in {path}. Error: {result.get('error', 'Unknown error')}"}

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

    # ── Research workflow tools ─────────────────────────────────────────

    async def research_search_agents(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        """List all available Mistral cloud agents."""
        try:
            agents = await mistral.list_cloud_agents()
            return {"ok": True, "data": {"agents": agents, "count": len(agents)}}
        except Exception as e:
            return {"ok": False, "error": f"Failed to list agents: {e}"}

    async def research_setup_agent(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        """Find or create a specialized agent for a task type."""
        from app.agent.specialized_agent_manager import SpecializedAgentManager
        
        manager = SpecializedAgentManager(config=config, mistral=mistral, storage=storage, session_id=session_id)
        task_type = args.get("task_type", "general_research")
        
        try:
            agent_id, was_created = await manager.find_or_create_agent(task_type)
            return {
                "ok": True, 
                "data": {
                    "agent_id": agent_id,
                    "task_type": task_type,
                    "was_created": was_created,
                }
            }
        except Exception as e:
            return {"ok": False, "error": f"Failed to setup agent: {e}"}

    async def research_ask_questions(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        """Generate clarifying questions for a task."""
        from app.agent.specialized_agent_manager import SpecializedAgentManager
        
        manager = SpecializedAgentManager(config=config, mistral=mistral, storage=storage, session_id=session_id)
        task_type = args["task_type"]
        user_input = args["user_input"]
        agent_id = args.get("agent_id")
        
        if not agent_id:
            # Try to get or create agent
            agent_id, _ = await manager.find_or_create_agent(task_type)
        
        try:
            questions, conversation_id = await manager.ask_clarifying_questions(task_type, user_input, agent_id)
            return {
                "ok": True,
                "data": {
                    "questions": [
                        {
                            "question": q.question,
                            "context": q.context,
                            "suggested_answers": q.suggested_answers,
                        }
                        for q in questions
                    ],
                    "agent_id": agent_id,
                    "conversation_id": conversation_id,
                }
            }
        except Exception as e:
            # Do not hard-fail the workflow: return fallback questions so the main agent can proceed.
            fallback = manager._fallback_questions(task_type=task_type, user_input=user_input)
            return {
                "ok": True,
                "data": {
                    "questions": [
                        {
                            "question": q.question,
                            "context": q.context,
                            "suggested_answers": q.suggested_answers,
                        }
                        for q in fallback
                    ],
                    "agent_id": agent_id,
                    "conversation_id": None,
                    "warning": f"Failed to generate questions via agent; using fallback. Error: {e}",
                },
            }

    async def research_generate_plan(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        """Generate a research plan based on user input and answers."""
        from app.agent.specialized_agent_manager import SpecializedAgentManager
        
        manager = SpecializedAgentManager(config=config, mistral=mistral, storage=storage, session_id=session_id)
        task_type = args["task_type"]
        user_input = args["user_input"]
        agent_id = args["agent_id"]
        answers = args.get("clarifying_answers", {})
        
        try:
            plan = await manager.generate_research_plan(task_type, user_input, answers, agent_id)
            return {
                "ok": True,
                "data": {
                    "plan": {
                        "task_type": plan.task_type,
                        "summary": plan.summary,
                        "steps": plan.steps,
                        "requirements": plan.requirements,
                        "estimated_time": plan.estimated_time,
                        "potential_issues": plan.potential_issues,
                    },
                    "agent_id": plan.agent_id,
                    "conversation_id": plan.conversation_id,
                }
            }
        except Exception as e:
            return {"ok": False, "error": f"Failed to generate plan: {e}"}

    async def research_execute_step(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        """Execute a single research step."""
        from app.agent.specialized_agent_manager import SpecializedAgentManager
        
        manager = SpecializedAgentManager(config=config, mistral=mistral, storage=storage, session_id=session_id)
        agent_id = str(args["agent_id"])
        step_description = str(args["step_description"])
        task_type = str(args["task_type"])
        
        try:
            result = await manager.execute_research_step(agent_id, step_description, task_type)
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": f"Failed to execute step: {e}"}

    async def web_browse_handler(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        """Web browser automation using Playwright."""
        from app.tools.web_browser import web_browse
        
        try:
            result = await web_browse(
                action=args["action"],
                session_id=session_id,
                url=args.get("url"),
                query=args.get("query"),
                selector=args.get("selector"),
                text=args.get("text"),
                submit=args.get("submit", False),
                engine=args.get("engine", "google"),
                direction=args.get("direction", "down"),
            )
            # Keep raw image bytes out of the model's message history; store an artifact instead.
            b64 = result.pop("screenshot_base64", None)
            if b64:
                result["screenshot_artifact_id"] = await save_screenshot(b64, session_id, run_id)
            return result
        except Exception as e:
            return {"ok": False, "error": f"Web browse failed: {e}"}

    async def research_execute_plan_with_hands(args: dict[str, Any], session_id: str, run_id: str) -> dict[str, Any]:
        """Execute an approved plan using Windows automation (hands).
        
        This opens applications and websites but stops before any purchase.
        """
        # This tool just returns instructions - the actual execution happens through
        # the normal hands.observe and hands.act tools in subsequent steps
        plan_steps = args.get("plan_steps", [])
        stop_before_purchase = args.get("stop_before_purchase", True)
        
        # Create a summary of what will be done
        action_summary = []
        for step in plan_steps:
            if isinstance(step, dict):
                desc = step.get("description", str(step))
                action_summary.append(desc)
        
        return {
            "ok": True,
            "data": {
                "message": f"Plan approved. Will execute {len(action_summary)} steps using Windows automation.",
                "stop_before_purchase": stop_before_purchase,
                "steps": action_summary,
                "next_action": "Use hands.observe and hands.act to execute each step. Stop if a purchase screen is encountered.",
            }
        }

    tools: dict[str, ToolDef] = {
        "hands.observe": ToolDef(
            name="hands.observe",
            description=(
                "Observe desktop state via accessibility tree (FAST, no screenshot by default). "
                "Returns: active window, windows list, and interactive_elements with labels/names/coordinates. "
                "NO SCREENSHOT NEEDED for routine tasks - the element list has everything. "
                "Use include_screenshot=true ONLY for: ambiguous layouts, unfamiliar apps, visual verification. "
                "For Settings changes and file tasks: use arguments={} (no screenshot)."
            ),
            args_model=_HandsObserveArgs,
            handler=hands_observe,
            risk="LOW",
        ),
        "hands.act": ToolDef(
            name="hands.act",
            description=(
                "Perform GUI actions using elements from hands.observe. "
                "Use element labels/names from the observe results - NO screenshots needed. "
                "Actions: "
                "click by label: {type:'click', element_label:5}; "
                "click by name: {type:'click', name:'Display'}; "
                "scroll (use wheel_times=10+): {type:'scroll', direction:'down', wheel_times:10}; "
                "shortcut: {type:'shortcut', shortcut:'win+i'}; "
                "wait: {type:'wait', ms:500}; "
                "type: {type:'type', element_label:8, text:'query', submit:true}. "
                "BATCH actions: Do multiple clicks/scrolls between observes."
            ),
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
        "fs.find_large": ToolDef(
            name="fs.find_large",
            description="Find the largest files in a directory. Searches recursively and returns the N largest files with their sizes. Use this for finding large files - do NOT use shell.exec_powershell with Get-ChildItem.",
            args_model=_FsFindLargeArgs,
            handler=fs_find_large,
            risk="LOW",
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
        # Research workflow tools
        "research.search_agents": ToolDef(
            name="research.search_agents",
            description="List all available Mistral cloud agents to check if a specialized agent already exists.",
            args_model=_ResearchSearchAgentsArgs,
            handler=research_search_agents,
            risk="LOW",
        ),
        "research.setup_agent": ToolDef(
            name="research.setup_agent",
            description="Find or create a specialized agent for a specific task type (trip_planning, product_research, event_planning, general_research). Uses mistral-large-latest with 4096 max_tokens and image/search/code capabilities.",
            args_model=_ResearchSetupAgentArgs,
            handler=research_setup_agent,
            risk="LOW",
        ),
        "research.ask_clarifying_questions": ToolDef(
            name="research.ask_clarifying_questions",
            description="Generate 2-4 clarifying questions to better understand the user's research/planning task. Returns questions with context and suggested answers.",
            args_model=_ResearchAskQuestionsArgs,
            handler=research_ask_questions,
            risk="LOW",
        ),
        "research.generate_plan": ToolDef(
            name="research.generate_plan",
            description="Generate a comprehensive research/action plan based on user input and clarifying answers. Returns structured plan with steps, requirements, time estimate, and potential issues.",
            args_model=_ResearchGeneratePlanArgs,
            handler=research_generate_plan,
            risk="LOW",
        ),
        "research.execute_step": ToolDef(
            name="research.execute_step",
            description="Execute a single research step using the specialized agent (web search, image analysis, calculations).",
            args_model=_ResearchExecuteStepArgs,
            handler=research_execute_step,
            risk="LOW",
        ),
        "research.execute_plan_with_hands": ToolDef(
            name="research.execute_plan_with_hands",
            description="Begin executing an approved plan using Windows automation (hands.observe and hands.act). Will open apps/websites but ALWAYS stops before any purchase or booking confirmation. Requires user approval for each step.",
            args_model=_ResearchExecutePlanWithHandsArgs,
            handler=research_execute_plan_with_hands,
            risk="HIGH",
        ),
        "web.browse": ToolDef(
            name="web.browse",
            description=(
                "Web browser automation using Playwright. "
                "Actions: start (launch browser), navigate (go to URL), search (Google/Bing), "
                "click (click element), type (fill input), get_text (extract content), "
                "screenshot (capture page), scroll (scroll page), get_links (list links), close (quit browser). "
                "Browser persists per session until closed. "
                "IMPORTANT: Do NOT attempt to install Playwright browsers or dependencies during a run. "
                "If this tool fails to start/launch, switch to hands.observe + hands.act in the user's real browser instead."
            ),
            args_model=_WebBrowseArgs,
            handler=web_browse_handler,
            risk="MEDIUM",
        ),
    }

    return ToolRegistry(tools)
