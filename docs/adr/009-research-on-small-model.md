# ADR-009: Research and match run on the small model

Status: Accepted, 2026-11-10. Deviation from PLAN.md §3.3 and §7 (research on the large tier).

## Context
Research is the most token-heavy stage (several fetched pages per lead), and the cost target is ≤ $0.05 per qualified lead. The research output is a short brief with cited sources, which the verifier checks anyway.

## Decision
RESEARCH and MATCH use `Tier.SMALL`. Only DRAFT uses `Tier.LARGE`, and the judge uses `Tier.JUDGE`. Research is capped at 4 fetched pages (5 model turns), 200 KB per response and 8,000 characters of text per page.

## Consequences
- Large-model spend is limited to one or two draft calls per lead.
- If briefs turn out thin on live runs (`complete: false` too often), research moves up a tier. That's a one-line change in `pipeline/stages.py`.
