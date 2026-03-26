/* openMax Dashboard — WebSocket client + UI logic */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

let ws = null;
let tasks = [];
let currentFilter = "all";

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
    tasks = tasks.filter((t) => t.id !== id);
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

/* ── Filter ── */

function setFilter(f) {
  currentFilter = f;
  $$(".filter-tab").forEach((el) => el.classList.remove("active"));
  const btn = $$(".filter-tab").find((el) => el.textContent.toLowerCase() === f);
  if (btn) btn.classList.add("active");
  render();
}

function filterTasks(list) {
  if (currentFilter === "all") return list;
  if (currentFilter === "queued") return list.filter((t) => t.status === "queued" || t.status === "sizing");
  if (currentFilter === "running") return list.filter((t) => t.status === "running");
  if (currentFilter === "done") return list.filter((t) => t.status === "done" || t.status === "error" || t.status === "cancelled");
  return list;
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
  const filtered = filterTasks(sorted);

  const running = filtered.filter((t) => t.status === "running" || t.status === "sizing");
  const queued = filtered.filter((t) => t.status === "queued");
  const done = filtered.filter((t) => t.status === "done" || t.status === "error" || t.status === "cancelled");

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

  if (!filtered.length) {
    const msg = tasks.length ? "No tasks match this filter" : "Submit a task above to get started";
    const title = tasks.length ? "No results" : "No tasks yet";
    html = `<div class="empty-state">
      <div class="empty-icon">⚡</div>
      <h3>${title}</h3>
      <p>${msg}</p>
    </div>`;
  }

  list.innerHTML = html;
}

function taskCard(t, isExpanded) {
  const statusClass = `status-${t.status}`;
  const pct = calcProgress(t);
  const progressClass = pct >= 100 ? "done" : "";
  const label = t.task.length > 100 ? t.task.slice(0, 100) + "..." : t.task;
  const isRunning = t.status === "running";
  const glowClass = isRunning ? " running-glow" : "";
  const expandClass = isExpanded ? " expanded" : "";

  const sizes = ["small", "medium", "large"];
  const sizeHtml = `<div class="size-selector">${sizes.map((s) => {
    const active = t.size === s ? " active" : "";
    return `<button class="size-opt${active}" onclick="event.stopPropagation();setSize('${t.id}','${s}')">${s[0].toUpperCase()}</button>`;
  }).join("")}</div>`;

  const sizeClass = `badge-${t.size || "unknown"}`;
  const badgeHtml = `<span class="badge ${sizeClass}">${t.size || "..."}</span>`;

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
  const canEdit = t.status === "queued";

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
      ${badgeHtml}
      ${canEdit ? sizeHtml : ""}
      <span class="task-status ${statusClass}">${t.status}</span>
      <div class="task-actions">
        ${canEdit ? `<button class="act-btn" onclick="event.stopPropagation();editTask('${t.id}')" title="Edit">&#9998;</button>` : ""}
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

function setSize(id, size) {
  fetch(`/api/tasks/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ size }),
  }).then((r) => r.json()).then((data) => {
    const idx = tasks.findIndex((t) => t.id === id);
    if (idx >= 0) tasks[idx] = data;
    render();
  });
}

function editTask(id) {
  const card = document.querySelector(`.task-card[data-id="${id}"]`);
  if (!card) return;
  const nameEl = card.querySelector(".task-name");
  const task = tasks.find((t) => t.id === id);
  if (!task) return;

  const input = document.createElement("input");
  input.className = "task-name-input";
  input.value = task.task;
  input.onclick = (e) => e.stopPropagation();
  input.onkeydown = (e) => {
    if (e.key === "Enter") saveEdit(id, input.value);
    if (e.key === "Escape") render();
  };
  input.onblur = () => saveEdit(id, input.value);
  nameEl.replaceWith(input);
  input.focus();
  input.select();
}

function saveEdit(id, newText) {
  const text = newText.trim();
  if (!text) { render(); return; }
  fetch(`/api/tasks/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task: text }),
  }).then((r) => r.json()).then((data) => {
    const idx = tasks.findIndex((t) => t.id === id);
    if (idx >= 0) tasks[idx] = data;
    render();
  });
}

function clearDone() {
  const doneTasks = tasks.filter((t) => t.status === "done" || t.status === "error" || t.status === "cancelled");
  doneTasks.forEach((t) => {
    fetch(`/api/tasks/${t.id}`, { method: "DELETE" });
  });
  tasks = tasks.filter((t) => t.status !== "done" && t.status !== "error" && t.status !== "cancelled");
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
