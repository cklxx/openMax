"""Verify all public modules import cleanly."""


def test_import_package():
    import openmax

    assert openmax.__version__


def test_import_pane_manager():
    from openmax.pane_manager import PaneManager

    assert PaneManager


def test_import_kaku():
    from openmax.kaku import ensure_kaku, is_kaku_available

    assert callable(is_kaku_available)
    assert callable(ensure_kaku)


def test_import_adapters():
    from openmax.adapters import ClaudeCodeAdapter, CodexAdapter, OpenCodeAdapter

    assert ClaudeCodeAdapter
    assert CodexAdapter
    assert OpenCodeAdapter
