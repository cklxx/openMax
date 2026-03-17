---
description: Bump version, update CHANGELOG, build, commit, push, and publish to PyPI
allowed-tools: Bash, Read, Edit
---

Full release workflow. $ARGUMENTS (provide target version, e.g. `0.5.6`).

1. **Lint + test first**: `ruff check src/ tests/ && ruff format src/ tests/ && pytest tests/ -v`
2. **Bump version** in both `pyproject.toml` and `src/openmax/__init__.py`
3. **Update CHANGELOG.md** — add entry under the new version with bullet points of changes
4. **Commit**: `git commit -m "chore: bump version to <version>"`
5. **Build**: `python -m build`
6. **Push**: `git push origin main`
7. **Publish**: `python -m twine upload dist/openmax-<version>*` (credentials in `~/.pypirc`)
8. Confirm upload succeeded by checking the twine output for "View at: https://pypi.org/..."
