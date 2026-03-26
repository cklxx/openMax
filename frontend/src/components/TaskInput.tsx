import { useState, useRef, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { ArrowUp } from "lucide-react"
import { submitTask } from "@/lib/api"

export function TaskInput() {
  const [value, setValue] = useState("")
  const ref = useRef<HTMLTextAreaElement>(null)

  function handleSubmit() {
    if (!value.trim()) return
    submitTask(value.trim())
    setValue("")
    if (ref.current) ref.current.style.height = "auto"
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = Math.min(el.scrollHeight, 200) + "px"
  }, [value])

  return (
    <div className="py-3">
      <div className="relative rounded-2xl border bg-background shadow-sm focus-within:ring-2 focus-within:ring-ring/20 focus-within:border-ring/40 transition-all">
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Describe a task..."
          rows={1}
          className="w-full resize-none bg-transparent px-4 pt-3 pb-10 text-sm outline-none placeholder:text-muted-foreground/50 min-h-[52px] max-h-[200px]"
        />
        <div className="absolute right-2 bottom-2 flex items-center gap-1.5">
          <span className="text-[10px] text-muted-foreground/40 mr-1 select-none">
            Shift+Enter for newline
          </span>
          <Button
            size="icon"
            onClick={handleSubmit}
            disabled={!value.trim()}
            className="h-7 w-7 rounded-lg"
          >
            <ArrowUp className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
