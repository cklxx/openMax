/* openMax Dashboard */

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

let ws = null;
let tasks = [];
let filter = "all";

/* ── WebSocket ── */

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => { $(".conn-dot").classList.add("connected"); $(".conn-label").textContent = "Connected"; };
  ws.onclose = () => { $(".conn-dot").classList.remove("connected"); $(".conn-label").textContent = "Offline"; setTimeout(connectWS, 2000); };
  ws.onmessage = (e) => { const { event, data } = JSON.parse(e.data); onEvent(event, data); };
}

function send(action, payload) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action, ...payload }));
}

/* ── Events ── */

function onEvent(ev, d) {
  if (ev === "task_created") { upsert(d); }
  else if (ev === "task_updated" || ev === "task_completed" || ev === "task_error") { upsert(d); }
  else if (ev === "task_cancelled") { tasks = tasks.filter((t) => t.id !== (d.id || d)); }
  else if (ev === "subtask_progress") { onSubtask(d); }
  else if (ev === "activity") { onActivity(d); }
  render();
}

function upsert(d) {
  const i = tasks.findIndex((t) => t.id === d.id);
  if (i >= 0) tasks[i] = d; else tasks.push(d);
}

function onSubtask(d) {
  const t = tasks.find((t) => t.id === d.task_id);
  if (!t) return;
  const s = (t.subtasks || []).find((s) => s.name === d.subtask);
  if (s) {
    if (d.type === "done") s.status = "done";
    if (d.data?.progress_pct !== undefined) s.progress_pct = d.data.progress_pct;
  }
}

function onActivity(d) {
  const t = tasks.find((t) => t.id === d.task_id);
  if (!t) return;
  if (!t.activity) t.activity = [];
  t.activity.push(d.entry);
  if (t.activity.length > 200) t.activity = t.activity.slice(-200);
  // Auto-scroll activity log if visible
  const logEl = document.querySelector(`.task-row[data-id="${d.task_id}"] .activity`);
  if (logEl) requestAnimationFrame(() => { logEl.scrollTop = logEl.scrollHeight; });
}

/* ── Filter ── */

function setFilter(f) {
  filter = f;
  $$(".nav-item").forEach((el) => el.classList.remove("active"));
  const titles = { all: "All tasks", running: "Running", queued: "Queued", done: "Completed" };
  $$(".nav-item").forEach((el) => { if (el.textContent.trim().toLowerCase().includes(f === "all" ? "all" : f)) el.classList.add("active"); });
  $("#page-title").textContent = titles[f] || "All tasks";
  render();
}

function filtered() {
  if (filter === "all") return tasks;
  if (filter === "queued") return tasks.filter((t) => t.status === "queued" || t.status === "sizing");
  if (filter === "running") return tasks.filter((t) => t.status === "running");
  return tasks.filter((t) => t.status === "done" || t.status === "error" || t.status === "cancelled");
}

/* ── Render ── */

function render() {
  const c = { queued: 0, sizing: 0, running: 0, done: 0, error: 0 };
  tasks.forEach((t) => { c[t.status in c ? t.status : "queued"]++; });

  setText("stat-queued", c.queued + c.sizing);
  setText("stat-running", c.running);
  setText("stat-done", c.done);
  setText("nav-all", tasks.length);
  setText("nav-running", c.running);
  setText("nav-queued", c.queued + c.sizing);
  setText("nav-done", c.done + c.error);

  const expanded = new Set($$(".task-row.expanded").map((el) => el.dataset.id));
  const sorted = [...filtered()].sort((a, b) => a.priority - b.priority);
  const run = sorted.filter((t) => t.status === "running" || t.status === "sizing");
  const que = sorted.filter((t) => t.status === "queued");
  const don = sorted.filter((t) => t.status === "done" || t.status === "error" || t.status === "cancelled");

  let h = "";
  if (run.length) { h += sec("Running", run.length); h += run.map((t) => row(t, expanded.has(t.id))).join(""); }
  if (que.length) { h += sec("Queued", que.length); h += que.map((t) => row(t, expanded.has(t.id))).join(""); }
  if (don.length) { h += sec("Completed", don.length); h += don.map((t) => row(t, expanded.has(t.id))).join(""); }

  if (!sorted.length) {
    const msg = tasks.length ? "No tasks match this filter" : "What needs to be done?";
    h = `<div class="empty"><h3>${tasks.length ? "Nothing here" : "No tasks yet"}</h3><p>${msg}</p></div>`;
  }
  $(".task-list").innerHTML = h;
}

function setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
function sec(label, n) { return `<div class="section-label">${label} (${n})</div>`; }

function row(t, open) {
  const glow = t.status === "running" ? " glow" : "";
  const exp = open ? " expanded" : "";
  const lbl = t.task.length > 120 ? t.task.slice(0, 120) + "..." : t.task;
  const sz = { small: "S", medium: "M", large: "L" }[t.size] || "";
  const szTag = sz ? `<span class="tag tag-${sz.toLowerCase()}">${sz}</span>` : `<span class="tag tag-u">...</span>`;
  const pct = progress(t);
  const isQ = t.status === "queued";
  const canX = isQ || t.status === "running";
  const dur = fmtDuration(t);

  let picker = "";
  if (isQ) {
    picker = `<div class="size-picker">${["small","medium","large"].map((s) =>
      `<button class="size-btn${t.size === s ? " on" : ""}" onclick="event.stopPropagation();setSize('${t.id}','${s}')">${s[0].toUpperCase()}</button>`
    ).join("")}</div>`;
  }

  let prog = "";
  if (t.status === "running") {
    const info = t.subtasks?.length ? `${t.subtasks.filter((s) => s.status === "done").length}/${t.subtasks.length}` : "";
    prog = `<div class="task-progress">
      <div class="progress-track"><div class="progress-bar${pct >= 100 ? " complete" : ""}" style="width:${pct}%"></div></div>
      <div class="progress-info"><span>${info}</span><span>${pct}%</span></div>
    </div>`;
  }

  let detail = '<div class="task-detail-inner">';

  // Subtasks
  if (t.subtasks?.length) {
    detail += `<div class="subtask-list">${t.subtasks.map((s) => {
      const c = s.status === "done" ? "done" : s.status === "running" ? "running" : "pending";
      return `<div class="subtask-item">
        <span class="subtask-dot ${c}"></span>
        <span class="subtask-name">${esc(s.name)}</span>
        <span class="subtask-status ${c}">${s.status}</span>
      </div>`;
    }).join("")}</div>`;
  }

  // Error
  if (t.error) {
    detail += `<div class="task-error">${esc(t.error)}</div>`;
  }

  // Activity log
  if (t.activity?.length) {
    detail += `<div class="activity-section">
      <div class="activity-header">Activity log</div>
      <div class="activity">${t.activity.slice(-30).map((a) => {
        const cls = a.type === "done" ? " log-done" : a.type === "error" ? " log-error" : "";
        const whoCls = a.source === "system" || a.source === "sys" ? " log-sys" : "";
        return `<div class="log-row${cls}"><span class="log-ts">${fmtTime(a.timestamp)}</span><span class="log-who${whoCls}">${esc(a.source === "system" ? "sys" : a.source)}</span><span class="log-what">${esc(a.message)}</span></div>`;
      }).join("")}</div>
    </div>`;
  }
  detail += "</div>";

  const chevron = `<span class="task-chevron"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 18l6-6-6-6"/></svg></span>`;

  return `<div class="task-row${glow}${exp}" data-id="${t.id}" onclick="toggle(this)">
    <div class="task-header">
      <span class="task-indicator ind-${t.status}"></span>
      <div class="task-body">
        <div class="task-title">${esc(lbl)}</div>
        <div class="task-meta">${szTag} ${picker} ${dur ? `<span class="task-duration">${dur}</span>` : ""}</div>
      </div>
      <div class="task-actions">
        ${isQ ? `<button class="icon-btn" onclick="event.stopPropagation();editTask('${t.id}')" title="Edit">&#9998;</button>` : ""}
        ${isQ ? `<button class="icon-btn" onclick="event.stopPropagation();adjustP('${t.id}',-10)" title="Up">&#9650;</button>` : ""}
        ${isQ ? `<button class="icon-btn" onclick="event.stopPropagation();adjustP('${t.id}',10)" title="Down">&#9660;</button>` : ""}
        ${canX ? `<button class="icon-btn danger" onclick="event.stopPropagation();cancelTask('${t.id}')" title="Cancel">&#10005;</button>` : ""}
      </div>
      ${chevron}
    </div>
    ${prog}
    <div class="task-detail">${detail}</div>
  </div>`;
}

function progress(t) {
  if (t.status === "done") return 100;
  if (!t.subtasks?.length) return t.status === "running" ? 10 : 0;
  return Math.round(t.subtasks.filter((s) => s.status === "done").length / t.subtasks.length * 100);
}

function fmtTime(ts) {
  if (!ts) return "";
  try { return new Date(ts).toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
  catch { return ""; }
}

function fmtDuration(t) {
  if (!t.started_at) return "";
  const start = new Date(t.started_at).getTime();
  const end = t.finished_at ? new Date(t.finished_at).getTime() : Date.now();
  const sec = Math.round((end - start) / 1000);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m ${s}s`;
}

