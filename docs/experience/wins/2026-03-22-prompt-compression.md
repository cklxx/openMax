# Lead Agent Prompt Compression & Architecture Cleanup

**Date:** 2026-03-22
**Category:** optimization
**Severity:** P1

## What happened

Performed a 5-round iterative optimization of the openMax lead agent system:

1. **Prompt deduplication**: 263→196 lines (25% reduction). Merged 4x duplicate "dispatch_agent only" rules, deduplicated §3 conditional triggers vs §2 workflow, unified §4 Agent Types + §4.5 Agent Roles into one section.

2. **Gap filling**: Added "Output but no commit" and "Unresponsive >5min" monitor signals. Registered `find_files`, `grep_files`, `read_file` into ALL_TOOLS (were implemented but never registered). Updated prompt to reflect lead agent's actual file exploration capability.

3. **Decision framework**: Added explicit reversible/irreversible decision authority matrix. Removed contradictions ("Prefer completeness" vs "Act, don't narrate"; "Call wait" vs "use wait_for_agent_message").

4. **Tool description optimization**: Shortened all 22 MCP tool descriptions by ~20%, removing information the model can infer from parameter names.

5. **Module extraction**: `_error_context.py` (66 lines) from `_dispatch.py` (596→529), `_branch.py` (139 lines) from `_verify.py` (571→441).

## Key insight

Prompt token budget is the scarcest resource in an agent orchestration system. Every redundant line in the system prompt and every verbose tool description consumes tokens on every turn. The 25% prompt reduction + 20% tool description reduction compounds across an entire session.

Registering existing-but-unused tools (`find_files`/`grep_files`/`read_file`) eliminates unnecessary research agent dispatches for simple queries — saving full agent spawn overhead.

## Reuse

Apply this pattern periodically: audit prompt for redundancy, check tool registry completeness, measure description token cost. The "prompt as code" mindset — refactor, deduplicate, test — yields compounding efficiency gains.
