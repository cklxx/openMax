"""Tests for the startup banner renderer."""

from __future__ import annotations

from openmax.banner import render_banner


def test_render_banner_basic():
    lines = render_banner()
    assert len(lines) == 2
    assert "openMax" in lines[0].plain
    assert "multi-agent orchestration" in lines[1].plain


def test_render_banner_with_session():
    lines = render_banner(session_id="abc-123", resume=True)
    plain = lines[0].plain
    assert "session abc-123" in plain
    assert "resume" in plain


def test_render_banner_with_task_count():
    lines = render_banner(task_count=5)
    assert "5 tasks" in lines[0].plain


def test_render_banner_version():
    from openmax import __version__

    lines = render_banner()
    assert __version__ in lines[0].plain
