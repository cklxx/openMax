import { useStore, type Task } from "./store"
import { wsSend } from "./ws"

export function submitTask(text: string) {
  wsSend("submit_task", { task: text })
}

export function cancelTask(id: string) {
  wsSend("cancel_task", { task_id: id })
}

export function adjustPriority(id: string, delta: number) {
  const t = useStore.getState().tasks.find((x) => x.id === id)
  if (!t) return
  const priority = Math.max(0, Math.min(100, t.priority + delta))
  wsSend("update_priority", { task_id: id, priority })
}

export async function setSize(id: string, size: string) {
  const r = await fetch(`/api/tasks/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ size }),
  })
  if (r.ok) useStore.getState().upsertTask((await r.json()) as Task)
}

export async function saveTask(id: string, text: string) {
  if (!text.trim()) return
  const r = await fetch(`/api/tasks/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task: text.trim() }),
  })
  if (r.ok) useStore.getState().upsertTask((await r.json()) as Task)
}

export function clearDone() {
  const s = useStore.getState()
  s.tasks
    .filter((t) => ["done", "error", "cancelled"].includes(t.status))
    .forEach((t) => fetch(`/api/tasks/${t.id}`, { method: "DELETE" }))
  s.setTasks(s.tasks.filter((t) => !["done", "error", "cancelled"].includes(t.status)))
}

export async function fetchTasks() {
  try {
    const r = await fetch("/api/tasks")
    if (r.ok) useStore.getState().setTasks(await r.json())
  } catch { /* offline */ }
}
