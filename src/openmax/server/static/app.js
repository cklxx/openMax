/* openMax Dashboard — WebSocket client + UI logic */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

let ws = null;
let tasks = [];

/* ── WebSocket ── */

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    $(".conn-dot").classList.add("connected");
    $(".conn-label").textContent = "Live";
  };

  ws.onclose = () => {
    $(".conn-dot").classList.remove("connected");
    $(".conn-label").textContent = "Reconnecting...";
    setTimeout(connectWS, 2000);
  };

  ws.onmessage = (e) => {
    const { event, data } = JSON.parse(e.data);
    handleEvent(event, data);
  };
}

function sendWS(action, payload) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action, ...payload }));
  }
}

/* ── Event handlers ── */

function handleEvent(event, data) {
  if (event === "task_created") {
    const idx = tasks.findIndex((t) => t.id === data.id);
    if (idx === -1) tasks.push(data);
    else tasks[idx] = data;
  } else if (event === "task_updated" || event === "task_completed" || event === "task_error") {
    const idx = tasks.findIndex((t) => t.id === data.id);
    if (idx >= 0) tasks[idx] = data;
    else tasks.push(data);
  } else if (event === "task_cancelled") {
    const id = data.id || data;
    const idx = tasks.findIndex((t) => t.id === id);
    if (idx >= 0) tasks[idx].status = "cancelled";
  } else if (event === "subtask_progress") {
    updateSubtask(data);
  } else if (event === "activity") {
    appendActivity(data);
  }
  render();
}

function updateSubtask(data) {
  const task = tasks.find((t) => t.id === data.task_id);
  if (!task) return;
  if (!task.subtasks) task.subtasks = [];
  const sub = task.subtasks.find((s) => s.name === data.subtask);
  if (sub) {
    if (data.type === "done") sub.status = "done";
    if (data.data && data.data.progress_pct !== undefined) {
      sub.progress_pct = data.data.progress_pct;
    }
  }
}

function appendActivity(data) {
  const task = tasks.find((t) => t.id === data.task_id);
  if (!task) return;
  if (!task.activity) task.activity = [];
  task.activity.push(data.entry);
  if (task.activity.length > 200) task.activity = task.activity.slice(-200);
}

/* ── Render ── */

function render() {
  renderStats();
  renderTasks();
}

function renderStats() {
  const counts = { queued: 0, sizing: 0, running: 0, done: 0, error: 0 };
  tasks.forEach((t) => {
    const k = t.status in counts ? t.status : "queued";
    counts[k]++;
  });
  animateNum("stat-queued", counts.queued + counts.sizing);
  animateNum("stat-running", counts.running);
  animateNum("stat-done", counts.done);
}

function animateNum(id, target) {
  const el = document.getElementById(id);
  if (el.textContent === String(target)) return;
  el.textContent = target;
  el.style.transform = "scale(1.15)";
  setTimeout(() => (el.style.transition = "transform 0.2s", el.style.transform = ""), 50);
}

function renderTasks() {
  const list = $(".task-list");
  const expanded = new Set($$(".task-card.expanded").map((el) => el.dataset.id));
  const sorted = [...tasks].sort((a, b) => a.priority - b.priority);

  const running = sorted.filter((t) => t.status === "running" || t.status === "sizing");
  const queued = sorted.filter((t) => t.status === "queued");
  const done = sorted.filter(
    (t) => t.status === "done" || t.status === "error" || t.status === "cancelled"
  );

  let html = "";

  if (running.length) {
    html += `<div class="section-header">Running (${running.length})</div>`;
    html += running.map((t) => taskCard(t, expanded.has(t.id))).join("");
  }
  if (queued.length) {
    html += `<div class="section-header">Queued (${queued.length})</div>`;
    html += queued.map((t) => taskCard(t, expanded.has(t.id))).join("");
  }
  if (done.length) {
    html += `<div class="section-header">Completed (${done.length})</div>`;
    html += done.map((t) => taskCard(t, expanded.has(t.id))).join("");
  }

  if (!tasks.length) {
    html = `<div class="empty-state">
      <div class="empty-icon">⚡</div>
      <h3>No tasks yet</h3>
      <p>Submit a task above to get started</p>
    </div>`;
  }

  list.innerHTML = html;
}

