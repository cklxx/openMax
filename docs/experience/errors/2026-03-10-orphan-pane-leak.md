# Orphan Pane Leak on Cleanup

**Date:** 2026-03-10
**Category:** bug
**Severity:** P0

## What happened

During session cleanup, killing straggler panes could cause Kaku to spawn replacement panes. These orphan replacements were never tracked, leading to leaked terminal panes accumulating across sessions.

## Root cause

After killing a pane, we didn't re-list panes to check if Kaku auto-spawned a replacement. The cleanup loop assumed kill was final.

## Prevention

Always re-list panes after straggler kill to catch orphan replacements. See commit `ccaaa1f`. Pattern: any time you kill a pane, verify the pane count hasn't increased afterward.
