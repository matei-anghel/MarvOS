function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(";").shift();
  return null;
}

const sessionId = getCookie("marvos_session_id") || crypto.randomUUID().replaceAll("-", "");
const chatLog = document.getElementById("chatLog");
const eventsLog = document.getElementById("eventsLog");
const statusLine = document.getElementById("statusLine");

// Task management (chat tabs)
// Declared early because websocket events can arrive immediately.
let tasks = [];
let activeTaskId = null;
const sendBtn = document.getElementById("sendBtn");
const stopBtn = document.getElementById("stopBtn");
const micBtn = document.getElementById("micBtn");
const themeBtn = document.getElementById("themeBtn");
const chatInput = document.getElementById("chatInput");

const clearEventsBtn = document.getElementById("clearEventsBtn");

const approvalModal = document.getElementById("approvalModal");
const approvalSummary = document.getElementById("approvalSummary");
const approveBtn = document.getElementById("approveBtn");
const denyBtn = document.getElementById("denyBtn");

const confirmModal = document.getElementById("confirmModal");
const confirmTitle = document.getElementById("confirmTitle");
const confirmBody = document.getElementById("confirmBody");
const confirmCancelBtn = document.getElementById("confirmCancelBtn");
const confirmOkBtn = document.getElementById("confirmOkBtn");
const confirmBackdrop = document.getElementById("confirmBackdrop");

let confirmResolver = null;

// Sidebar / layout refs
const sidebarToggle = document.getElementById("sidebarToggle");
const sidebarLeft = document.getElementById("sidebarLeft");
const sidebarRight = document.getElementById("sidebarRight");
const newTaskBtn = document.getElementById("newTaskBtn");
const taskListEl = document.getElementById("taskList");
const inspectorClose = document.getElementById("inspectorClose");
function summarizeOneLine(text, maxLen = 160) {
  const s = String(text || "");
  const firstLine = s.split(/\r?\n/)[0] || "";
  const trimmed = firstLine.trim();
  if (!trimmed) return "";
  if (trimmed.length <= maxLen && s.trim() === trimmed) return trimmed;
  if (trimmed.length <= maxLen) return trimmed + " ...";
  return trimmed.slice(0, Math.max(0, maxLen - 4)).trimEnd() + " ...";
}

function renderEventLine(obj) {
  if (!obj) return null;
  const payload = obj.payload || obj;

  const line = document.createElement("div");
  line.className = "event-line";

  const time = document.createElement("span");
  time.className = "event-time";
  time.textContent = new Date(obj.__ts || Date.now()).toLocaleTimeString();

  const type = document.createElement("span");
  type.className = "event-type";
  type.textContent = ` ${obj.type || "?"}`;

  let detail = document.createElement("span");
  detail.className = "event-detail";

  if (obj.type === "artifact.created" && payload.kind === "screenshot" && payload.artifact_id) {
    line.classList.add("event-line--media");
    detail = document.createElement("div");
    detail.className = "event-detail event-detail--media";
    const label = document.createElement("div");
    label.className = "event-detail-text";
    label.textContent = ` screenshot artifact: ${payload.artifact_id}`;
    const img = document.createElement("img");
    img.className = "event-image";
    img.alt = "screenshot";
    img.src = `/artifacts/${payload.artifact_id}`;
    detail.appendChild(label);
    detail.appendChild(img);
  } else if (obj.type === "assistant.final") {
    const full = payload.text || "";
    detail.classList.add("event-detail--one-line");
    detail.textContent = " " + summarizeOneLine(full);
    // Keep the full text available for selection/copy if needed.
    detail.dataset.full = full;
  } else {
    detail.textContent = ` ${JSON.stringify(payload).slice(0, 200)}`;
  }

  line.appendChild(time);
  line.appendChild(type);
  line.appendChild(detail);
  return line;
}

function restoreTaskEvents() {
  if (!eventsLog) return;
  eventsLog.textContent = "";
  const task = tasks.find((t) => t.id === activeTaskId);
  const evts = (task && Array.isArray(task.events)) ? task.events : [];
  evts.forEach((e) => {
    // Keep the inspector clean: never backfill chunk events.
    if (e && e.type === "assistant.delta") return;
    const line = renderEventLine(e);
    if (line) eventsLog.appendChild(line);
  });
  eventsLog.scrollTop = eventsLog.scrollHeight;
}
const mainChat = document.querySelector(".main-chat");

let currentRunId = null;
const approvalsById = new Map();
let recorder = null;
let chunks = [];
let streamingNode = null;
let isRunning = false;

function updateEmptyState() {
  if (!mainChat || !chatLog) return;
  const isEmpty = chatLog.children.length === 0;
  mainChat.classList.toggle("empty", isEmpty);
}

// ── Realtime dictation state ────────────────────────────────────────
let dictationWs = null;       // WebSocket for /ws/dictation
let dictationCtx = null;      // AudioContext
let dictationSource = null;   // MediaStreamAudioSourceNode
let dictationStream = null;   // raw getUserMedia stream
let dictationWorklet = null;  // AudioWorkletNode
let dictationText = "";       // accumulated transcript

function getInitialTheme() {
  try {
    const saved = localStorage.getItem("marvos_theme");
    if (saved === "dark" || saved === "light") return saved;
  } catch {}
  try {
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark";
  } catch {}
  return "light";
}

