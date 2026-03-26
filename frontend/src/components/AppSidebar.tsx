import { LayoutGrid, Clock, ListOrdered, CheckCircle2, Zap, Circle, Users } from "lucide-react"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuBadge,
  SidebarSeparator,
} from "@/components/ui/sidebar"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useStore, taskStats, type Filter } from "@/lib/store"
import { cn } from "@/lib/utils"

const NAV_ITEMS: { id: Filter; label: string; icon: typeof LayoutGrid }[] = [
  { id: "all", label: "All tasks", icon: LayoutGrid },
  { id: "running", label: "Running", icon: Clock },
  { id: "queued", label: "Queued", icon: ListOrdered },
  { id: "done", label: "Completed", icon: CheckCircle2 },
]

function EmployeeList() {
  const employees = useStore((s) => s.employees)
  if (!employees.length) return null

  return (
    <SidebarGroup>
      <SidebarGroupLabel>
        <Users className="h-3.5 w-3.5 mr-1.5" />
        Team ({employees.length})
      </SidebarGroupLabel>
      <SidebarGroupContent>
        <div className="px-2 space-y-0.5">
          {employees.map((e) => (
            <Tooltip key={e.name}>
              <TooltipTrigger>
                <div className="flex items-center gap-2 px-2 py-1.5 rounded-md text-sm hover:bg-sidebar-accent cursor-default w-full">
                  <div className="w-6 h-6 rounded-full bg-primary/10 flex items-center justify-center text-[10px] font-bold text-primary uppercase shrink-0">
                    {e.name[0]}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium truncate">{e.name}</p>
                    <p className="text-[10px] text-muted-foreground truncate">{e.role}{e.specialty ? ` · ${e.specialty}` : ""}</p>
                  </div>
                  {e.task_count > 0 && (
                    <span className="text-[10px] text-muted-foreground tabular-nums">{e.task_count}</span>
                  )}
                </div>
              </TooltipTrigger>
              <TooltipContent side="right">
                <p className="font-medium">{e.name}</p>
                <p className="text-xs text-muted-foreground">{e.role} · {e.agent_type || "claude-code"}</p>
                {e.specialty && <p className="text-xs">{e.specialty}</p>}
                <p className="text-xs text-muted-foreground">{e.task_count} tasks completed</p>
              </TooltipContent>
            </Tooltip>
          ))}
        </div>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}

export function AppSidebar() {
  const filter = useStore((s) => s.filter)
  const tasks = useStore((s) => s.tasks)
  const connected = useStore((s) => s.connected)
  const setFilter = useStore((s) => s.setFilter)
  const stats = taskStats(tasks)

  const counts: Record<Filter, number> = {
    all: tasks.length,
    running: stats.running,
    queued: stats.queued,
    done: stats.done + stats.error,
  }

  return (
    <Sidebar>
      <SidebarHeader className="px-4 py-4">
        <div className="flex items-center gap-2">
          <Zap className="h-5 w-5 text-amber-500" />
          <span className="text-base font-semibold tracking-tight">openMax</span>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Tasks</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV_ITEMS.map((item) => (
                <SidebarMenuItem key={item.id}>
                  <SidebarMenuButton
                    isActive={filter === item.id}
                    onClick={() => setFilter(item.id)}
                  >
                    <item.icon className="h-4 w-4" />
                    <span>{item.label}</span>
                  </SidebarMenuButton>
                  <SidebarMenuBadge>{counts[item.id]}</SidebarMenuBadge>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        <SidebarSeparator />
        <EmployeeList />
      </SidebarContent>
      <SidebarFooter className="px-4 py-3">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Circle className={cn("h-2 w-2 fill-current", connected ? "text-green-500" : "text-muted-foreground/40")} />
          {connected ? "Connected" : "Offline"}
        </div>
      </SidebarFooter>
    </Sidebar>
  )
}
