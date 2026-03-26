import { LayoutGrid, Clock, ListOrdered, CheckCircle2, Zap, Circle } from "lucide-react"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuBadge,
} from "@/components/ui/sidebar"
import { useStore, taskStats, type Filter } from "@/lib/store"
import { cn } from "@/lib/utils"

const NAV_ITEMS: { id: Filter; label: string; icon: typeof LayoutGrid }[] = [
  { id: "all", label: "All tasks", icon: LayoutGrid },
  { id: "running", label: "Running", icon: Clock },
  { id: "queued", label: "Queued", icon: ListOrdered },
  { id: "done", label: "Completed", icon: CheckCircle2 },
]

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
