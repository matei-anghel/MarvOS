# MarvOS Windows LLM Interface Layer (MVP)

Desktop agent UI for Windows:
- **UI**: `pywebview` hosting a local web app
- **Backend**: `FastAPI` + WebSocket event stream
- **LLM**: Mistral via `mistralai`
- **Hands**: CursorTouch **Windows-Use** (pluggable; stub fallback included)
- **Dictation**: Voxtral transcription endpoint (via `mistralai`)

## Quickstart (run from source)

1. Install Python 3.12+
2. Set env var `MISTRAL_API_KEY`
3. Install deps:
   - `pip install -e ".[dev,windows]"`
4. Run:
   - `python -m app.main`

The app starts a local server on `127.0.0.1` and opens a desktop window.

## Configuration
  - `auto` (default): use Windows hands on Windows, else stub
  - `windows-use`: Windows UI automation backend (requires `windows-use` extra)
  - `stub`: no automation (safe for dev)

## Notes / Safety
- This app can automate your desktop. Run in **Windows Sandbox / VM** for demos.
- High-risk tools (PowerShell, deletes, settings changes) require explicit approval in the UI.
