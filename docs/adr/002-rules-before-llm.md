# ADR-002: Deterministic rules before any model call

Status: Accepted, 2026-11-10

## Context
By hand, about 500 posts gave 10 leads. Most of the noise is cheap to spot: recruiter wording, "US only", posts older than 15 days, staff-plus roles.

## Decision
`pre_rules` runs on raw text before NORMALISE, and `post_rules` runs on the extracted facts before CLASSIFY. Both live in `pipeline/rules.py`, are driven by `profiles/<name>/criteria.yaml`, and record a reason for every reject.

## Consequences
- The rules are gated on **recall** only (≥ 0.85 in CI): they may let noise through, they must not drop good posts. On the 68-post golden set they reach recall 1.00 at precision 0.26; the model stages do the rest.
- A wrong rule reject is recoverable: the UI's "Rescue to review" moves a `REJECTED` lead to `REVIEW`.
- Senior roles (and posts asking up to 7 years) are a separate **stretch** class, not a reject: they pass with a `stretch` flag, get drafted, and are shown apart in review. The eval measures core fit, which is what the golden labels describe, and lists stretch leads separately. Added 2026-11-10 after the first live eval showed both false positives were senior roles.