function applyTheme(theme, { persist } = { persist: false }) {
  document.documentElement.dataset.theme = theme;
  updateHighlightTheme(theme);
  if (persist) {
    try {
      localStorage.setItem("marvos_theme", theme);
    } catch {}
  }
}

function setupTheme() {
  let saved = null;
  try {
    saved = localStorage.getItem("marvos_theme");
  } catch {}

  const current = (saved === "dark" || saved === "light")
    ? saved
    : (document.documentElement.dataset.theme || getInitialTheme());

  applyTheme(current, { persist: false });

  // If the user hasn't explicitly chosen a theme, follow the system preference.
  if (!(saved === "dark" || saved === "light")) {
    try {
      const media = window.matchMedia("(prefers-color-scheme: dark)");
      media.addEventListener("change", (e) => applyTheme(e.matches ? "dark" : "light", { persist: false }));
    } catch {}
  }

  if (!themeBtn) return;
  themeBtn.onclick = () => {
    const next = (document.documentElement.dataset.theme || "dark") === "dark" ? "light" : "dark";
    applyTheme(next, { persist: true });
  };
}

function updateHighlightTheme(theme) {
  const light = document.getElementById("hljsLight");
  const dark = document.getElementById("hljsDark");
  if (!light || !dark) return;
  if (theme === "dark") {
    dark.disabled = false;
    light.disabled = true;
  } else {
    light.disabled = false;
    dark.disabled = true;
  }
}

const SCALE = 5;
const PALETTE = {
  ".": "transparent",
  h: "#aadd77",
  g: "#7ecb47",
  d: "#4d8b2c",
  o: "#2d5a1e",
  b: "#0f172a",
  w: "#ffffff",
  u: "#3b82f6",
  l: "#bfdbfe",
  y: "#fbbf24",
  r: "#ef4444",
  c: "#14b8a6",
  x: "#1e293b",
};

const MAP = [
  "........................",
  "....ooo..........ooo....",
  "...o.l.o........o.u.o...",
  "...o.l.o........o.u.o...",
  "....ooo..........ooo....",
  ".....g............g.....",
  ".....g............g.....",
  "...oooooooooooooooooo...",
  "..ogghhhhhhhhhhhhhgggo..",
  ".ogghhhhhhhhhhhhhhhgggo.",
  ".ogbbbbbhhhhhhhhbbbbbgo.",
  "oghbbwwwbbhhhhbbwwwbbhgo",
  "oghbbwwwbbhhhhbbwwwbbhgo",
  "oghbbwwwbbhhhhbbwwwbbhgo",
  "oggbbbbbbbhhhhbbbbbbbggo",
  "ogghbbbbbhhhhhhbbbbbhggo",
  "oggggghhhhhhhhhhhhgggggo",
  "oggggghhhhhbbhhhhhgggggo",
  ".oggggggghhbbhhgggggggo.",
  "..ogggggggggggggggggoo..",
  "...ooooooooooooooooo....",
  "........................",
  "........................",
  "........................",
];

function renderPixelSvg(containerEl, map, palette, scale) {
  if (!containerEl) return;
  const size = map.length;
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width", String(size * scale));
  svg.setAttribute("height", String(size * scale));
  svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
  svg.style.imageRendering = "pixelated";
  svg.style.shapeRendering = "crispEdges";

  for (let y = 0; y < map.length; y++) {
    const row = map[y];
    if (!row) continue;
    for (let x = 0; x < row.length; x++) {
      const ch = row[x];
      const fill = palette[ch];
      if (!fill || ch === ".") continue;
      const rect = document.createElementNS(svgNS, "rect");
      rect.setAttribute("x", String(x));
      rect.setAttribute("y", String(y));
      rect.setAttribute("width", "1");
      rect.setAttribute("height", "1");
      rect.setAttribute("fill", fill);
      svg.appendChild(rect);
    }
  }

  containerEl.replaceChildren(svg);
}

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

const markdownRenderer = new marked.Renderer();
markdownRenderer.code = function (code, infostring) {
  const lang = String(infostring || "").trim().split(/\s+/)[0];
  let highlighted = "";

  if (window.hljs) {
    if (lang && hljs.getLanguage(lang)) {
      highlighted = hljs.highlight(code, { language: lang }).value;
    } else {
      highlighted = hljs.highlightAuto(code).value;
    }
  } else {
    highlighted = escapeHtml(code);
  }

  const langClass = lang ? ` language-${lang}` : "";
  return `<pre><code class="hljs${langClass}">${highlighted}</code></pre>`;
};

markdownRenderer.link = function (href, title, text) {
  const safeHref = escapeHtml(href || "");
  const safeTitle = title ? ` title="${escapeHtml(title)}"` : "";
  return `<a href="${safeHref}" target="_blank" rel="noreferrer noopener"${safeTitle}>${text}</a>`;
};

marked.setOptions({
  renderer: markdownRenderer,
  breaks: true,
  gfm: true,
  mangle: false,
  headerIds: false,
});

const markdownSanitizeConfig = {
  ALLOWED_TAGS: [
    "a", "p", "br", "strong", "em", "del", "code", "pre",
    "blockquote", "ul", "ol", "li", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "th", "td",
    "img", "span",
  ],
  ALLOWED_ATTR: ["href", "title", "target", "rel", "src", "alt", "class", "width", "height"],
};

