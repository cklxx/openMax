#!/usr/bin/env bash
# Install openMax skills for supported agents.
# Usage: ./install_skills.sh [--global] [--codex] [--opencode]
#
# --global   Install to ~/.claude/commands/ (available in ALL projects)
# --codex    Print instructions for Codex (no native skill system)
# --opencode Print instructions for OpenCode (no native skill system)
# Default: install to .claude/commands/ in current project

set -euo pipefail

SKILLS_DIR="$(cd "$(dirname "$0")/skills" && pwd)"
PROJECT_COMMANDS=".claude/commands"
GLOBAL_COMMANDS="$HOME/.claude/commands"

install_claude_code() {
    local target="$1"
    mkdir -p "$target"
    for f in "$SKILLS_DIR"/*.md; do
        skill="$(basename "$f")"
        ln -sf "$f" "$target/$skill"
        echo "  linked: $target/$skill"
    done
    echo "Done. In Claude Code, invoke with /commit, /release, /test, /lint, /debug, /research"
}

print_codex_instructions() {
    echo ""
    echo "=== Codex / OpenCode (no native skill system) ==="
    echo "Inject skill content directly into your prompt:"
    echo ""
    echo "  cat skills/commit.md | codex"
    echo "  # or prepend to your task prompt:"
    echo "  codex \"\$(cat skills/debug.md)\nError: <paste error here>\""
    echo ""
    echo "Or let openMax lead agent auto-inject skills via dispatch prompts."
}

for arg in "$@"; do
    case "$arg" in
        --global)
            echo "Installing to global ~/.claude/commands/ ..."
            install_claude_code "$GLOBAL_COMMANDS"
            ;;
        --codex|--opencode)
            print_codex_instructions
            ;;
    esac
done

if [ $# -eq 0 ]; then
    echo "Installing to project .claude/commands/ ..."
    install_claude_code "$PROJECT_COMMANDS"
fi
