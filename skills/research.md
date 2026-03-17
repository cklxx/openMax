---
description: Research the codebase for a specific topic and return a structured report
allowed-tools: Bash, Glob, Grep, Read
---

Research target: **$ARGUMENTS**

Return a structured report covering:
1. **Relevant files** — paths + one-line role description
2. **Key functions/classes** — names, signatures, what they do
3. **Cross-module dependencies** — what calls what, shared state
4. **Gotchas** — anything non-obvious that would affect implementation
5. **Suggested entry point** — where to start for changes

Be specific. Do not summarize what you already know — only report what you find in the code.
Write the report to `.openmax/reports/research_$ARGUMENTS.md`.
