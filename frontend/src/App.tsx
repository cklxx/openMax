import { useEffect } from "react"
import { SidebarProvider, SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { Separator } from "@/components/ui/separator"
import { Button } from "@/components/ui/button"
import { AppSidebar } from "@/components/AppSidebar"
import { TaskRow } from "@/components/TaskRow"
import { TaskInput } from "@/components/TaskInput"
import { useStore, filteredTasks, taskStats } from "@/lib/store"
import { connectWS } from "@/lib/ws"
import { fetchTasks, clearDone } from "@/lib/api"

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
      <div className="text-center py-20 text-muted-foreground">
        <p className="text-sm font-medium">{tasks.length ? "Nothing here" : "No tasks yet"}</p>
        <p className="text-xs mt-1">{tasks.length ? "No tasks match this filter" : "Submit a task below to get started"}</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {running.length > 0 && (
        <Section label="Running" count={running.length}>
          {running.map((t) => <TaskRow key={t.id} task={t} />)}
        </Section>
      )}
      {queued.length > 0 && (
        <Section label="Queued" count={queued.length}>
          {queued.map((t) => <TaskRow key={t.id} task={t} />)}
        </Section>
      )}
      {done.length > 0 && (
        <Section label="Completed" count={done.length}>
          {done.map((t) => <TaskRow key={t.id} task={t} />)}
        </Section>
      )}
    </div>
  )
}

function Section({ label, count, children }: { label: string; count: number; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
        {label} ({count})
      </p>
      <div className="space-y-1">{children}</div>
    </div>
  )
}

function StatsRow() {
  const tasks = useStore((s) => s.tasks)
  const stats = taskStats(tasks)
  return (
    <div className="flex gap-8 mb-4">
      <Stat value={stats.queued} label="queued" />
      <Stat value={stats.running} label="running" accent />
      <Stat value={stats.done} label="done" success />
    </div>
  )
}

function Stat({ value, label, accent, success }: { value: number; label: string; accent?: boolean; success?: boolean }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className={`text-2xl font-semibold tabular-nums tracking-tight ${accent ? "text-primary" : success ? "text-green-600" : "text-muted-foreground"}`}>
        {value}
      </span>
      <span className="text-sm text-muted-foreground">{label}</span>
    </div>
  )
}

export default function App() {
  const filter = useStore((s) => s.filter)

  useEffect(() => {
    fetchTasks()
    connectWS()
  }, [])

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="flex items-center gap-2 px-6 py-4">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="h-4" />
          <h1 className="text-lg font-semibold tracking-tight">{TITLES[filter] ?? "All tasks"}</h1>
          <div className="flex-1" />
          <Button variant="ghost" size="sm" onClick={clearDone}>Clear done</Button>
        </header>
        <div className="px-6 pb-4 max-w-5xl">
          <StatsRow />
          <TaskList />
        </div>
        <div className="px-6 max-w-5xl">
          <TaskInput />
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}
