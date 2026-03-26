import { useState, useRef, useEffect } from "react"
import { ChevronRight, Pencil, ArrowUp, ArrowDown, X } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { ActivityLog } from "@/components/ActivityLog"
import { adjustPriority, cancelTask, saveTask, setSize } from "@/lib/api"
import type { Task } from "@/lib/store"
import { cn } from "@/lib/utils"

const SIZE_LABELS: Record<string, string> = { small: "S", medium: "M", large: "L" }
const SIZE_VARIANTS: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  small: "secondary",
  medium: "outline",
  large: "destructive",
}

function fmtDuration(t: Task) {
  if (!t.started_at) return ""
  const start = new Date(t.started_at).getTime()
  const end = t.finished_at ? new Date(t.finished_at).getTime() : Date.now()
  const sec = Math.round((end - start) / 1000)
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m ${sec % 60}s`
}

function taskProgress(t: Task) {
  if (t.status === "done") return 100
  if (!t.subtasks?.length) return t.status === "running" ? 10 : 0
  return Math.round((t.subtasks.filter((s) => s.status === "done").length / t.subtasks.length) * 100)
}

function StatusDot({ status }: { status: string }) {
  return (
    <span className={cn(
      "w-2 h-2 rounded-full shrink-0",
      status === "queued" && "bg-muted-foreground/40",
      status === "sizing" && "bg-amber-500 animate-pulse",
      status === "running" && "bg-primary animate-pulse",
      status === "done" && "bg-green-500",
      status === "error" && "bg-destructive",
      status === "cancelled" && "bg-muted-foreground/20",
    )} />
  )
}

function SizePicker({ task }: { task: Task }) {
  if (task.status !== "queued") return null
  return (
    <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
      {(["small", "medium", "large"] as const).map((s) => (
        <button
          key={s}
          onClick={(e) => { e.stopPropagation(); setSize(task.id, s) }}
          className={cn(
            "px-1.5 py-0 text-[10px] font-semibold border rounded",
            task.size === s ? "border-primary text-primary bg-primary/5" : "border-border text-muted-foreground hover:text-foreground",
          )}
        >
          {s[0].toUpperCase()}
        </button>
      ))}
    </div>
  )
}

export function TaskRow({ task }: { task: Task }) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editValue, setEditValue] = useState(task.task)
  const [duration, setDuration] = useState(fmtDuration(task))
  const inputRef = useRef<HTMLInputElement>(null)
  const isQ = task.status === "queued"
  const canX = isQ || task.status === "running"
  const pct = taskProgress(task)

  useEffect(() => {
    if (task.status !== "running") return
    const id = setInterval(() => setDuration(fmtDuration(task)), 1000)
    return () => clearInterval(id)
  }, [task.status, task.started_at])

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  function handleEdit(e: React.MouseEvent) {
    e.stopPropagation()
    setEditValue(task.task)
    setEditing(true)
  }

  function handleSave() {
    setEditing(false)
    saveTask(task.id, editValue)
  }

  const sz = SIZE_LABELS[task.size]

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className={cn(
        "group border rounded-lg transition-all",
        task.status === "running" && "border-primary/30 shadow-[0_0_0_3px_rgba(0,0,0,0.03)]",
      )}>
        <CollapsibleTrigger className="w-full text-left">
          <div className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-muted/50">
            <StatusDot status={task.status} />
            <div className="flex-1 min-w-0">
              {editing ? (
                <Input
                  ref={inputRef}
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") handleSave(); if (e.key === "Escape") setEditing(false) }}
                  onBlur={handleSave}
                  onClick={(e) => e.stopPropagation()}
                  className="h-7 text-sm"
                />
              ) : (
                <p className="text-sm font-medium truncate">{task.task}</p>
              )}
              <div className="flex items-center gap-2 mt-0.5">
                {sz ? (
                  <Badge variant={SIZE_VARIANTS[task.size] ?? "outline"} className="text-[10px] px-1.5 py-0">
                    {sz}
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-[10px] px-1.5 py-0">...</Badge>
                )}
                <SizePicker task={task} />
                {duration && <span className="text-[11px] text-muted-foreground">{duration}</span>}
              </div>
            </div>
            <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
              {isQ && (
                <>
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={handleEdit}><Pencil className="h-3.5 w-3.5" /></Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); adjustPriority(task.id, -10) }}><ArrowUp className="h-3.5 w-3.5" /></Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); adjustPriority(task.id, 10) }}><ArrowDown className="h-3.5 w-3.5" /></Button>
                </>
              )}
              {canX && (
                <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={(e) => { e.stopPropagation(); cancelTask(task.id) }}>
                  <X className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
            <ChevronRight className={cn("h-4 w-4 text-muted-foreground transition-transform", open && "rotate-90")} />
          </div>
        </CollapsibleTrigger>

        {task.status === "running" && (
          <div className="px-4 pb-2">
            <Progress value={pct} className="h-1" />
            <div className="flex justify-between text-[11px] text-muted-foreground mt-1">
              <span>
                {task.subtasks?.length
                  ? `${task.subtasks.filter((s) => s.status === "done").length}/${task.subtasks.length}`
                  : ""}
              </span>
              <span>{pct}%</span>
            </div>
          </div>
        )}

        <CollapsibleContent>
          <div className="px-4 pb-3 space-y-2">
            {task.subtasks?.length > 0 && (
              <div className="space-y-0.5">
                {task.subtasks.map((st) => (
                  <div key={st.name} className="flex items-center gap-2 py-1 px-2 rounded text-sm hover:bg-muted/50">
                    <span className={cn(
                      "w-1.5 h-1.5 rounded-full",
                      st.status === "done" ? "bg-green-500" : st.status === "running" ? "bg-primary animate-pulse" : "bg-muted-foreground/30",
                    )} />
                    <span className="flex-1 truncate">{st.name}</span>
                    <span className={cn(
                      "text-[11px] font-medium uppercase tracking-wide",
                      st.status === "done" ? "text-green-600" : st.status === "running" ? "text-primary" : "text-muted-foreground",
                    )}>
                      {st.status}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {task.error && (
              <div className="p-2 rounded bg-destructive/5 text-destructive text-xs font-mono whitespace-pre-wrap">
                {task.error}
              </div>
            )}
            <ActivityLog entries={task.activity} />
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  )
}
