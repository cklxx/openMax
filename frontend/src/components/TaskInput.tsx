import { useState } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { ArrowUp } from "lucide-react"
import { submitTask } from "@/lib/api"

export function TaskInput() {
  const [value, setValue] = useState("")

  function handleSubmit() {
    if (!value.trim()) return
    submitTask(value.trim())
    setValue("")
  }

  return (
    <div className="py-3 flex gap-2">
      <Input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
        placeholder="Describe a task..."
        className="h-9"
      />
      <Button size="sm" onClick={handleSubmit} disabled={!value.trim()} className="h-9 px-3">
        <ArrowUp className="h-4 w-4" />
      </Button>
    </div>
  )
}
