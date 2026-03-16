# Hardcoded Tool List Drift

**Date:** 2026-03-12
**Category:** bug
**Severity:** P1

## What happened

`allowed_tools` was hardcoded instead of derived from `ALL_TOOLS`. When new tools were added, they weren't included in the allowed list, causing silent failures where the lead agent couldn't use newly registered tools.

## Root cause

Copy-paste of tool names into a separate list rather than deriving from the source of truth.

## Prevention

Always derive tool lists from the canonical registry (`ALL_TOOLS`). Never maintain a separate hardcoded list. See commit `2f4cf99`.
