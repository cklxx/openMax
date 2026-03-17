---
description: Run ruff check and format on src/ and tests/, auto-fix safe issues
allowed-tools: Bash
---

```bash
ruff check src/ tests/ --fix $ARGUMENTS
ruff format src/ tests/
```

After auto-fix, run `ruff check src/ tests/` again to surface any remaining issues that require manual attention. Report those issues — do not guess at fixes.
