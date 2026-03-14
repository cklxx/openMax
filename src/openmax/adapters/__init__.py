"""Agent adapters for openMax."""

from openmax.adapters.base import AgentAdapter, AgentCommand
from openmax.adapters.claude_code import ClaudeCodeAdapter, ClaudeCodePrintAdapter
from openmax.adapters.codex_adapter import CodexAdapter, CodexExecAdapter
from openmax.adapters.opencode_adapter import OpenCodeAdapter
from openmax.adapters.subprocess_adapter import SubprocessAdapter

__all__ = [
    "AgentAdapter",
    "AgentCommand",
    "ClaudeCodeAdapter",
    "ClaudeCodePrintAdapter",
    "CodexAdapter",
    "CodexExecAdapter",
    "OpenCodeAdapter",
    "SubprocessAdapter",
]
