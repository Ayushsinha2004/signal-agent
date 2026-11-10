# ADR-011: Personal evidence and the denylist stay out of the repo

Status: Accepted, 2026-11-10. Deviation from PLAN.md §8 (`profiles/ayush/evidence.yaml` committed).

## Context
My evidence bank is generated from my private résumé data, and some ids and texts refer to client work under codenames. The repo is public.

## Decision
`profiles/*/evidence.yaml` is gitignored, except `profiles/example/`. My file is generated locally by `scripts/import_evidence.py`. The private denylist (client names that must never appear in a draft) lives at `~/.config/signal-agent/denylist-<profile>.txt` and is read at runtime. Criteria (`criteria.yaml`) are committed, since they hold nothing private.

## Consequences
- CI uses the example profile for tests and `criteria.yaml` alone for evals (`profile.load_criteria`), so it never needs private files.
- The plan's "denylist checked in CI" becomes a runtime check in VERIFY instead, since the list can't be public.