function protectMathExpressions(input) {
  const src = String(input || "");
  if (!src) return { text: "", tokens: [] };

  const tokens = [];
  const stash = (raw) => {
    const token = `@@MATH_${tokens.length}@@`;
    tokens.push(raw);
    return token;
  };

  let text = src;
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (m) => stash(m));
  text = text.replace(/\\\[([\s\S]+?)\\\]/g, (m) => stash(m));
  text = text.replace(/\\\((.+?)\\\)/g, (m) => stash(m));
  text = text.replace(/(^|[^\\])\$(?!\$)([^$\n]+?)\$/g, (_m, pre, body) => {
    return pre + stash(`$${body}$`);
  });

  return { text, tokens };
}

function restoreMathExpressions(html, tokens) {
  if (!tokens || !tokens.length) return html;
  return html.replace(/@@MATH_(\d+)@@/g, (_m, idx) => tokens[Number(idx)] || "");
}

function renderMarkdown(md) {
  const src = String(md || "");
  if (!src) return "";

  const protectedMath = protectMathExpressions(src);
  const parsed = marked.parse(protectedMath.text || "");
  const restored = restoreMathExpressions(parsed, protectedMath.tokens);
  const sanitized = DOMPurify.sanitize(restored, markdownSanitizeConfig);
  return sanitized;
}

function renderKaTeXMath(rootEl) {
  if (!rootEl || typeof window.renderMathInElement !== "function") return;
  try {
    renderMathInElement(rootEl, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\[", right: "\\]", display: true },
        { left: "\\(", right: "\\)", display: false },
        { left: "$", right: "$", display: false },
      ],
      ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"],
      throwOnError: false,
    });
  } catch (err) {
    console.warn("KaTeX render failed:", err);
  }
}

function postProcessMarkdown(el) {
  if (!el) return;
  const raw = el.dataset ? el.dataset.raw || "" : "";
  if (!raw) return;
  if (!/[\$\\\[]/.test(raw)) return;
  renderKaTeXMath(el);
}

function isNearBottom(el, threshold = 80) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
}

function scrollChatToBottom({ force = false } = {}) {
  if (!chatLog) return;
  if (!force && !isNearBottom(chatLog)) return;
  requestAnimationFrame(() => {
    chatLog.scrollTop = chatLog.scrollHeight;
  });
}

function appendChat(role, text) {
  const div = document.createElement("div");
  const roleClass = role === "user" ? "msg--user" : (role === "system" ? "msg--system" : "msg--assistant");
  div.className = `msg ${roleClass}`;

  const roleEl = document.createElement("div");
  roleEl.className = "msgRole";
  roleEl.textContent = role === "user" ? "You" : (role === "system" ? "Status" : "Marvin");

  const bodyEl = document.createElement("div");
  bodyEl.className = "msgBody";
  bodyEl.dataset.raw = text || "";
  bodyEl.innerHTML = renderMarkdown(text || "");
  postProcessMarkdown(bodyEl);

  div.appendChild(roleEl);
  div.appendChild(bodyEl);
  chatLog.appendChild(div);
  scrollChatToBottom({ force: true });
  updateEmptyState();

  // Hide presets after first user message & auto-name task
  if (role === "user") {
    const presets = document.getElementById("presets");
    if (presets) presets.style.display = "none";
    // Auto-name task from first message
    const task = tasks.find(t => t.id === activeTaskId);
    if (task && task.name === "New Task" && text) {
      task.name = text.length > 40 ? text.slice(0, 37) + "…" : text;
      saveTasks();
      renderTasks();
    }
  }

  return bodyEl;
}

function appendApprovalMessage(payload) {
  if (!chatLog || !payload) return;
  const div = document.createElement("div");
  div.className = "msg msg--assistant msg--approval";

  const roleEl = document.createElement("div");
  roleEl.className = "msgRole";
  roleEl.textContent = "Approval";

  const bodyEl = document.createElement("div");
  bodyEl.className = "msgBody";
  const summaryText = `[${payload.risk}] ${payload.tool_name}\n\n${payload.summary || ""}`.trim();
  bodyEl.dataset.raw = summaryText;
  bodyEl.innerHTML = renderMarkdown(summaryText);
  postProcessMarkdown(bodyEl);

  const statusEl = document.createElement("div");
  statusEl.className = "approval-status";
  statusEl.textContent = "Awaiting approval";

  const actionsEl = document.createElement("div");
  actionsEl.className = "approval-actions";

  const denyBtn = document.createElement("button");
  denyBtn.className = "btn btn--ghost";
  denyBtn.textContent = "Deny";
  denyBtn.onclick = () => resolveApproval("deny", payload.tool_call_id);

  const approveBtn = document.createElement("button");
  approveBtn.className = "btn btn--primary";
  approveBtn.textContent = "Approve";
  approveBtn.onclick = () => resolveApproval("approve", payload.tool_call_id);

  actionsEl.appendChild(denyBtn);
  actionsEl.appendChild(approveBtn);

  div.appendChild(roleEl);
  div.appendChild(bodyEl);
  div.appendChild(statusEl);
  div.appendChild(actionsEl);
  chatLog.appendChild(div);
  scrollChatToBottom({ force: true });
  updateEmptyState();

  approvalsById.set(payload.tool_call_id, { statusEl, actionsEl });
}

function appendEvent(obj) {
  if (!obj) return;

  // Don't show streaming chunk events in the inspector.
  if (obj.type === "assistant.delta") return;

  // Stamp a client-side timestamp for per-chat restore.
  if (obj.__ts == null) obj.__ts = Date.now();

  const task = tasks.find((t) => t.id === activeTaskId);
  if (task) {
    task.events = Array.isArray(task.events) ? task.events : [];
    task.events.push(obj);
    try { saveTasks(); } catch {}
  }

  const line = renderEventLine(obj);
  if (!line) return;
  eventsLog.appendChild(line);
  eventsLog.scrollTop = eventsLog.scrollHeight;

  // Auto-show inspector when (non-chunk) events arrive
  showInspector();
}

