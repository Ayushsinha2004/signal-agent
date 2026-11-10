# ADR-004: Postgres as the only datastore

Status: Accepted, 2026-11-10. Retrieval part amended by [ADR-008](008-full-text-search-before-embeddings.md).

## Context
The project needs lead state, dedupe, an evidence index, cost metrics and an audit trail. ALBERT's stack is Python, Claude, Postgres and React.

## Decision
One Postgres 16 database (the `pgvector/pgvector:pg16` image) holds everything. Schema changes are hand-written SQL in `migrations/`, applied in order by `signal migrate`. Dedupe is a unique index on `(source, external_id)` for posts and a partial unique index on `(profile_id, company_key, role_key)` for leads.

## Consequences
- Funnel, cost and review-time KPIs are SQL views (`v_funnel`, `v_cost_per_lead`, `v_review_seconds`).
- Locally it runs as its own compose project on port 55433, separate from anything else on the machine.
- pgvector is optional: migration 0001 creates the extension only where the app's user is allowed to (managed Postgres such as Render may refuse), and adds the unused `embedding` column only then. Found by running the image against a non-superuser database on 2026-11-10.
