<p align="center">
  <img src="marvin-assets/marvin_head_pixel.png" alt="Marvin - MarvOS Mascot" width="200"/>
</p>

<h1 align="center">🤖 MarvOS</h1>

<p align="center">
  <strong>Your AI Desktop Agent for Windows</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+"/>
  <img src="https://img.shields.io/badge/platform-Windows-0078D6.svg" alt="Windows"/>
  <img src="https://img.shields.io/badge/AI-Mistral-orange.svg" alt="Powered by Mistral"/>
  <img src="https://img.shields.io/badge/license-Proprietary-red.svg" alt="Proprietary License"/>
</p>

<p align="center">
  <em>Meet Marvin — your paranoid android assistant who can see your screen, control your desktop, browse the web, and execute complex multi-step tasks with just a conversation.</em>
</p>

---

<p align="center">
  <strong>Chat Interface</strong><br/>
  <img src="images/home%20page.jpeg" alt="Home Page" width="700"/>
</p>

<p align="center">
  <strong>Terminal Use Capability</strong><br/>
  <img src="images/terminal%20use.jpeg" alt="Terminal Use" width="700"/>
</p>

<p align="center">
  <strong>Windows Use Capability</strong><br/>
  <img src="images/windows%20use.jpeg" alt="Windows Use" width="700"/>
</p>

## ✨ Features

### 🎯 **Intelligent Desktop Automation**
- **Windows UI Control** — Click, type, scroll, and navigate using AI-powered UI analysis
- **Screen Vision** — Marvin can see and understand what's on your screen
- **Smart Action Planning** — Multi-step task execution with real-time feedback

### 💬 **Natural Conversation Interface**
- **Chat-Based Commands** — Just tell Marvin what you want in plain English
- **Voice Dictation** — Real-time speech-to-text with Voxtral transcription
- **Rich Markdown Responses** — Code highlighting, LaTeX math, and more

### 🌐 **Web Browsing & Research**
- **Headless Browser Automation** — Navigate, search, click, and extract data from websites
- **Specialized Research Agents** — Trip planning, product research, and more
- **Multi-Engine Search** — Google and Bing support

### 🛠️ **Powerful Tools**
- **File System Operations** — List, create, move, rename, and delete files
- **Shell Command Execution** — Run PowerShell commands with approval safeguards
- **Large File Finder** — Identify disk space hogs across your system

