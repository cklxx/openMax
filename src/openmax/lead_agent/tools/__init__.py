"""Tools package for the lead agent MCP server."""

from __future__ import annotations

import subprocess as subprocess
import time as time

import anyio as anyio

from openmax.lead_agent.tools._dispatch import _check_budget_warning as _check_budget_warning
from openmax.lead_agent.tools._dispatch import dispatch_agent, read_pane_output, send_text_to_pane
from openmax.lead_agent.tools._helpers import _append_session_event as _append_session_event
from openmax.lead_agent.tools._helpers import _compress_context as _compress_context
from openmax.lead_agent.tools._misc import (
    ask_user,
    check_conflicts,
    read_task_report,
    remember_learning,
    run_command,
    wait_for_agent_message,
    wait_tool,
)
from openmax.lead_agent.tools._misc import find_files_tool as find_files_tool
from openmax.lead_agent.tools._misc import get_agent_recommendations as get_agent_recommendations
from openmax.lead_agent.tools._misc import grep_files_tool as grep_files_tool
from openmax.lead_agent.tools._misc import list_managed_panes as list_managed_panes
from openmax.lead_agent.tools._misc import read_file_tool as read_file_tool
from openmax.lead_agent.tools._planning import (
    check_checkpoints,
    mark_task_done,
    resolve_checkpoint,
    submit_plan,
    transition_phase,
)
from openmax.lead_agent.tools._planning import record_phase_anchor as record_phase_anchor
from openmax.lead_agent.tools._report import report_completion
from openmax.lead_agent.tools._shared import read_shared_context_tool, update_shared_context
from openmax.lead_agent.tools._verify import _sanitize_branch_name as _sanitize_branch_name
from openmax.lead_agent.tools._verify import merge_agent_branch, run_verification

ALL_TOOLS = [
    ask_user,
    check_checkpoints,
    check_conflicts,
    dispatch_agent,
    mark_task_done,
    merge_agent_branch,
    read_pane_output,
    read_shared_context_tool,
    read_task_report,
    resolve_checkpoint,
    run_command,
    run_verification,
    send_text_to_pane,
    submit_plan,
    remember_learning,
    report_completion,
    transition_phase,
    update_shared_context,
    wait_for_agent_message,
    wait_tool,
]
