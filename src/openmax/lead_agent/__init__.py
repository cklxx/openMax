"""Lead Agent — orchestration via claude-agent-sdk with custom tools."""

from openmax.lead_agent.core import run_lead_agent
from openmax.lead_agent.formatting import _format_tool_use
from openmax.lead_agent.tools import (
    dispatch_agent,
    get_agent_recommendations,
    mark_task_done,
    read_file_tool,
    read_pane_output,
    record_phase_anchor,
    remember_learning,
    report_completion,
    send_text_to_pane,
    wait_tool,
)
from openmax.lead_agent.types import (
    LeadAgentStartupError,
    PlanResult,
    SubTask,
    TaskStatus,
)

__all__ = [
    "LeadAgentStartupError",
    "PlanResult",
    "SubTask",
    "TaskStatus",
    "_format_tool_use",
    "dispatch_agent",
    "get_agent_recommendations",
    "mark_task_done",
    "read_file_tool",
    "read_pane_output",
    "record_phase_anchor",
    "remember_learning",
    "report_completion",
    "run_lead_agent",
    "send_text_to_pane",
    "wait_tool",
]
