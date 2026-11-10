# ADR-001: An explicit state machine, not one autonomous agent loop

Status: Accepted, 2026-11-10

## Context
The job is a fixed sequence: read a post, decide if it fits, learn about the company, pick evidence, write, check. Only one step, researching the company, has an open-ended shape.

## Decision
Each lead is one row in `leads` with a `state`. `pipeline/runner.py` advances it one stage at a time, and `models.TRANSITIONS` lists every legal move; anything else raises `IllegalTransition`. The only tool-using loop is RESEARCH, and it has a hard page and turn budget. The review UI moves leads through the same `move()` function, so a human can't put a lead in an illegal state either.

## Consequences
- Every stage is tested, retried and costed on its own (`step_runs`). A failure lands in `FAILED` with the stage and error, and `retry` sends it back to `NEW`.
- Cost per lead is bounded by construction.
- Less flexible than a free agent: a new kind of step means a new state and transition, which is deliberate.
