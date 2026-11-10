# ADR-006: Postgres is the source of truth for metrics; Langfuse is an optional trace view

Status: Accepted, 2026-11-10

## Context
I need two kinds of observability: per-call debugging (what did the model see and say) and business questions (cost per qualified lead, funnel conversion).

## Decision
Every stage execution writes a `step_runs` row (model, tokens, cost, latency, error, detail) through `tracing.step()`. When `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set, the same step is also sent to Langfuse as a span on the lead's trace (`leads.trace_id`). Without the keys nothing is sent and nothing breaks.

## Consequences
- KPIs never depend on a third-party service being up.
- Langfuse is the place to show a single lead's trace in the video; SQL is the place for numbers.