/* ─── Status helper ─── */
function setStatus(text, running, { showStop = true } = {}) {
  const label = statusLine.querySelector(".status-label");
  if (label) label.textContent = text;
  isRunning = running;
  if (running) {
    statusLine.classList.add("running");
    if (showStop) stopBtn.classList.add("visible");
    else stopBtn.classList.remove("visible");
  } else {
    statusLine.classList.remove("running");
    stopBtn.classList.remove("visible");
  }

  // Stop should replace Send while a run is active.
  if (sendBtn) {
    const showStopHere = running && showStop;
    sendBtn.classList.toggle("hidden", showStopHere);
    sendBtn.disabled = showStopHere;
  }
}

async function startRun(text) {
  appendChat("user", text);
  setStatus("Starting...", true, { showStop: true });
  const res = await fetch("/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      input_text: text,
      mode: "chat",
      options: { include_screenshots: true, auto_approve_medium: true },
    }),
  });
  const body = await res.json();
  currentRunId = body.run_id;
}

async function cancelRun() {
  if (!currentRunId) return;
  await fetch(`/runs/${currentRunId}/cancel`, { method: "POST" });
}

function showApprovalModal(summary) {
  approvalSummary.textContent = summary;
  approvalModal.classList.remove("hidden");
}

function hideApprovalModal() {
  approvalModal.classList.add("hidden");
}

function showConfirmDialog({ title, message, okText } = {}) {
  if (!confirmModal) return Promise.resolve(false);
  if (confirmTitle) confirmTitle.textContent = title || "Confirm";
  if (confirmBody) confirmBody.textContent = message || "";
  if (confirmOkBtn) confirmOkBtn.textContent = okText || "OK";

  confirmModal.classList.remove("hidden");

  // Resolve only once.
  return new Promise((resolve) => {
    confirmResolver = (val) => {
      if (!confirmModal.classList.contains("hidden")) confirmModal.classList.add("hidden");
      confirmResolver = null;
      resolve(Boolean(val));
    };
    // Focus cancel by default to prevent accidental deletes.
    try { confirmCancelBtn && confirmCancelBtn.focus(); } catch {}
  });
}

function _closeConfirm(val) {
  if (typeof confirmResolver === "function") confirmResolver(val);
}

if (confirmCancelBtn) confirmCancelBtn.onclick = () => _closeConfirm(false);
if (confirmOkBtn) confirmOkBtn.onclick = () => _closeConfirm(true);
if (confirmBackdrop) confirmBackdrop.onclick = () => _closeConfirm(false);

document.addEventListener("keydown", (e) => {
  if (!confirmModal || confirmModal.classList.contains("hidden")) return;
  if (e.key === "Escape") {
    e.preventDefault();
    _closeConfirm(false);
  }
});

function setApprovalMessageState(toolCallId, state) {
  const entry = approvalsById.get(toolCallId);
  if (!entry) return;
  if (entry.statusEl) entry.statusEl.textContent = state;
  if (entry.actionsEl) entry.actionsEl.classList.add("hidden");
}

async function resolveApproval(decision, toolCallId) {
  if (!toolCallId || !currentRunId) return;
  setApprovalMessageState(toolCallId, decision === "approve" ? "Approved" : "Denied");
  await fetch(`/runs/${currentRunId}/approval`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tool_call_id: toolCallId, decision }),
  });
  approvalsById.delete(toolCallId);
}

// ── Realtime dictation (PCM S16LE 16 kHz mono over /ws/dictation) ─────
// Falls back to MediaRecorder + POST /dictation/transcribe when AudioContext
// or AudioWorklet are unavailable.

const DICTATION_SAMPLE_RATE = 16000;

/**
 * Inline AudioWorkletProcessor source that resamples input to 16 kHz S16LE
 * and posts binary chunks to the main thread ~every 480 ms.
 */
function getDictationProcessorSource() {
  return `
class DictationProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.targetRate = options.processorOptions.targetRate || 16000;
    this.ratio = sampleRate / this.targetRate;
    this.buffer = [];
    // Accumulate ~480ms worth of target-rate samples before flushing.
    this.flushSize = Math.floor(this.targetRate * 0.48);
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const data = input[0]; // Float32, mono channel 0

    // Naive linear down-sample.
    for (let i = 0; i < data.length; i += this.ratio) {
      const idx = Math.floor(i);
      if (idx < data.length) {
        // Clamp to int16 range.
        const s = Math.max(-1, Math.min(1, data[idx]));
        this.buffer.push(s < 0 ? s * 0x8000 : s * 0x7FFF);
      }
    }

    if (this.buffer.length >= this.flushSize) {
      const int16 = new Int16Array(this.buffer);
      this.port.postMessage(int16.buffer, [int16.buffer]);
      this.buffer = [];
    }
    return true;
  }
}
registerProcessor("dictation-processor", DictationProcessor);
`;
}

function canUseRealtimeDictation() {
  return !!(window.AudioContext || window.webkitAudioContext) && !!window.AudioWorklet;
}

