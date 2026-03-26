import { useStore } from "./store"

let ws: WebSocket | null = null

export function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:"
  ws = new WebSocket(`${proto}//${location.host}/ws`)
  ws.onopen = () => useStore.getState().setConnected(true)
  ws.onclose = () => {
    useStore.getState().setConnected(false)
    setTimeout(connectWS, 2000)
  }
  ws.onmessage = (e) => {
    const { event, data } = JSON.parse(e.data)
    handleEvent(event, data)
  }
}

function handleEvent(event: string, data: Record<string, unknown>) {
  const s = useStore.getState()
  if (["task_created", "task_updated", "task_completed", "task_error"].includes(event)) {
    s.upsertTask(data as never)
  } else if (event === "task_cancelled") {
    s.removeTask((data as { id: string }).id ?? String(data))
  } else if (event === "subtask_progress") {
    const d = data as { task_id: string; subtask: string; type: string; data: Record<string, unknown> }
    s.updateSubtask(d.task_id, d.subtask, d.type, d.data)
  } else if (event === "activity") {
    const d = data as { task_id: string; entry: { timestamp: string; source: string; message: string; type: string } }
    s.addActivity(d.task_id, d.entry)
  }
}

export function wsSend(action: string, payload: Record<string, unknown> = {}) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action, ...payload }))
}
