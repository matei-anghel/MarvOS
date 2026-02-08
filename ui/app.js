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
const visionImg = document.getElementById("visionImg");
const visionText = document.getElementById("visionText");

const sendBtn = document.getElementById("sendBtn");
const stopBtn = document.getElementById("stopBtn");
const micBtn = document.getElementById("micBtn");
const themeBtn = document.getElementById("themeBtn");
const chatInput = document.getElementById("chatInput");

const approvalModal = document.getElementById("approvalModal");
const approvalSummary = document.getElementById("approvalSummary");
const approveBtn = document.getElementById("approveBtn");
const denyBtn = document.getElementById("denyBtn");

let currentRunId = null;
let pendingApproval = null;
let recorder = null;
let chunks = [];
let streamingNode = null;

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
  if (persist) {
    try {
      localStorage.setItem("marvos_theme", theme);
    } catch {}
  }
  if (themeBtn) themeBtn.textContent = `Theme: ${theme[0].toUpperCase()}${theme.slice(1)}`;
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

const SCALE = 6;
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

function appendChat(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role === "user" ? "msg--user" : "msg--assistant"}`;

  const roleEl = document.createElement("div");
  roleEl.className = "msgRole";
  roleEl.textContent = role;

  const bodyEl = document.createElement("div");
  bodyEl.className = "msgBody msg-body";
  bodyEl.textContent = text || "";

  div.appendChild(roleEl);
  div.appendChild(bodyEl);
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return bodyEl;
}

function appendEvent(obj) {
  eventsLog.textContent += `${new Date().toLocaleTimeString()} ${JSON.stringify(obj)}\n`;
  eventsLog.scrollTop = eventsLog.scrollHeight;
}

async function startRun(text) {
  appendChat("user", text);
  statusLine.textContent = "Starting...";
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
  stopBtn.disabled = false;
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

async function resolveApproval(decision) {
  if (!pendingApproval || !currentRunId) return;
  await fetch(`/runs/${currentRunId}/approval`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tool_call_id: pendingApproval.tool_call_id, decision }),
  });
  pendingApproval = null;
  hideApprovalModal();
}

async function startDictation() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    appendChat("assistant", "Dictation not supported in this embedded browser.");
    return;
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = (e) => chunks.push(e.data);
  recorder.onstop = async () => {
    const blob = new Blob(chunks, { type: "audio/webm" });
    const form = new FormData();
    form.append("file", blob, "audio.webm");
    statusLine.textContent = "Transcribing...";
    const res = await fetch("/dictation/transcribe", { method: "POST", body: form });
    const body = await res.json();
    chatInput.value = body.text || "";
    statusLine.textContent = "Ready";
    micBtn.textContent = "Dictation";
  };
  recorder.start();
  micBtn.textContent = "Stop mic";
  statusLine.textContent = "Listening...";
}

function stopDictation() {
  if (!recorder) return;
  recorder.stop();
  recorder = null;
}

sendBtn.onclick = async () => {
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = "";
  await startRun(text);
};

stopBtn.onclick = async () => {
  await cancelRun();
  stopBtn.disabled = true;
};

micBtn.onclick = async () => {
  if (recorder) stopDictation();
  else await startDictation();
};

document.querySelectorAll("[data-preset]").forEach((btn) => {
  btn.onclick = () => {
    chatInput.value = btn.getAttribute("data-preset");
    chatInput.focus();
  };
});

const wsProto = location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${wsProto}://${location.host}/ws?session_id=${encodeURIComponent(sessionId)}`);
ws.onmessage = (evt) => {
  const msg = JSON.parse(evt.data);
  appendEvent(msg);
  const { type, payload } = msg;
  if (type === "run.status") {
    statusLine.textContent = `Run ${payload.run_id}: ${payload.status}`;
    if (payload.status === "succeeded" || payload.status === "failed" || payload.status === "canceled") {
      stopBtn.disabled = true;
    }
  }
  if (type === "assistant.final") {
    streamingNode = null;
    appendChat("assistant", payload.text || "");
  }
  if (type === "assistant.delta") {
    if (!streamingNode) {
      streamingNode = appendChat("assistant", "");
    }
    streamingNode.textContent += payload.text || "";
    chatLog.scrollTop = chatLog.scrollHeight;
  }
  if (type === "approval.requested") {
    pendingApproval = payload;
    showApprovalModal(`[${payload.risk}] ${payload.tool_name}\n\n${payload.summary}`);
  }
  if (type === "artifact.created" && payload.kind === "screenshot") {
    visionImg.src = `/artifacts/${payload.artifact_id}`;
  }
  if (type === "tool.result" && payload.tool_name === "vision.inspect_screenshot") {
    try {
      visionText.textContent = payload.result.data.text || JSON.stringify(payload.result, null, 2);
    } catch {
      visionText.textContent = JSON.stringify(payload.result);
    }
  }
};
ws.onopen = () => ws.send("hello");

approveBtn.onclick = async () => resolveApproval("approve");
denyBtn.onclick = async () => resolveApproval("deny");

setupTheme();
renderPixelSvg(document.getElementById("marvin"), MAP, PALETTE, SCALE);