async function startDictation() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    appendChat("assistant", "Dictation not supported in this embedded browser.");
    return;
  }

  if (canUseRealtimeDictation()) {
    await startRealtimeDictation();
  } else {
    await startFallbackDictation();
  }
}

async function startRealtimeDictation() {
  // 1. Open mic.
  dictationStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, sampleRate: { ideal: DICTATION_SAMPLE_RATE } },
  });

  // 2. Set up AudioContext + Worklet.
  const AC = window.AudioContext || window.webkitAudioContext;
  dictationCtx = new AC({ sampleRate: dictationStream.getAudioTracks()[0].getSettings().sampleRate || 48000 });

  const blob = new Blob([getDictationProcessorSource()], { type: "application/javascript" });
  const blobUrl = URL.createObjectURL(blob);
  await dictationCtx.audioWorklet.addModule(blobUrl);
  URL.revokeObjectURL(blobUrl);

  dictationWorklet = new AudioWorkletNode(dictationCtx, "dictation-processor", {
    processorOptions: { targetRate: DICTATION_SAMPLE_RATE },
  });

  dictationSource = dictationCtx.createMediaStreamSource(dictationStream);
  dictationSource.connect(dictationWorklet);
  dictationWorklet.connect(dictationCtx.destination); // required for process() to run

  // 3. Open the dictation WebSocket.
  const wsProto = location.protocol === "https:" ? "wss" : "ws";
  dictationWs = new WebSocket(`${wsProto}://${location.host}/ws/dictation`);
  dictationWs.binaryType = "arraybuffer";
  dictationText = "";

  dictationWs.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === "delta") {
        dictationText += msg.text || "";
        chatInput.value = dictationText;
        setStatus("Transcribing\u2026", true, { showStop: false });
      } else if (msg.type === "done") {
        chatInput.value = dictationText;
        setStatus("Ready", false, { showStop: false });
      } else if (msg.type === "error") {
        setStatus("Transcription error", false, { showStop: false });
        console.warn("Dictation error:", msg.text);
      }
    } catch (e) {
      console.warn("Dictation WS parse error:", e);
    }
  };

  dictationWs.onclose = () => {
    cleanupRealtimeDictation(false);
  };

  // 4. Forward PCM chunks from the worklet to the WS.
  dictationWorklet.port.onmessage = (evt) => {
    if (dictationWs && dictationWs.readyState === WebSocket.OPEN) {
      dictationWs.send(evt.data); // ArrayBuffer of Int16
    }
  };

  micBtn.classList.add("btn--recording");
  micBtn.title = "Stop dictation";
  setStatus("Listening\u2026", true, { showStop: false });
}

function cleanupRealtimeDictation(sendStop = true) {
  if (dictationWs && dictationWs.readyState === WebSocket.OPEN && sendStop) {
    try { dictationWs.send(JSON.stringify({ type: "stop" })); } catch {}
  }
  if (dictationWorklet) {
    try { dictationWorklet.disconnect(); } catch {}
    dictationWorklet = null;
  }
  if (dictationSource) {
    try { dictationSource.disconnect(); } catch {}
    dictationSource = null;
  }
  if (dictationCtx) {
    try { dictationCtx.close(); } catch {}
    dictationCtx = null;
  }
  if (dictationStream) {
    dictationStream.getTracks().forEach((t) => t.stop());
    dictationStream = null;
  }
  dictationWs = null;
  micBtn.classList.remove("btn--recording");
  micBtn.title = "Dictation";
}

// ── Fallback: MediaRecorder + POST (Voxtral Mini Transcribe V2) ──────

async function startFallbackDictation() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = (e) => chunks.push(e.data);
  recorder.onstop = async () => {
    const blob = new Blob(chunks, { type: "audio/webm" });
    const form = new FormData();
    form.append("file", blob, "audio.webm");
    setStatus("Transcribing\u2026", true, { showStop: false });
    const res = await fetch("/dictation/transcribe", { method: "POST", body: form });
    const body = await res.json();
    chatInput.value = body.text || "";
    setStatus("Ready", false, { showStop: false });
    micBtn.classList.remove("btn--recording");
    micBtn.title = "Dictation";
  };
  recorder.start();
  micBtn.classList.add("btn--recording");
  micBtn.title = "Stop dictation";
  setStatus("Listening\u2026", true, { showStop: false });
}

function stopDictation() {
  // Realtime path.
  if (dictationWs) {
    cleanupRealtimeDictation(true);
    setStatus("Ready", false, { showStop: false });
    return;
  }
  // Fallback path.
  if (recorder) {
    recorder.stop();
    recorder = null;
  }
}

/* ─── Auto-resize textarea ─── */
function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}
chatInput.addEventListener("input", () => autoResize(chatInput));
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendBtn.click();
  }
});

sendBtn.onclick = async () => {
  const text = chatInput.value.trim();
  if (!text) return;
  if (isRunning) return;
  chatInput.value = "";
  chatInput.style.height = "";
  await startRun(text);
};

stopBtn.onclick = async () => {
  await cancelRun();
};

micBtn.onclick = async () => {
  if (recorder || dictationWs) stopDictation();
  else await startDictation();
};

if (clearEventsBtn) {
  clearEventsBtn.onclick = () => {
    const task = tasks.find((t) => t.id === activeTaskId);
    if (task) {
      task.events = [];
      try { saveTasks(); } catch {}
    }
    eventsLog.textContent = "";
    // If events are empty, hide inspector
    hideInspector();
  };
}

