# ADR-003: Drafts are structured claims, each with a citation

Status: Accepted, 2026-11-10

## Context
The failure that matters most in outreach is a confident, invented fact: a number I never hit, a product the company doesn't make.

## Decision
DRAFT returns `DraftOut{subject, body, connection_note, claims[]}`; each claim cites an `evidence_id` (about me) or a `source_url` (about the company or post). VERIFY (`pipeline/verify.py`) checks mechanically that every cited id exists, every number in the body appears in a source, the company is named, no denylisted name, dash or buzzword appears, and lengths are in range (body 50 to 110 words, connection note ≤ 300 characters), and then asks a judge model for relevance, specificity, tone and honesty. A draft that fails is rewritten once with the problems as feedback; a second failure goes to review flagged.

## Consequences
- Grounding is a hard gate (target 1.00), checked without a model.
- Drafts are a little stiffer than free text. My edits in the UI are stored as diffs so I can see whether that costs me time.