function taskCard(t, isExpanded) {
  const statusClass = `status-${t.status}`;
  const sizeClass = `badge-${t.size || "unknown"}`;
  const pct = calcProgress(t);
  const progressClass = pct >= 100 ? "done" : "";
  const label = t.task.length > 100 ? t.task.slice(0, 100) + "..." : t.task;
  const isRunning = t.status === "running";
  const glowClass = isRunning ? " running-glow" : "";
  const expandClass = isExpanded ? " expanded" : "";

  let subtasksHtml = "";
  if (t.subtasks && t.subtasks.length) {
    subtasksHtml = '<div class="subtasks">' +
      t.subtasks.map((s) => {
        const cls = s.status === "done" ? "done" : s.status === "running" ? "running" : "pending";
        const icon = s.status === "done" ? "&#10003;" : "&#8226;";
        return `<div class="subtask">
          <span class="subtask-icon ${cls}">${icon}</span>
          <span class="subtask-name">${esc(s.name)}</span>
          <span class="${statusClass}" style="font-size:11px">${s.status}</span>
        </div>`;
      }).join("") + "</div>";
  }

  const activityHtml = renderActivity(t);

  const canCancel = t.status === "queued" || t.status === "running";
  const canAdjust = t.status === "queued";

  let progressHtml = "";
  if (isRunning) {
    const subInfo = t.subtasks && t.subtasks.length
      ? `${t.subtasks.filter(s => s.status === "done").length}/${t.subtasks.length} subtasks`
      : "processing...";
    progressHtml = `
      <div class="progress-meta"><span>${subInfo}</span><span>${pct}%</span></div>
      <div class="progress-bar"><div class="progress-fill ${progressClass}" style="width:${pct}%"></div></div>`;
  }

  return `<div class="task-card${glowClass}${expandClass}" data-id="${t.id}" onclick="toggleExpand(this)">
    <div class="task-header">
      <span class="task-name">${esc(label)}</span>
      <span class="badge ${sizeClass}">${t.size || "..."}</span>
      <span class="task-status ${statusClass}">${t.status}</span>
      <div class="task-actions">
        ${canAdjust ? `<button class="act-btn" onclick="event.stopPropagation();adjustPriority('${t.id}',-10)" title="Higher priority">&#9650;</button>` : ""}
        ${canAdjust ? `<button class="act-btn" onclick="event.stopPropagation();adjustPriority('${t.id}',10)" title="Lower priority">&#9660;</button>` : ""}
        ${canCancel ? `<button class="act-btn danger" onclick="event.stopPropagation();cancelTask('${t.id}')" title="Cancel">&#10005;</button>` : ""}
      </div>
    </div>
    ${progressHtml}
    ${subtasksHtml}
    ${activityHtml}
  </div>`;
}

function renderActivity(t) {
  const entries = t.activity || [];
  if (!entries.length) return "";
  const recent = entries.slice(-20);
  const lines = recent.map((a) => {
    const time = formatTime(a.timestamp);
    const typeClass = `log-${a.type || "info"}`;
    const src = a.source === "system" ? "sys" : a.source;
    return `<div class="log-line ${typeClass}">
      <span class="log-time">${time}</span>
      <span class="log-src">[${esc(src)}]</span>
      <span class="log-msg">${esc(a.message)}</span>
    </div>`;
  }).join("");
  return `<div class="activity-log">${lines}</div>`;
}

function formatTime(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch { return ""; }
}

function calcProgress(t) {
  if (t.status === "done") return 100;
  if (!t.subtasks || !t.subtasks.length) return t.status === "running" ? 10 : 0;
  const done = t.subtasks.filter((s) => s.status === "done").length;
  return Math.round((done / t.subtasks.length) * 100);
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

/* ── Actions ── */

function toggleExpand(el) {
  el.classList.toggle("expanded");
}

function submitTask() {
  const input = $("#task-input");
  const text = input.value.trim();
  if (!text) return;
  sendWS("submit_task", { task: text });
  input.value = "";
  input.focus();
}

function cancelTask(id) {
  sendWS("cancel_task", { task_id: id });
}

function adjustPriority(id, delta) {
  const task = tasks.find((t) => t.id === id);
  if (!task) return;
  const newP = Math.max(0, Math.min(100, task.priority + delta));
  sendWS("update_priority", { task_id: id, priority: newP });
  task.priority = newP;
  render();
}

/* ── Init ── */

async function init() {
  try {
    const resp = await fetch("/api/tasks");
    tasks = await resp.json();
  } catch (e) {
    console.warn("Failed to load tasks:", e);
  }
  render();
  connectWS();

  $("#task-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitTask();
  });
}

document.addEventListener("DOMContentLoaded", init);