document.querySelectorAll("[data-preset]").forEach((btn) => {
  btn.onclick = () => {
    chatInput.value = btn.getAttribute("data-preset");
    chatInput.focus();
    autoResize(chatInput);
  };
});

// Initialize tasks before websocket events start arriving.
initLeftSidebar();
loadTasks();
renderTasks();
restoreTaskChat();
restoreTaskEvents();

updateEmptyState();

const wsProto = location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${wsProto}://${location.host}/ws?session_id=${encodeURIComponent(sessionId)}`);
ws.onmessage = (evt) => {
  const msg = JSON.parse(evt.data);
  appendEvent(msg);
  const { type, payload } = msg;
  if (type === "run.status") {
    const s = payload.status;
    setStatus(
      `${s.charAt(0).toUpperCase() + s.slice(1)}`,
      s === "running" || s === "starting",
      { showStop: true }
    );
    if (s === "succeeded" || s === "failed" || s === "canceled") {
      stopBtn.classList.remove("visible");
      // Persist the latest chat messages if the user switches chats right after a run.
      try { saveCurrentChat(); } catch {}
      if (payload.run_id === currentRunId) {
        const label = s.charAt(0).toUpperCase() + s.slice(1);
        appendChat("system", `Run ${label}.`);
      }
    }
  }
  if (type === "assistant.final") {
    const finalText = payload.text || "";
    if (streamingNode) {
      streamingNode.dataset.raw = finalText;
      streamingNode.innerHTML = renderMarkdown(finalText);
      postProcessMarkdown(streamingNode);
      streamingNode = null;
      scrollChatToBottom({ force: true });
    } else {
      appendChat("assistant", finalText);
    }
    try { saveCurrentChat(); } catch {}
  }
  if (type === "assistant.delta") {
    if (!streamingNode) {
      streamingNode = appendChat("assistant", "");
    }
    const next = (streamingNode.dataset.raw || "") + (payload.text || "");
    streamingNode.dataset.raw = next;
    streamingNode.innerHTML = renderMarkdown(next);
    postProcessMarkdown(streamingNode);
    scrollChatToBottom({ force: false });
  }
  if (type === "approval.requested") {
    appendApprovalMessage(payload);
  }
  if (type === "approval.resolved") {
    setApprovalMessageState(payload.tool_call_id, payload.decision === "approve" ? "Approved" : "Denied");
  }
};
ws.onopen = () => ws.send("hello");

if (approveBtn) approveBtn.onclick = null;
if (denyBtn) denyBtn.onclick = null;

/* ═══════════════════════════════════════════════════
   SIDEBAR LOGIC
   ═══════════════════════════════════════════════════ */

// ── Left sidebar toggle ──
function initLeftSidebar() {
  const saved = localStorage.getItem("marvos_sidebar_open");
  const leftHandle = document.getElementById("resizeLeft");
  if (saved === "false") {
    sidebarLeft.classList.add("collapsed");
    if (leftHandle) leftHandle.classList.add("hidden");
  } else {
    const savedW = localStorage.getItem("marvos_left_w");
    if (savedW) sidebarLeft.style.width = savedW + "px";
  }
  sidebarToggle.onclick = () => {
    const willCollapse = !sidebarLeft.classList.contains("collapsed");
    sidebarLeft.classList.toggle("collapsed");
    if (willCollapse) {
      sidebarLeft.style.width = "";
      if (leftHandle) leftHandle.classList.add("hidden");
    } else {
      const savedW = localStorage.getItem("marvos_left_w");
      if (savedW) sidebarLeft.style.width = savedW + "px";
      if (leftHandle) leftHandle.classList.remove("hidden");
    }
    localStorage.setItem("marvos_sidebar_open", String(!willCollapse));
  };
}

// ── Right sidebar (inspector) — hidden until events arrive ──
function showInspector() {
  sidebarRight.classList.remove("collapsed");
  const toggle = document.getElementById("inspectorToggle");
  const handle = document.getElementById("resizeRight");
  if (toggle) toggle.classList.remove("visible");
  if (handle) handle.classList.remove("hidden");
  const savedW = localStorage.getItem("marvos_right_w");
  if (savedW) sidebarRight.style.width = savedW + "px";
}
function hideInspector() {
  sidebarRight.classList.add("collapsed");
  sidebarRight.style.width = "";
  const toggle = document.getElementById("inspectorToggle");
  const handle = document.getElementById("resizeRight");
  if (toggle) toggle.classList.add("visible");
  if (handle) handle.classList.add("hidden");
}

if (inspectorClose) {
  inspectorClose.onclick = hideInspector;
}

// Start collapsed
sidebarRight.classList.add("collapsed");

// Hide right resize handle since inspector starts collapsed
const resizeRightHandle = document.getElementById("resizeRight");
if (resizeRightHandle) resizeRightHandle.classList.add("hidden");

/* ═══════════════════════════════════════════════════
   CUSTOM TITLE BAR
   ═══════════════════════════════════════════════════ */
(function initTitleBar() {
  const minBtn = document.getElementById("titleMinBtn");
  const maxBtn = document.getElementById("titleMaxBtn");
  const closeBtn = document.getElementById("titleCloseBtn");
  const titlebarDrag = document.getElementById("titlebarDrag");

  function callApi(method) {
    if (window.pywebview && window.pywebview.api && window.pywebview.api[method]) {
      window.pywebview.api[method]();
    }
  }

  if (minBtn) minBtn.onclick = () => callApi("minimize");
  if (maxBtn) maxBtn.onclick = () => callApi("toggle_maximize");
  if (closeBtn) closeBtn.onclick = () => callApi("close_window");
  if (titlebarDrag) {
    titlebarDrag.addEventListener("dblclick", () => callApi("toggle_maximize"));
  }
})();