### 🔒 **Safety First**
- **Approval System** — High-risk operations require explicit user consent
- **Risk-Based Classification** — Tools are categorized by potential impact
- **Sandboxed Execution** — Recommended to run in Windows Sandbox for demos

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.12+**
- **Windows 10/11** (for full desktop automation)
- **Mistral API Key** — [Get one here](https://console.mistral.ai/)

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/MarvOS.git
cd MarvOS

# Create a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate

# Install with all extras
pip install -e ".[dev,windows]"

# Install Playwright browsers (for web browsing)
playwright install
```

### Configuration

Set your Mistral API key as an environment variable:

```powershell
# PowerShell
$env:MISTRAL_API_KEY = "your-api-key-here"

# Or add to your profile permanently
[Environment]::SetEnvironmentVariable("MISTRAL_API_KEY", "your-api-key", "User")
```

### Running MarvOS

```bash
python -m app.main
```

A sleek desktop window will open with Marvin ready to assist! 🎉

---

## 🎨 Architecture

```
MarvOS/
├── app/
│   ├── main.py              # Application entry point (pywebview)
│   ├── server.py            # FastAPI backend with WebSocket support
│   ├── config.py            # Model and app configuration
│   ├── agent/               # AI agent orchestration
│   │   ├── planner.py       # Prompt engineering & planning
│   │   ├── runner.py        # Task execution loop
│   │   └── specialized_agent_manager.py  # Research agents
│   ├── hands/               # Desktop automation backends
│   │   ├── windows_use_adapter.py  # Windows UI Automation
│   │   └── stub_hands.py    # Safe fallback for development
│   ├── llm/                 # LLM integration
│   │   └── mistral_gateway.py  # Mistral API wrapper
│   ├── tools/               # Available agent tools
│   │   ├── registry.py      # Tool definitions & routing
│   │   ├── filesystem.py    # File operations
│   │   ├── shell.py         # Command execution
│   │   └── web_browser.py   # Playwright browser control
│   └── storage/             # Persistence layer
│       └── db.py            # JSON-based storage
├── ui/                      # Frontend assets
│   ├── index.html           # Main application shell
│   ├── app.js               # UI logic & WebSocket client
│   └── app.css              # Styling
└── marvin-assets/           # Mascot artwork
```

---

## ⚙️ Configuration Options

### Hands Backend

Control how Marvin interacts with your desktop:

| Mode | Description |
|------|-------------|
| `auto` (default) | Windows automation on Windows, stub elsewhere |
| `windows-use` | Full Windows UI Automation |
| `stub` | No automation (safe for development) |

Set via environment variable:
```powershell
$env:MARVOS_HANDS = "stub"
```

### AI Models

MarvOS uses multiple specialized Mistral models:

| Purpose | Model | Use Case |
|---------|-------|----------|
| **Chat** | `labs-mistral-small-creative` | Conversational responses |
| **Agent** | `devstral-small-latest` | Tool calling & reasoning |
| **Vision** | `mistral-medium-latest` | Screenshot analysis |
| **Transcription** | `voxtral-mini-transcribe-realtime-2602` | Real-time voice input |
| **Research** | `mistral-large-latest` | Specialized agent tasks |

---

## 🛡️ Safety & Permissions

MarvOS implements a tiered risk system for operations:

| Risk Level | Approval | Examples |
|------------|----------|----------|
| 🟢 **Low** | Auto-approved | Read files, observe UI |
| 🟡 **Medium** | Prompted | Write files, shell commands |
| 🔴 **High** | Required | Delete files, system changes |

> ⚠️ **Important:** For demos and testing, we strongly recommend running MarvOS inside **Windows Sandbox** or a virtual machine to prevent unintended system modifications.

---

## 🧪 Development

### Running Tests

```bash
pytest
```

### Code Quality

```bash
# Format and lint with Ruff
ruff check app/
ruff format app/
```

### Project Structure

- **Backend:** FastAPI with async/await patterns
- **Frontend:** Vanilla JavaScript with WebSocket events
- **State:** JSON file-based persistence via `platformdirs`

---

## 📦 Open Source Acknowledgments

MarvOS is built on the shoulders of giants. We gratefully acknowledge these open-source projects:

### 🐍 Python Core

| Package | License | Description |
|---------|---------|-------------|
| [**FastAPI**](https://fastapi.tiangolo.com/) | MIT | High-performance async web framework |
| [**Uvicorn**](https://www.uvicorn.org/) | BSD-3-Clause | Lightning-fast ASGI server |
| [**Pydantic**](https://pydantic.dev/) | MIT | Data validation using Python type hints |
| [**pywebview**](https://pywebview.flowrl.com/) | BSD-3-Clause | Cross-platform desktop webview wrapper |
| [**Pillow (PIL)**](https://python-pillow.org/) | HPND | Python imaging library |
| [**platformdirs**](https://github.com/platformdirs/platformdirs) | MIT | Cross-platform app directory paths |
| [**python-multipart**](https://github.com/Kludex/python-multipart) | Apache-2.0 | Streaming multipart form parser |

### 🤖 AI & Automation

| Package | License | Description |
|---------|---------|-------------|
| [**mistralai**](https://github.com/mistralai/client-python) | Apache-2.0 | Official Mistral AI Python client |
| [**Playwright**](https://playwright.dev/python/) | Apache-2.0 | Cross-browser automation library |
| [**uiautomation**](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows) | Apache-2.0 | Windows UI Automation wrapper |
| [**fuzzywuzzy**](https://github.com/seatgeek/fuzzywuzzy) | GPL-2.0 | Fuzzy string matching |

### 🧰 Development Tools

| Package | License | Description |
|---------|---------|-------------|
| [**pytest**](https://pytest.org/) | MIT | Python testing framework |
| [**httpx**](https://www.python-httpx.org/) | BSD-3-Clause | Async HTTP client for testing |
| [**Ruff**](https://github.com/astral-sh/ruff) | MIT | Fast Python linter and formatter |

### 🌐 Frontend Libraries (CDN)

| Library | License | Description |
|---------|---------|-------------|
| [**marked.js**](https://marked.js.org/) | MIT | Markdown parser and compiler |
| [**DOMPurify**](https://github.com/cure53/DOMPurify) | Apache-2.0 | XSS sanitizer for HTML |
| [**highlight.js**](https://highlightjs.org/) | BSD-3-Clause | Syntax highlighting for code |
| [**KaTeX**](https://katex.org/) | MIT | Fast math typesetting |

### 🎨 Fonts

| Font | License | Usage |
|------|---------|-------|
| [**Syne**](https://fonts.google.com/specimen/Syne) | OFL | Headlines & branding |
| [**DM Sans**](https://fonts.google.com/specimen/DM+Sans) | OFL | Body text |
| [**JetBrains Mono**](https://fonts.google.com/specimen/JetBrains+Mono) | OFL | Code & monospace |
| [**Silkscreen**](https://fonts.google.com/specimen/Silkscreen) | OFL | Pixel-art elements |

### 🏗️ Build System

| Tool | License | Description |
|------|---------|-------------|
| [**setuptools**](https://setuptools.pypa.io/) | MIT | Python packaging |
| [**wheel**](https://wheel.readthedocs.io/) | MIT | Built-package format |

---

## 🎭 Meet Marvin

<p align="center">
  <img src="marvin-assets/marvin_fullbody_pixel.png" alt="Marvin Full Body" width="300"/>
</p>

Marvin is our pixel-art mascot — a friendly (if slightly paranoid) android inspired by a certain depressed robot from a well-known sci-fi series. Despite his existential concerns, he's surprisingly helpful at automating your desktop tasks!

---

## 📜 License

This project is licensed under the **MIT License**.

See the [LICENSE](LICENSE) file for details.

---

<p align="center">
  <strong>Built with 💚 and existential dread</strong>
</p>

<p align="center">
  <em>"Here I am, brain the size of a planet, and they ask me to organize their Downloads folder..."</em><br/>
  — Marvin, probably
</p>
