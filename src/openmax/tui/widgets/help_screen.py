"""Help screen modal listing all available keybindings."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP_TEXT = """\
 Keybindings
 ───────────────────────────
 q         Quit
 d         DAG view
 Tab       Focus next panel
 j / ↑     Previous task
 k / ↓     Next task
 c         Cancel selected task
 r         Retry failed task
 l         Filter logs by task
 ? / h     Toggle this help
 Esc       Close overlay
"""


class HelpScreen(ModalScreen[None]):
    """Modal overlay showing keybinding reference."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-content {
        width: 40;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $surface;
        border: solid $primary;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_help", "Close", show=False),
        Binding("question_mark", "dismiss_help", "Close", show=False),
        Binding("h", "dismiss_help", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static(_HELP_TEXT, id="help-content")

    def action_dismiss_help(self) -> None:
        self.dismiss(None)
