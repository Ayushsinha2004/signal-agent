# ADR-007: No LinkedIn automation; CSV import only

Status: Accepted, 2026-11-10

## Context
LinkedIn is where most of my manual leads came from, and scraping or automating it breaks its terms and risks my account.

## Decision
Signal collects from public sources only (HN "Who is hiring", public Greenhouse, Lever and Ashby boards) and imports CSV files I export myself. It never sends anything: a human copies the approved message and sends it.

## Consequences
- Lower volume than a scraper. ATS boards and CSV import make up for part of that.
- Emails come only from the post itself; Signal never guesses an address.
