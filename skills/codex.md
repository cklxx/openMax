---
description: Plan with Claude, execute code changes with Codex
allowed-tools: Bash, Read, Glob, Grep, mcp__openmax__execute_with_codex
---

You are in "plan-then-execute" mode. Claude plans, Codex implements.

**Task:** $ARGUMENTS

## Workflow

1. **Analyze**: Read relevant files, understand architecture and context
2. **Plan**: Determine which files to change, how, and what constraints apply
3. **Execute**: Call `execute_with_codex` with detailed implementation instructions
   - Include specific file paths and concrete change descriptions
   - Include code style constraints and testing requirements
   - For large tasks, break into multiple sequential `execute_with_codex` calls
4. **Verify**: Review changed files, run lint and tests
5. **Fix**: If issues found, call `execute_with_codex` again with fix instructions

## Guidelines

- Be specific in task descriptions — Codex works best with clear, concrete instructions
- Include file paths, function names, and expected behavior in the task
- After execution, always verify the changes make sense
- If Codex is not installed, fall back to implementing changes directly