function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

/* ── Actions ── */

function toggle(el) { el.classList.toggle("expanded"); }

function submitTask() {
  const i = $("#task-input"); const t = i.value.trim();
  if (!t) return;
  send("submit_task", { task: t }); i.value = ""; i.focus();
}

function cancelTask(id) { send("cancel_task", { task_id: id }); }

function adjustP(id, delta) {
  const t = tasks.find((x) => x.id === id);
  if (!t) return;
  t.priority = Math.max(0, Math.min(100, t.priority + delta));
  send("update_priority", { task_id: id, priority: t.priority });
  render();
}

function setSize(id, size) {
  fetch(`/api/tasks/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ size }) })
    .then((r) => r.json()).then((d) => { upsert(d); render(); });
}

function editTask(id) {
  const el = document.querySelector(`.task-row[data-id="${id}"] .task-title`);
  const t = tasks.find((x) => x.id === id);
  if (!el || !t) return;
  const inp = document.createElement("input");
  inp.className = "edit-input";
  inp.value = t.task;
  inp.onclick = (e) => e.stopPropagation();
  inp.onkeydown = (e) => { if (e.key === "Enter") save(id, inp.value); if (e.key === "Escape") render(); };
  inp.onblur = () => save(id, inp.value);
  el.replaceWith(inp); inp.focus(); inp.select();
}

function save(id, text) {
  if (!text.trim()) { render(); return; }
  fetch(`/api/tasks/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ task: text.trim() }) })
    .then((r) => r.json()).then((d) => { upsert(d); render(); });
}

function clearDone() {
  tasks.filter((t) => t.status === "done" || t.status === "error" || t.status === "cancelled")
    .forEach((t) => fetch(`/api/tasks/${t.id}`, { method: "DELETE" }));
  tasks = tasks.filter((t) => t.status !== "done" && t.status !== "error" && t.status !== "cancelled");
  render();
}

/* ── Live duration ticker ── */
setInterval(() => {
  $$(".task-row.glow .task-duration").forEach((el) => {
    const id = el.closest(".task-row")?.dataset.id;
    const t = tasks.find((x) => x.id === id);
    if (t) el.textContent = fmtDuration(t);
  });
}, 1000);

/* ── Init ── */

async function init() {
  try { tasks = await (await fetch("/api/tasks")).json(); } catch {}
  render();
  connectWS();
  $("#task-input").addEventListener("keydown", (e) => { if (e.key === "Enter") submitTask(); });
}

document.addEventListener("DOMContentLoaded", init);
