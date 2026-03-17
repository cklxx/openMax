---
description: Run the full test suite with verbose output and report failures
allowed-tools: Bash
---

Run tests and report results.

```bash
pytest tests/ -v $ARGUMENTS
```

If any tests fail:
- Show the full traceback for each failure
- Identify the root cause (not just the symptom)
- Do NOT retry blindly — diagnose first
