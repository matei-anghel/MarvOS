from __future__ import annotations

import base64
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.agent.runner import RunManager
from app.config import AppConfig
from app.events import Event, WebSocketHub
from app.hands.hands_iface import Hands
from app.hands.stub_hands import StubHands
from app.hands.windows_use_adapter import WindowsUseHands
from app.llm.fake_gateway import FakeMistralGateway
from app.llm.mistral_gateway import MistralGateway
from app.storage.db import Storage
from app.storage.paths import ensure_data_dirs
from app.tools.registry import ToolRegistry, build_default_registry


class Services:
    def __init__(
        self,
        *,
        config: AppConfig,
        storage: Storage,
        hub: WebSocketHub,
        tools: ToolRegistry,
        run_manager: RunManager,
        hands: Hands,
        mistral: MistralGateway,
        artifacts_dir: Path,
    ) -> None:
        self.config = config
        self.storage = storage
        self.hub = hub
        self.tools = tools
        self.run_manager = run_manager
        self.hands = hands
        self.mistral = mistral
        self.artifacts_dir = artifacts_dir


def _load_ui_file(name: str) -> Path:
    here = Path(__file__).resolve().parent.parent
    return (here / "ui" / name).resolve()


def _get_session_id(request: Request) -> str:
    cookie = request.cookies.get("marvos_session_id")
    if cookie:
        return cookie
    return uuid.uuid4().hex


def _hands_from_env() -> Hands:
    requested = os.environ.get("MARVOS_HANDS", "auto").lower()
    if requested == "stub":
        return StubHands()
    if requested in {"windows-use", "windows_use"}:
        return WindowsUseHands()
    if requested == "auto":
        if os.name == "nt":
            try:
                return WindowsUseHands()
            except Exception:
                return StubHands()
        return StubHands()
    return StubHands()


def _mistral_from_env() -> MistralGateway:
    if os.environ.get("MARVOS_FAKE_LLM") == "1":
        return FakeMistralGateway()
    return MistralGateway()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    config = AppConfig()
    paths = ensure_data_dirs()
    storage = Storage(paths["base"] / "app.db")
    hub = WebSocketHub(storage)
    hands = _hands_from_env()
    mistral = _mistral_from_env()
    tools = build_default_registry(config=config, storage=storage, hub=hub, hands=hands, mistral=mistral)
    run_manager = RunManager(config=config, storage=storage, hub=hub, tools=tools, hands=hands, mistral=mistral)
    app.state.services = Services(
        config=config,
        storage=storage,
        hub=hub,
        tools=tools,
        run_manager=run_manager,
        hands=hands,
        mistral=mistral,
        artifacts_dir=paths["artifacts"],
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="MarvOS Agent", lifespan=_lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "name": "marvos-agent"}

    @app.get("/app")
    async def ui_entry(request: Request) -> HTMLResponse:
        session_id = _get_session_id(request)
        services: Services = request.app.state.services
        services.storage.ensure_session(session_id)
        await services.hub.publish(session_id, None, Event("session.ready", {"session_id": session_id}))
        html = _load_ui_file("index.html").read_text(encoding="utf-8")
        response = HTMLResponse(html)
        response.set_cookie("marvos_session_id", session_id, httponly=False, samesite="lax")
        return response

    @app.get("/app.js")
    async def ui_js() -> FileResponse:
        return FileResponse(_load_ui_file("app.js"))

    @app.get("/app.css")
    async def ui_css() -> FileResponse:
        return FileResponse(_load_ui_file("app.css"))

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket, session_id: str):
        await ws.accept()
        services: Services = app.state.services
        services.storage.ensure_session(session_id)
        await services.hub.register(session_id, ws)
        try:
            while True:
                # Keepalive: UI can send pings; we ignore content.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await services.hub.unregister(session_id, ws)

    @app.post("/runs")
    async def create_run(request: Request) -> JSONResponse:
        body = await request.json()
        session_id = str(body.get("session_id") or request.cookies.get("marvos_session_id") or uuid.uuid4().hex)
        input_text = str(body.get("input_text") or "")
        mode = str(body.get("mode") or "chat")
        options = dict(body.get("options") or {})

        services: Services = request.app.state.services
        services.storage.ensure_session(session_id)
        run_id = await services.run_manager.start_run(
            session_id=session_id, input_text=input_text, mode=mode, options=options
        )
        return JSONResponse({"run_id": run_id})

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, request: Request) -> JSONResponse:
        services: Services = request.app.state.services
        await services.run_manager.cancel_run(run_id=run_id)
        return JSONResponse({"ok": True})

    @app.post("/runs/{run_id}/approval")
    async def resolve_approval(run_id: str, request: Request) -> JSONResponse:
        body = await request.json()
        tool_call_id = str(body["tool_call_id"])
        decision = str(body["decision"])
        note = str(body.get("note") or "")
        services: Services = request.app.state.services
        ok = await services.run_manager.resolve_approval(
            run_id=run_id,
            tool_call_id=tool_call_id,
            decision=decision,
            note=note,
        )
        return JSONResponse({"ok": ok})

    @app.post("/dictation/transcribe")
    async def transcribe(request: Request, file: UploadFile = File(...)) -> JSONResponse:
        services: Services = request.app.state.services
        language = request.query_params.get("language")
        content = await file.read()
        text = await services.mistral.transcribe_audio(
            model=services.config.default_audio_model,
            file_content=content,
            file_name=file.filename or "audio.webm",
            language=language,
        )
        return JSONResponse({"text": text})

    @app.get("/artifacts/{artifact_id}")
    async def get_artifact(artifact_id: str, request: Request):
        services: Services = request.app.state.services
        artifact = services.storage.get_artifact(artifact_id)
        if artifact is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return FileResponse(path=artifact.path, media_type=artifact.mime)

    @app.post("/debug/base64")
    async def debug_base64(request: Request) -> JSONResponse:
        body = await request.json()
        raw = str(body.get("data") or "")
        try:
            base64.b64decode(raw)
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    return app
