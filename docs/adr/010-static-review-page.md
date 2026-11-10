# ADR-010: The review UI is one static page, not React + Vite

Status: Accepted, 2026-11-10. Deviation from PLAN.md §8 and §16 (React + Vite); allowed by the §15 risk note.

## Context
The UI has one screen: a queue, a lead panel and a few buttons. The video deadline is mid-November.

## Decision
FastAPI (`api/app.py`) serves a JSON API and one HTML file (`api/static/index.html`) with plain JavaScript. There's no Node toolchain or build step. All post text is untrusted, so the page builds DOM with `textContent` and never puts data into `innerHTML`. Every human action goes through `runner.move()` and is written to `review_events`, which keeps edits as diffs and measures review time per lead.

## Consequences
- One language and one process to deploy.
- If the UI grows (filters, bulk actions), the API already returns JSON, so a React front end can replace the page without touching the backend.
