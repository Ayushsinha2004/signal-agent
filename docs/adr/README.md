# Architecture decision records

| # | Decision | Status |
|---|---|---|
| [001](001-state-machine-not-agent-loop.md) | An explicit state machine, not one autonomous agent loop | Accepted |
| [002](002-rules-before-llm.md) | Deterministic rules before any model call | Accepted |
| [003](003-grounded-drafting-with-citations.md) | Drafts are structured claims, each with a citation | Accepted |
| [004](004-postgres-only-datastore.md) | Postgres (with pgvector available) as the only datastore | Accepted, retrieval part amended by 008 |
| [005](005-claude-behind-provider-interface.md) | Claude behind a provider-agnostic interface; judge differs from drafter | Accepted, tiers amended by 009, provider by 012 |
| [006](006-postgres-metrics-langfuse-traces.md) | Postgres is the source of truth for metrics; Langfuse is an optional trace view | Accepted |
| [007](007-no-linkedin-automation.md) | No LinkedIn automation; CSV import only | Accepted |
| [008](008-full-text-search-before-embeddings.md) | Evidence retrieval uses Postgres full-text search in v1 | Accepted (deviation from plan) |
| [009](009-research-on-small-model.md) | Research and match run on the small model | Accepted (deviation from plan) |
| [010](010-static-review-page.md) | Review UI is one static page, not React + Vite | Accepted (deviation from plan) |
| [011](011-private-evidence-outside-repo.md) | Personal evidence and denylist stay out of the repo | Accepted (deviation from plan) |
| [012](012-openrouter-free-tier.md) | Run on OpenRouter free models behind the same interface, with cache and quota pausing | Accepted (deviation from plan) |