/* ═══════════════════════════════════════════════════
   FRAMELESS WINDOW RESIZE (pywebview)
   ═══════════════════════════════════════════════════ */
(function initWindowResize() {
  const handles = Array.from(document.querySelectorAll("[data-window-resize]"));
  if (!handles.length) return;

  function hasApi() {
    return !!(window.pywebview && window.pywebview.api && window.pywebview.api.get_bounds && window.pywebview.api.set_bounds);
  }

  const MIN_W = 900;
  const MIN_H = 560;

  let dragging = false;
  let dir = "";
  let startMouseX = 0;
  let startMouseY = 0;
  let start = null;
  let rafPending = false;
  let lastBounds = null;

  function scheduleSetBounds(bounds) {
    lastBounds = bounds;
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => {
      rafPending = false;
      const b = lastBounds;
      if (!b) return;
      try {
        // Fire-and-forget to keep mousemove responsive.
        window.pywebview.api.set_bounds(b.x, b.y, b.width, b.height);
      } catch {}
    });
  }

  function clampBounds(next) {
    let { x, y, width, height } = next;
    width = Math.max(MIN_W, Math.round(width));
    height = Math.max(MIN_H, Math.round(height));
    return { x: Math.round(x), y: Math.round(y), width, height };
  }

  function computeBounds(ev) {
    const dx = ev.clientX - startMouseX;
    const dy = ev.clientY - startMouseY;

    let x = start.x;
    let y = start.y;
    let width = start.width;
    let height = start.height;

    if (dir.includes("e")) {
      width = start.width + dx;
    }
    if (dir.includes("s")) {
      height = start.height + dy;
    }
    if (dir.includes("w")) {
      width = start.width - dx;
      x = start.x + dx;
    }
    if (dir.includes("n")) {
      height = start.height - dy;
      y = start.y + dy;
    }

    // Clamp while keeping the opposite edge anchored.
    if (dir.includes("w") && width < MIN_W) {
      x = start.x + (start.width - MIN_W);
      width = MIN_W;
    }
    if (dir.includes("n") && height < MIN_H) {
      y = start.y + (start.height - MIN_H);
      height = MIN_H;
    }

    return clampBounds({ x, y, width, height });
  }

  async function onDown(e) {
    if (!hasApi()) return;
    if (e.button !== 0) return;

    dir = String(e.currentTarget?.dataset?.windowResize || "");
    if (!dir) return;

    e.preventDefault();
    e.stopPropagation();

    let bounds;
    try {
      bounds = await window.pywebview.api.get_bounds();
    } catch {
      bounds = null;
    }
    if (!bounds || !bounds.ok) return;
    if (bounds.maximized) return;

    start = {
      x: Number(bounds.x) || 0,
      y: Number(bounds.y) || 0,
      width: Number(bounds.width) || 0,
      height: Number(bounds.height) || 0,
    };
    startMouseX = e.clientX;
    startMouseY = e.clientY;

    dragging = true;
    document.body.classList.add("window-resizing");
    document.addEventListener("mousemove", onMove, true);
    document.addEventListener("mouseup", onUp, true);
  }

  function onMove(e) {
    if (!dragging || !start) return;
    e.preventDefault();
    const b = computeBounds(e);
    scheduleSetBounds(b);
  }

  function onUp() {
    if (!dragging) return;
    dragging = false;
    start = null;
    document.body.classList.remove("window-resizing");
    document.removeEventListener("mousemove", onMove, true);
    document.removeEventListener("mouseup", onUp, true);
  }

  handles.forEach((h) => h.addEventListener("mousedown", onDown, true));
})();

/* ═══════════════════════════════════════════════════
   SIDEBAR RESIZE
   ═══════════════════════════════════════════════════ */
