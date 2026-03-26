import { useEffect, useRef } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { ActivityEntry } from "@/lib/store"
import { cn } from "@/lib/utils"

function fmtTime(ts: string) {
  try {
    return new Date(ts).toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })
  } catch {
    return ""
  }
}

export function ActivityLog({ entries }: { entries: ActivityEntry[] }) {
  const endRef = useRef<HTMLDivElement>(null)
  const shown = entries.slice(-40)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [shown.length])

  if (!shown.length) return null

  return (
    <div className="mt-2">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-1">
        Activity
      </p>
      <ScrollArea className="h-[220px] rounded-md border bg-muted/30 p-2 font-mono text-[11px] leading-relaxed">
        {shown.map((a, i) => (
          <div key={i} className="flex gap-2">
            <span className="text-muted-foreground/60 shrink-0 w-[58px]">{fmtTime(a.timestamp)}</span>
            <span className={cn(
              "shrink-0 w-[80px] truncate font-medium",
              a.source === "system" ? "text-muted-foreground" : "text-primary",
            )}>
              {a.source === "system" ? "sys" : a.source}
            </span>
            <span className={cn(
              "truncate",
              a.type === "done" && "text-green-600 font-medium",
              a.type === "error" && "text-destructive font-medium",
            )}>
              {a.message}
            </span>
          </div>
        ))}
        <div ref={endRef} />
      </ScrollArea>
    </div>
  )
}
