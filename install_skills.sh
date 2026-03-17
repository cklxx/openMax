#!/usr/bin/env bash
# Install openMax as a reusable skill for AI coding agents.
#
# Usage:
#   ./install_skills.sh              # Claude Code: current project (.claude/commands/)
#   ./install_skills.sh --global     # Claude Code: all projects (~/.claude/commands/)
#   ./install_skills.sh --all        # Both project + global
#
# After install, invoke in Claude Code with:   /openmax "your task here"
# Codex / OpenCode: no native skill system — see instructions printed by --codex flag.
#   ./install_skills.sh --codex

set -euo pipefail

SKILL_FILE="$(cd "$(dirname "$0")/skills" && pwd)/openmax.md"
PROJECT_COMMANDS=".claude/commands"
GLOBAL_COMMANDS="$HOME/.claude/commands"

install_to() {
    local target="$1"
    mkdir -p "$target"
    ln -sf "$SKILL_FILE" "$target/openmax.md"
    echo "Installed: $target/openmax.md -> $SKILL_FILE"
    echo "Invoke in Claude Code: /openmax \"your task\""
}

print_codex_instructions() {
    cat <<'EOF'

=== Codex / OpenCode — no native skill system ===

Prepend the skill content to your task prompt:

  # Codex
  codex "$(cat skills/openmax.md | grep -v '^---' | grep -v '^description:')

  Task: fix the auth bug in src/api/auth.py"

  # OpenCode
  opencode "$(cat skills/openmax.md)
  Task: ..."

Or simply tell the agent directly:
  "Run: openmax run '<your task description>'"

EOF
}

if [ $# -eq 0 ]; then
    install_to "$PROJECT_COMMANDS"
    exit 0
fi

for arg in "$@"; do
    case "$arg" in
        --global)   install_to "$GLOBAL_COMMANDS" ;;
        --all)      install_to "$PROJECT_COMMANDS"; install_to "$GLOBAL_COMMANDS" ;;
        --codex|--opencode) print_codex_instructions ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done
