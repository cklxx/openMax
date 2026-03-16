# Experience Log

Record error patterns and successful techniques here so they survive across sessions.

## Structure

- `errors/` — Error patterns, bugs caused by recurring mistakes, postmortems
- `wins/` — Techniques, patterns, or approaches that worked well

## Entry format

Each entry is a markdown file named `YYYY-MM-DD-<slug>.md` with this structure:

```markdown
# <Title>

**Date:** YYYY-MM-DD
**Category:** <bug|architecture|performance|process>
**Severity:** <P0|P1|P2>

## What happened

<Description of the issue or success>

## Root cause / Key insight

<Why it happened, or why the technique works>

## Prevention / Reuse

<How to avoid this error in the future, or how to apply this technique>
```
