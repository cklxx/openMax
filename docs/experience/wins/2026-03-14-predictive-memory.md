# Predictive Memory with Dual-Buffer Context

**Date:** 2026-03-14
**Category:** architecture
**Severity:** P1

## What happened

Implemented dual-buffer context assembly: active buffer (keyword-matched, ~67%) + predictive buffer (prediction-matched + distribution-weighted, ~33%). This surfaces relevant past learnings even when the current task doesn't directly match stored keywords.

## Key insight

Session-end predictions (predicting likely follow-up queries) combined with query-distribution weighting gives the memory system a forward-looking capability. The agent gets useful context even for novel tasks in familiar categories.

## Reuse

Apply the same dual-buffer pattern whenever building context from a scored entry set: reserve a fraction of the budget for indirect/predictive matches rather than filling entirely with direct keyword hits.
