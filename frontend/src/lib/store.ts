import { create } from "zustand"

export type TaskStatus = "queued" | "sizing" | "running" | "done" | "error" | "cancelled"
export type TaskSize = "unknown" | "small" | "medium" | "large"

export interface SubtaskInfo {
  name: string
  status: string
  progress_pct: number
  agent_type: string
}

export interface ActivityEntry {
  timestamp: string
  source: string
  message: string
  type: string
}

export interface Task {
  id: string
  task: string
  status: TaskStatus
  priority: number
  size: TaskSize
  size_confidence: number
  size_override: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
  session_id: string | null
  subtasks: SubtaskInfo[]
  activity: ActivityEntry[]
  cwd: string
  error: string | null
}

export interface Employee {
  name: string
  role: string
  specialty: string
  agent_type: string
  task_count: number
  created: string
}

export type Filter = "all" | "running" | "queued" | "done"

interface Store {
  tasks: Task[]
  employees: Employee[]
  filter: Filter
  connected: boolean
  setFilter: (f: Filter) => void
  setConnected: (v: boolean) => void
  setTasks: (tasks: Task[]) => void
  setEmployees: (employees: Employee[]) => void
  upsertTask: (task: Task) => void
  removeTask: (id: string) => void
  addActivity: (taskId: string, entry: ActivityEntry) => void
  updateSubtask: (taskId: string, subtask: string, type: string, data: Record<string, unknown>) => void
}

export const useStore = create<Store>((set) => ({
  tasks: [],
  employees: [],
  filter: "all",
  connected: false,
  setEmployees: (employees) => set({ employees }),
  setFilter: (filter) => set({ filter }),
  setConnected: (connected) => set({ connected }),
  setTasks: (tasks) => set({ tasks }),
  upsertTask: (task) =>
    set((s) => {
      const i = s.tasks.findIndex((t) => t.id === task.id)
      const next = [...s.tasks]
      if (i >= 0) next[i] = { ...next[i], ...task, activity: task.activity ?? next[i].activity }
      else next.push(task)
      return { tasks: next }
    }),
  removeTask: (id) => set((s) => ({ tasks: s.tasks.filter((t) => t.id !== id) })),
  addActivity: (taskId, entry) =>
    set((s) => ({
      tasks: s.tasks.map((t) =>
        t.id === taskId
          ? { ...t, activity: [...t.activity, entry].slice(-200) }
          : t,
      ),
    })),
  updateSubtask: (taskId, subtask, type, data) =>
    set((s) => ({
      tasks: s.tasks.map((t) => {
        if (t.id !== taskId) return t
        return {
          ...t,
          subtasks: t.subtasks.map((st) =>
            st.name === subtask
              ? {
                  ...st,
                  status: type === "done" ? "done" : st.status,
                  progress_pct: (data?.progress_pct as number) ?? st.progress_pct,
                }
              : st,
          ),
        }
      }),
    })),
}))

export function filteredTasks(tasks: Task[], filter: Filter): Task[] {
  if (filter === "all") return tasks
  if (filter === "queued") return tasks.filter((t) => t.status === "queued" || t.status === "sizing")
  if (filter === "running") return tasks.filter((t) => t.status === "running")
  return tasks.filter((t) => ["done", "error", "cancelled"].includes(t.status))
}

export function taskStats(tasks: Task[]) {
  const c = { queued: 0, running: 0, done: 0, error: 0 }
  for (const t of tasks) {
    if (t.status === "queued" || t.status === "sizing") c.queued++
    else if (t.status === "running") c.running++
    else if (t.status === "done") c.done++
    else if (t.status === "error") c.error++
  }
  return c
}
