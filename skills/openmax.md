---
description: Dispatch a complex multi-agent task via openMax — parallel agent orchestration, verification, and reporting
allowed-tools: Bash
---

Use openMax to orchestrate the following task across multiple parallel agents:

**Task:** $ARGUMENTS

```bash
openmax run "$ARGUMENTS"
```

openMax handles the full workflow automatically:
1. **Research** — inspects the codebase for relevant files and dependencies
2. **Plan** — decomposes into parallel subtasks with narrow file scope
3. **Dispatch** — launches sub-agents (Claude Code, Codex, OpenCode) in terminal panes simultaneously
4. **Monitor** — tracks progress, intervenes on stalls or errors
5. **Verify** — runs lint + tests after all agents finish, auto-dispatches debug agent on failure
6. **Report** — summarizes what was delivered

**Useful follow-up commands:**
```bash
openmax runs                        # list recent sessions
openmax inspect <session-id>        # inspect a specific session
openmax memories                    # view learned workspace knowledge
openmax status                      # view agent subscription usage
```

Only use this skill when the task is non-trivial (multi-file, multi-module, or requires parallel work).
For single-file edits, handle directly without openmax.
