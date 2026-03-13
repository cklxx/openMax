from __future__ import annotations

from pathlib import Path


def test_ci_workflow_includes_headless_ci_smoke_track():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "ci-smoke:" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "OPENMAX_PANE_BACKEND: headless" in workflow
    assert "PYTHONPATH=src pytest -q tests/test_ci_smoke.py" in workflow