(function initResize() {
  const LEFT_MIN = 180, LEFT_MAX = 420, LEFT_COLLAPSE = 120;
  const RIGHT_MIN = 220, RIGHT_MAX = 520, RIGHT_COLLAPSE = 150;

  function setup(handleId, sidebar, side) {
    const handle = document.getElementById(handleId);
    if (!handle) return;

    const min = side === "left" ? LEFT_MIN : RIGHT_MIN;
    const max = side === "left" ? LEFT_MAX : RIGHT_MAX;
    const collapse = side === "left" ? LEFT_COLLAPSE : RIGHT_COLLAPSE;
    const storageKeyW = side === "left" ? "marvos_left_w" : "marvos_right_w";
    const storageKeyOpen = side === "left" ? "marvos_sidebar_open" : "marvos_inspector_open";

    handle.addEventListener("mousedown", (e) => {
      if (sidebar.classList.contains("collapsed")) return;
      e.preventDefault();
      const startX = e.clientX;
      const startW = sidebar.getBoundingClientRect().width;
      document.body.classList.add("resizing");
      sidebar.classList.add("no-transition");

      const onMove = (ev) => {
        const delta = side === "left" ? ev.clientX - startX : startX - ev.clientX;
        let newW = startW + delta;

        if (newW < collapse) {
          sidebar.classList.add("collapsed");
          sidebar.style.width = "";
          handle.classList.add("hidden");
          document.body.classList.remove("resizing");
          sidebar.classList.remove("no-transition");
          localStorage.setItem(storageKeyOpen, "false");
          if (side === "right") {
            const toggle = document.getElementById("inspectorToggle");
            if (toggle) toggle.classList.add("visible");
          }
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          return;
        }

        newW = Math.max(min, Math.min(max, newW));
        sidebar.style.width = newW + "px";
      };

      const onUp = () => {
        document.body.classList.remove("resizing");
        sidebar.classList.remove("no-transition");
        if (!sidebar.classList.contains("collapsed")) {
          const w = Math.round(sidebar.getBoundingClientRect().width);
          localStorage.setItem(storageKeyW, String(w));
        }
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  }

  setup("resizeLeft", sidebarLeft, "left");
  setup("resizeRight", sidebarRight, "right");
})();

/* ═══════════════════════════════════════════════════
   INSPECTOR TOGGLE
   ═══════════════════════════════════════════════════ */
(function initInspectorToggle() {
  const toggle = document.getElementById("inspectorToggle");
  if (!toggle) return;
  // Show toggle since inspector starts collapsed
  toggle.classList.add("visible");
  toggle.onclick = () => showInspector();
})();

/* ═══════════════════════════════════════════════════
   TASK MANAGEMENT
   ═══════════════════════════════════════════════════ */
function loadTasks() {
  try {
    tasks = JSON.parse(localStorage.getItem("marvos_tasks") || "[]");
  } catch { tasks = []; }
  // Back-compat for older saved tasks.
  tasks.forEach((t) => {
    if (!Array.isArray(t.messages)) t.messages = [];
    if (!Array.isArray(t.events)) t.events = [];
  });
  if (!tasks.length) {
    createTask("New Task");
  }
  activeTaskId = activeTaskId || (tasks[0] && tasks[0].id);
}

function saveTasks() {
  try { localStorage.setItem("marvos_tasks", JSON.stringify(tasks)); } catch {}
}

function createTask(name) {
  // Persist the currently active chat before switching to a new one.
  // Otherwise, the DOM gets cleared and history appears lost when returning.
  saveCurrentChat();
  const task = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), name: name || "New Task", messages: [], events: [] };
  tasks.unshift(task);
  activeTaskId = task.id;
  saveTasks();
  renderTasks();
  chatLog.innerHTML = "";
  restoreTaskEvents();
  const presets = document.getElementById("presets");
  if (presets) presets.style.display = "";
  updateEmptyState();
  return task;
}

function deleteTask(id) {
  tasks = tasks.filter(t => t.id !== id);
  if (activeTaskId === id) {
    activeTaskId = tasks.length ? tasks[0].id : null;
    if (!activeTaskId) createTask("New Task");
    else {
      renderTasks();
      restoreTaskChat();
      restoreTaskEvents();
      return;
    }
  }
  saveTasks();
  renderTasks();
}

function switchTask(id) {
  // Save current chat to active task
  saveCurrentChat();
  activeTaskId = id;
  restoreTaskChat();
  restoreTaskEvents();
  renderTasks();
}

function saveCurrentChat() {
  const task = tasks.find(t => t.id === activeTaskId);
  if (task) {
    task.messages = Array.from(chatLog.children).map(el => ({
      role: el.classList.contains("msg--user") ? "user" : "assistant",
      text: el.querySelector(".msgBody")?.dataset?.raw || el.querySelector(".msgBody")?.textContent || ""
    }));
    saveTasks();
  }
}

function restoreTaskChat() {
  chatLog.innerHTML = "";
  const task = tasks.find(t => t.id === activeTaskId);
  if (!task) return;
  const presets = document.getElementById("presets");
  if (task.messages.length) {
    task.messages.forEach(m => appendChat(m.role, m.text));
    if (presets) presets.style.display = "none";
  } else {
    if (presets) presets.style.display = "";
  }
  updateEmptyState();
}

function renderTasks() {
  if (!taskListEl) return;
  taskListEl.innerHTML = "";
  tasks.forEach(task => {
    const item = document.createElement("div");
    item.className = "task-item" + (task.id === activeTaskId ? " active" : "");

    const icon = document.createElement("div");
    icon.className = "task-icon";
    icon.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 5h8M4 8h5M4 11h6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>';

    const name = document.createElement("span");
    name.className = "task-name";
    name.textContent = task.name;

    const del = document.createElement("button");
    del.className = "task-delete";
    del.title = "Delete chat";
    del.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M9 3h6m-8 4h10m-9 0l1 14h6l1-14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M10 11v7M14 11v7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
    del.onclick = async (e) => {
      e.stopPropagation();
      const label = task.name || "this chat";
      const ok = await showConfirmDialog({
        title: "Delete chat?",
        message: `Delete "${label}"? This cannot be undone.`,
        okText: "Delete",
      });
      if (!ok) return;
      deleteTask(task.id);
    };

    item.appendChild(icon);
    item.appendChild(name);
    item.appendChild(del);
    item.onclick = () => switchTask(task.id);
    taskListEl.appendChild(item);
  });
}

if (newTaskBtn) {
  newTaskBtn.onclick = () => createTask("New Task");
}

// Best-effort persistence if the user closes/reloads the app mid-chat.
window.addEventListener("beforeunload", () => {
  try { saveCurrentChat(); } catch {}
});

setupTheme();
renderPixelSvg(document.getElementById("marvin"), MAP, PALETTE, SCALE);
renderPixelSvg(document.getElementById("marvinHero"), MAP, PALETTE, SCALE);
