from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

from app.agent.planner import build_prompts, tool_result_message, to_message
from app.agent.schemas import RunOptions, RunStatus
from app.config import AppConfig
from app.events import Event, WebSocketHub
from app.hands.hands_iface import Hands
from app.llm.mistral_gateway import MistralGateway
from app.policy.approvals import ApprovalManager
from app.storage.db import Storage
from app.tools.registry import ToolRegistry


@dataclass
class _RunRuntime:
    cancel: asyncio.Event
    approvals: ApprovalManager
    task: asyncio.Task | None = None


class RunManager:
    def __init__(
        self,
        *,
        config: AppConfig,
        storage: Storage,
        hub: WebSocketHub,
        tools: ToolRegistry,
        hands: Hands,
        mistral: MistralGateway,
    ) -> None:
        self._config = config
        self._storage = storage
        self._hub = hub
        self._tools = tools
        self._hands = hands
        self._mistral = mistral
        self._runs: dict[str, _RunRuntime] = {}
        self._lock = asyncio.Lock()

    async def start_run(self, *, session_id: str, input_text: str, mode: str, options: dict[str, Any]) -> str:
        run_id = uuid.uuid4().hex
        parsed_options = RunOptions(**options) if not isinstance(options, RunOptions) else options
        prompts = build_prompts(self._tools)
        message_history: list[dict[str, Any]] = [
            to_message("system", f"{prompts.system}\n\n{prompts.developer}"),
            to_message("user", input_text),
        ]

        await asyncio.to_thread(self._storage.create_run, run_id=run_id, session_id=session_id, input_text=input_text,
                                status=RunStatus.queued.value, options=parsed_options.model_dump(), message_history=message_history)
        await self._hub.publish(session_id, run_id, Event("run.created", {"run_id": run_id}))
        await self._hub.publish(session_id, run_id, Event("run.status", {"run_id": run_id, "status": RunStatus.queued.value}))

        runtime = _RunRuntime(cancel=asyncio.Event(), approvals=ApprovalManager())
        async with self._lock:
            self._runs[run_id] = runtime
            runtime.task = asyncio.create_task(
                self._run_loop(
                    session_id=session_id,
                    run_id=run_id,
                    message_history=message_history,
                    options=parsed_options,
                )
            )
        return run_id

    async def cancel_run(self, *, run_id: str) -> None:
        async with self._lock:
            runtime = self._runs.get(run_id)
        if runtime is not None:
            runtime.cancel.set()

    async def resolve_approval(self, *, run_id: str, tool_call_id: str, decision: str, note: str) -> bool:
        async with self._lock:
            runtime = self._runs.get(run_id)
        if runtime is None:
            return False
        return runtime.approvals.resolve(tool_call_id=tool_call_id, decision=decision, note=note)

    async def _run_loop(
        self,
        *,
        session_id: str,
        run_id: str,
        message_history: list[dict[str, Any]],
        options: RunOptions,
    ) -> None:
        await asyncio.to_thread(self._storage.update_run, run_id=run_id, status=RunStatus.running.value)
        await self._hub.publish(session_id, run_id, Event("run.status", {"run_id": run_id, "status": RunStatus.running.value}))

        async with self._lock:
            runtime = self._runs[run_id]

        try:
            await self._agent_loop(
                session_id=session_id,
                run_id=run_id,
                runtime=runtime,
                message_history=message_history,
                options=options,
            )
            await asyncio.to_thread(self._storage.update_run, run_id=run_id, status=RunStatus.succeeded.value, message_history=message_history)
            await self._hub.publish(session_id, run_id, Event("run.status", {"run_id": run_id, "status": RunStatus.succeeded.value}))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await asyncio.to_thread(self._storage.update_run, run_id=run_id, status=RunStatus.failed.value, message_history=message_history)
            await self._hub.publish(
                session_id,
                run_id,
                Event("error", {"run_id": run_id, "message": str(e)}),
            )
            await self._hub.publish(session_id, run_id, Event("run.status", {"run_id": run_id, "status": RunStatus.failed.value}))
        finally:
            async with self._lock:
                self._runs.pop(run_id, None)

    async def _agent_loop(
        self,
        *,
        session_id: str,
        run_id: str,
        runtime: _RunRuntime,
        message_history: list[dict[str, Any]],
        options: RunOptions,
    ) -> None:
        steps = 0
        while True:
            if runtime.cancel.is_set():
                await asyncio.to_thread(self._storage.update_run, run_id=run_id, status=RunStatus.canceled.value, message_history=message_history)
                await self._hub.publish(session_id, run_id, Event("run.status", {"run_id": run_id, "status": RunStatus.canceled.value}))
                return

            if steps >= options.max_steps:
                await self._hub.publish(
                    session_id,
                    run_id,
                    Event("assistant.final", {"run_id": run_id, "text": "Stopped: step limit reached."}),
                )
                return

            steps += 1
            tools_schema = self._tools.to_mistral_tools()

            full_content = ""
            tool_calls_acc: dict[int, dict[str, Any]] = {}
            
            async for chunk in self._mistral.chat_stream(
                model=self._config.default_text_model,
                messages=message_history,
                tools=tools_schema,
                tool_choice="auto",
            ):
                delta = chunk.get("delta")
                if not delta:
                    continue
                
                # Content delta
                content = getattr(delta, "content", None)
                if content:
                    full_content += content
                    await self._hub.publish(
                        session_id,
                        run_id,
                        Event("assistant.delta", {"run_id": run_id, "text": content}),
                    )
                
                # Tool call deltas
                tcs = getattr(delta, "tool_calls", None)
                if tcs:
                    for tc in tcs:
                        idx = tc.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc.id or "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""}
                            }
                        if tc.id:
                            tool_calls_acc[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls_acc[idx]["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                tool_calls_acc[idx]["function"]["arguments"] += tc.function.arguments

            # Build the assistant message to append to history
            assistant_message: dict[str, Any] = {"role": "assistant", "content": full_content}
            
            if tool_calls_acc:
                # Convert accumulated dict to list sorted by index
                sorted_tcs = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]
                assistant_message["tool_calls"] = sorted_tcs

            if assistant_message:
                message_history.append(assistant_message)

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                await self._hub.publish(session_id, run_id, Event("assistant.final", {"run_id": run_id, "text": full_content}))
                return

            if len(tool_calls) != 1:
                raise RuntimeError("Tool calling contract violated: expected exactly one tool call.")

            tool_call = tool_calls[0]
            tool_call_id = str(tool_call.get("id") or "")
            fn = tool_call.get("function") or {}
            tool_name = str(fn.get("name") or "")
            raw_args = fn.get("arguments") or "{}"
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)

            await self._hub.publish(
                session_id,
                run_id,
                Event(
                    "tool.requested",
                    {"run_id": run_id, "tool_call_id": tool_call_id, "tool_name": tool_name, "args": args},
                ),
            )

            tool_def = self._tools.tools.get(tool_name)
            if tool_def is None:
                result = {"ok": False, "error": f"Unknown tool: {tool_name}"}
            else:
                if tool_def.requires_approval and not (options.auto_approve_medium and tool_def.risk == "MEDIUM"):
                    await self._hub.publish(
                        session_id,
                        run_id,
                        Event(
                            "approval.requested",
                            {
                                "run_id": run_id,
                                "tool_call_id": tool_call_id,
                                "tool_name": tool_name,
                                "summary": tool_def.summarize(args),
                                "risk": tool_def.risk,
                            },
                        ),
                    )
                    await asyncio.to_thread(self._storage.update_run, run_id=run_id, status=RunStatus.awaiting_approval.value)
                    await self._hub.publish(session_id, run_id, Event("run.status", {"run_id": run_id, "status": RunStatus.awaiting_approval.value}))
                    decision = await runtime.approvals.wait(tool_call_id=tool_call_id)
                    await self._hub.publish(
                        session_id,
                        run_id,
                        Event(
                            "approval.resolved",
                            {"run_id": run_id, "tool_call_id": tool_call_id, "decision": decision},
                        ),
                    )
                    await asyncio.to_thread(self._storage.update_run, run_id=run_id, status=RunStatus.running.value)
                    await self._hub.publish(session_id, run_id, Event("run.status", {"run_id": run_id, "status": RunStatus.running.value}))
                    if decision != "approve":
                        result = {"ok": False, "error": "Denied by user."}
                        message_history.append(tool_result_message(tool_name=tool_name, tool_call_id=tool_call_id, result=result))
                        await self._hub.publish(
                            session_id,
                            run_id,
                            Event(
                                "tool.result",
                                {"run_id": run_id, "tool_call_id": tool_call_id, "tool_name": tool_name, "result": result},
                            ),
                        )
                        continue

                    if tool_name == "shell.exec_powershell":
                        args["approved_by_user"] = True

                result = await self._tools.execute(tool_name=tool_name, args=args, session_id=session_id, run_id=run_id)

            message_history.append(tool_result_message(tool_name=tool_name, tool_call_id=tool_call_id, result=result))
            await asyncio.to_thread(self._storage.update_run, run_id=run_id, message_history=message_history)
            await self._hub.publish(
                session_id,
                run_id,
                Event(
                    "tool.result",
                    {"run_id": run_id, "tool_call_id": tool_call_id, "tool_name": tool_name, "result": result},
                ),
            )
