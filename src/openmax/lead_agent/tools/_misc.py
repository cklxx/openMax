"""Miscellaneous tools: user interaction, commands, file exploration, memory."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import tool

from openmax.lead_agent.tools._helpers import (
    _append_session_event,
    _read_subtask_report,
    _runtime,
    _safe_launch_pane,
    _tool_response,
    _upsert_subtask,
)
from openmax.lead_agent.types import SubTask, TaskStatus
from openmax.output import P, console


@tool(
    "ask_user",
    "Ask the human operator a question and wait for their answer. "
    "Use when the goal is genuinely ambiguous or you need a decision "
    "only the user can make. Do NOT use for routine confirmations. "
    "Pass choices as a list of options \u2014 the user can pick by number "
    "or type a free-form answer.",
    {"question": str, "choices": list},
)
async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    question = args["question"]
    raw_choices = args.get("choices") or []
    if isinstance(raw_choices, str):
        try:
            raw_choices = json.loads(raw_choices)
        except (json.JSONDecodeError, ValueError):
            raw_choices = [raw_choices]
    choices: list[str] = list(raw_choices)

    if runtime.dashboard is not None:
        runtime.dashboard.stop()

    console.print(f"\n  [bold yellow]?[/bold yellow]  [bold]{question}[/bold]")
    if choices:
        for i, choice in enumerate(choices, 1):
            console.print(f"    [bold]{i}.[/bold] {choice}")
        console.print("    [dim]Enter a number or type your own answer[/dim]")
    raw: str = await anyio.to_thread.run_sync(lambda: input("Your answer: "))
    raw = raw.strip()

    answer = raw
    if choices and raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(choices):
            answer = choices[idx]
            console.print(f"  [dim]\u2192 {answer}[/dim]")

    if runtime.dashboard is not None:
        runtime.dashboard.start()

    _append_session_event(
        "tool.ask_user", {"question": question, "choices": choices, "answer": answer}
    )
    return _tool_response(answer)


@tool(
    "wait",
    "Wait for a specified number of seconds before continuing. "
    "Use between monitoring rounds: 10-15s for simple tasks, 30-45s for complex ones. "
    "Increase if the agent is making steady progress.",
    {"seconds": int},
)
async def wait_tool(args: dict[str, Any]) -> dict[str, Any]:
    seconds = min(max(args.get("seconds", 30), 5), 120)
    await anyio.sleep(seconds)
    return _tool_response(f"Waited {seconds}s")


@tool(
    "run_command",
    "Run a CLI command in a terminal pane. For build/test/git/servers — "
    "NOT for code exploration (dispatch agents for that). "
    "Set interactive=true for long-running programs, false (default) for one-shot.",
    {
        "command": str,
        "task_name": str,
        "interactive": bool,
    },
)
async def run_command(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    command_str = args["command"]
    task_name = args.get("task_name", command_str[:40])
    interactive = args.get("interactive", False)

    import shlex

    try:
        cmd_list = shlex.split(command_str)
    except ValueError:
        cmd_list = [command_str]

    if not cmd_list:
        return _tool_response("Error: empty command")

    pane, launch_err = _safe_launch_pane(
        runtime,
        command=cmd_list,
        purpose=task_name,
        agent_type="command",
        title=f"openMax: {runtime.plan.goal[:40]}",
    )
    if pane is None:
        console.print(f"  [bold red]\u2717[/bold red]  run_command failed: {launch_err}")
        return _tool_response({"status": "error", "error": launch_err})

    subtask = SubTask(
        name=task_name,
        agent_type="command",
        prompt=command_str,
        status=TaskStatus.RUNNING,
        pane_id=pane.pane_id,
    )
    _upsert_subtask(subtask)
    if runtime.dashboard is not None:
        runtime.dashboard.update_subtask(task_name, "command", pane.pane_id, "running")

    win = runtime.pane_mgr.windows.get(runtime.agent_window_id)
    pane_count = len(win.pane_ids) if win else 1
    console.print(
        f"  [bold cyan]{P}[/bold cyan]  [bold]{command_str[:60]}[/bold] \u2192 pane {pane.pane_id}"
    )
    _append_session_event(
        "tool.run_command",
        {
            "command": command_str,
            "task_name": task_name,
            "interactive": interactive,
            "pane_id": pane.pane_id,
            "window_id": runtime.agent_window_id,
        },
    )
    return _tool_response(
        {
            "status": "launched",
            "pane_id": pane.pane_id,
            "window_id": runtime.agent_window_id,
            "command": command_str,
            "task_name": task_name,
            "interactive": interactive,
            "panes_in_window": pane_count,
        }
    )


@tool(
    "remember_learning",
    "Store a reusable lesson so future runs in this workspace can improve automatically.",
    {"lesson": str, "rationale": str, "confidence": int},
)
async def remember_learning(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    lesson = args["lesson"]
    rationale = args.get("rationale", "")
    confidence = args.get("confidence")
    if runtime.memory_store is None:
        return _tool_response("Memory store unavailable")
    runtime.memory_store.record_lesson(
        cwd=runtime.cwd,
        task=runtime.plan.goal,
        lesson=lesson,
        rationale=rationale,
        confidence=confidence,
    )
    return _tool_response("Stored reusable lesson")


@tool(
    "check_conflicts",
    "Check for git conflicts and untracked files in the working directory.",
    {},
)
async def check_conflicts(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    cwd = runtime.cwd

    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        status_output = status_result.stdout
    except (subprocess.TimeoutExpired, OSError) as e:
        return _tool_response(
            {
                "conflict": False,
                "details": f"Error running git status: {e}",
                "untracked_files": [],
            }
        )

    try:
        diff_result = subprocess.run(
            ["git", "diff", "--check"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        diff_check_failed = diff_result.returncode != 0
        diff_output = diff_result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        diff_check_failed = False
        diff_output = ""

    untracked_files: list[str] = []
    conflict_markers = False
    for line in status_output.splitlines():
        if line.startswith("??"):
            untracked_files.append(line[3:].strip())
        elif line[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD"):
            conflict_markers = True

    has_conflict = diff_check_failed or conflict_markers
    if has_conflict:
        details = diff_output if diff_output else "Conflict markers detected in git status"
    else:
        details = "No conflicts detected"
    result = {"conflict": has_conflict, "details": details, "untracked_files": untracked_files}

    if has_conflict:
        console.print("  [bold red]\u2717[/bold red]  conflicts found")
    else:
        console.print("  [bold green]\u2713[/bold green]  no conflicts")
    _append_session_event("tool.check_conflicts", result)
    return _tool_response(result)


@tool(
    "get_agent_recommendations",
    "Get ranked agent recommendations for a task based on workspace memory and similar code work.",
    {"task": str},
)
async def get_agent_recommendations(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    task = args["task"]
    if runtime.memory_store is None:
        return _tool_response("[]")
    rankings = runtime.memory_store.derive_agent_rankings(cwd=runtime.cwd, task=task)
    payload = [
        {"agent_type": item.agent_type, "score": item.score, "reasons": item.reasons}
        for item in rankings
    ]
    return _tool_response(payload)


@tool(
    "list_managed_panes",
    "List all managed panes and their current states.",
    {},
)
async def list_managed_panes(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    runtime.pane_mgr.refresh_states()
    summary = runtime.pane_mgr.summary()
    return _tool_response(summary)


@tool(
    "read_task_report",
    "Read a sub-agent's completion report file. Returns the structured report "
    "if the agent wrote one, or null if no report exists yet.",
    {"task_name": str},
)
async def read_task_report(args: dict[str, Any]) -> dict[str, Any]:
    task_name = args["task_name"]
    report = _read_subtask_report(task_name)
    if report is None:
        return _tool_response({"task_name": task_name, "report": None})
    return _tool_response({"task_name": task_name, "report": report[:4000]})


@tool(
    "find_files",
    "Search for files by glob pattern in the working directory. "
    "Returns matching file paths instantly (no pane needed). Max 200 results. "
    "Examples: '**/*.md', 'src/**/*.py', '**/roadmap*', 'docs/**'.",
    {"pattern": str, "path": str},
)
async def find_files_tool(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    pattern = args["pattern"]
    rel_path = args.get("path", ".")

    target_dir = (Path(runtime.cwd) / rel_path).resolve()
    cwd_resolved = Path(runtime.cwd).resolve()
    if not str(target_dir).startswith(str(cwd_resolved)):
        return _tool_response("Error: path outside working directory")

    try:
        matches = sorted(target_dir.glob(pattern))
    except Exception as e:
        return _tool_response(f"Error: {e}")

    filtered = [
        m
        for m in matches
        if not any(part.startswith(".") for part in m.relative_to(cwd_resolved).parts)
        and "__pycache__" not in str(m)
    ][:200]

    rel_paths = [str(m.relative_to(cwd_resolved)) for m in filtered]
    result = f"Found {len(rel_paths)} file(s):\n" + "\n".join(rel_paths)
    console.print(f"  [dim]{P}  find '{pattern}' \u2192 {len(rel_paths)} file(s)[/dim]")
    return _tool_response(result)


@tool(
    "grep_files",
    "Search file contents for a regex pattern. Returns matching lines with "
    "file paths and line numbers instantly (no pane needed). Max 100 matches. "
    "Use glob param to filter files (e.g. '**/*.py').",
    {"pattern": str, "glob": str, "max_results": int},
)
async def grep_files_tool(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    pattern = args["pattern"]
    file_glob = args.get("glob", "**/*")
    max_results = min(args.get("max_results", 100), 200)

    import re

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return _tool_response(f"Error: invalid regex: {e}")

    cwd_resolved = Path(runtime.cwd).resolve()
    matches: list[str] = []

    try:
        for filepath in sorted(cwd_resolved.glob(file_glob)):
            if not filepath.is_file():
                continue
            if any(part.startswith(".") for part in filepath.relative_to(cwd_resolved).parts):
                continue
            if "__pycache__" in str(filepath):
                continue
            try:
                text = filepath.read_text(errors="strict")
            except (UnicodeDecodeError, OSError):
                continue
            rel = str(filepath.relative_to(cwd_resolved))
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{rel}:{i}: {line.rstrip()}")
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
    except Exception as e:
        return _tool_response(f"Error: {e}")

    result = (
        f"No matches for pattern '{pattern}'"
        if not matches
        else f"Found {len(matches)} match(es):\n" + "\n".join(matches)
    )
    console.print(f"  [dim]{P}  grep '{pattern}' \u2192 {len(matches)} match(es)[/dim]")
    return _tool_response(result)


@tool(
    "read_file",
    "Read a file from the working directory. Returns file content instantly "
    "(no pane needed, max 2000 lines). Specify offset/limit for large files.",
    {"path": str, "offset": int, "limit": int},
)
async def read_file_tool(args: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime()
    rel_path = args["path"]
    offset = args.get("offset", 0)
    limit = args.get("limit", 2000)

    target = (Path(runtime.cwd) / rel_path).resolve()
    cwd_resolved = Path(runtime.cwd).resolve()
    if not str(target).startswith(str(cwd_resolved)):
        return _tool_response("Error: path outside working directory")

    try:
        text = target.read_text(errors="replace")
    except FileNotFoundError:
        return _tool_response(f"Error: file not found: {rel_path}")
    except IsADirectoryError:
        return _tool_response(f"Error: {rel_path} is a directory")
    except OSError as e:
        return _tool_response(f"Error reading file: {e}")

    lines = text.splitlines()
    total = len(lines)
    selected = lines[offset : offset + limit]
    numbered = [f"{i + offset + 1:>5}  {line}" for i, line in enumerate(selected)]
    header = f"# {rel_path} ({total} lines total"
    if offset:
        header += f", showing from line {offset + 1}"
    header += ")\n"
    result = header + "\n".join(numbered)

    console.print(f"  [dim]{P}  read {rel_path} ({len(selected)}/{total} lines)[/dim]")
    return _tool_response(result)
