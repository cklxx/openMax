import { useState } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Send } from "lucide-react"
import { submitTask } from "@/lib/api"

export function TaskInput() {
  const [value, setValue] = useState("")

  function handleSubmit() {
    if (!value.trim()) return
    submitTask(value.trim())
    setValue("")
  }

  return (
    <div className="sticky bottom-0 bg-gradient-to-b from-transparent via-background to-background pt-6 pb-5 px-1">
      <div className="flex gap-2">
        <Input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
          placeholder="What needs to be done?"
          className="shadow-sm"
        />
        <Button onClick={handleSubmit} className="shrink-0 gap-1.5">
          <Send className="h-4 w-4" />
          Submit
        </Button>
      </div>
    </div>
  )
}
