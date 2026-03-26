import { useEffect } from "react"
import { SidebarProvider, SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { Separator } from "@/components/ui/separator"
import { Button } from "@/components/ui/button"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AppSidebar } from "@/components/AppSidebar"
import { TaskRow } from "@/components/TaskRow"
import { TaskInput } from "@/components/TaskInput"
import { useStore, filteredTasks, taskStats, type Task } from "@/lib/store"
import { connectWS } from "@/lib/ws"
import { fetchTasks, fetchEmployees, clearDone } from "@/lib/api"
import { ListChecks, Loader2, Inbox } from "lucide-react"

const TITLES: Record<string, string> = {
  all: "All tasks",
  running: "Running",
  queued: "Queued",
  done: "Completed",
}

function TaskList() {
  const tasks = useStore((s) => s.tasks)
  const filter = useStore((s) => s.filter)
  const visible = filteredTasks(tasks, filter).sort((a, b) => a.priority - b.priority)

  const running = visible.filter((t) => t.status === "running" || t.status === "sizing")
  const queued = visible.filter((t) => t.status === "queued")
  const done = visible.filter((t) => ["done", "error", "cancelled"].includes(t.status))

  if (!visible.length) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-muted-foreground">
        <Inbox className="h-10 w-10 mb-3 opacity-30" />
        <p className="text-sm font-medium">{tasks.length ? "Nothing here" : "No tasks yet"}</p>
        <p className="text-xs mt-1 opacity-70">
          {tasks.length ? "No tasks match this filter" : "Submit a task below to get started"}
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <TaskSection label="Running" count={running.length} tasks={running} />
      <TaskSection label="Queued" count={queued.length} tasks={queued} />
      <TaskSection label="Completed" count={done.length} tasks={done} />
    </div>
  )
}

function TaskSection({ label, count, tasks }: { label: string; count: number; tasks: Task[] }) {
  if (!count) return null
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
        {label} ({count})
      </p>
      <div className="space-y-1.5">
        {tasks.map((t) => <TaskRow key={t.id} task={t} />)}
      </div>
    </div>
  )
}

function StatsBar() {
  const tasks = useStore((s) => s.tasks)
  const employees = useStore((s) => s.employees)
  const stats = taskStats(tasks)
  return (
    <div className="flex items-center gap-6 mb-5">
      <StatPill value={stats.queued} label="queued" />
      <StatPill value={stats.running} label="running" active />
      <StatPill value={stats.done} label="done" success />
      {employees.length > 0 && (
        <>
          <Separator orientation="vertical" className="h-5" />
          <StatPill value={employees.length} label="employees" />
        </>
      )}
    </div>
  )
}

function StatPill({ value, label, active, success }: { value: number; label: string; active?: boolean; success?: boolean }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className={`text-xl font-semibold tabular-nums tracking-tight ${active ? "text-primary" : success ? "text-green-600" : "text-muted-foreground"}`}>
        {value}
      </span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  )
}

export default function App() {
  const filter = useStore((s) => s.filter)
  const tasks = useStore((s) => s.tasks)
  const connected = useStore((s) => s.connected)
  const stats = taskStats(tasks)

  useEffect(() => {
    fetchTasks()
    fetchEmployees()
    connectWS()
    // Refresh employees periodically
    const id = setInterval(fetchEmployees, 30000)
    return () => clearInterval(id)
  }, [])

  return (
    <TooltipProvider>
      <SidebarProvider>
        <AppSidebar />
        <SidebarInset>
          <header className="flex items-center gap-2 px-6 py-3 border-b">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="h-4" />
            <div className="flex items-center gap-2">
              <ListChecks className="h-4 w-4 text-muted-foreground" />
              <h1 className="text-sm font-semibold tracking-tight">{TITLES[filter] ?? "All tasks"}</h1>
            </div>
            {stats.running > 0 && (
              <div className="flex items-center gap-1.5 ml-2 text-xs text-primary">
                <Loader2 className="h-3 w-3 animate-spin" />
                <span>{stats.running} active</span>
              </div>
            )}
            <div className="flex-1" />
            {!connected && (
              <span className="text-xs text-destructive">Offline</span>
            )}
            <Button variant="ghost" size="sm" className="text-xs" onClick={clearDone}>
              Clear done
            </Button>
          </header>
          <div className="flex-1 overflow-y-auto">
            <div className="max-w-4xl mx-auto px-6 py-5">
              <StatsBar />
              <TaskList />
            </div>
          </div>
          <div className="border-t max-w-4xl mx-auto w-full px-6">
            <TaskInput />
          </div>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}
