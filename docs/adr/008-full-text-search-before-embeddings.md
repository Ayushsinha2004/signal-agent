# ADR-008: Evidence retrieval uses Postgres full-text search in v1

Status: Accepted, 2026-11-10. Deviation from PLAN.md §3.2 (ADR-004) and §16 (embeddings).

## Context
The plan called for embeddings in pgvector plus a rerank. My evidence bank is a few dozen short items written in the same vocabulary as job posts (Python, RAG, agents, Postgres).

## Decision
`evidence.tsv` is a `tsvector` built from the text and skills. MATCH takes the top 14 items by `ts_rank` against the post's stack and role terms, then the small model maps requirements to evidence ids, and any id it invents is dropped. The `embedding vector(1024)` column stays in the schema, unused.

## Consequences
- No embedding provider, key or cost, and retrieval is easy to explain and debug.
- Synonyms that don't share words ("LLM evaluation" vs "evals") can be missed. If manual review of matches shows that, embeddings go in behind the same function.
