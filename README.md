# Signal

A hiring-signal outreach agent in Python. It collects fresh hiring posts, filters out recruiters and roles that don't fit, researches each company, and drafts outreach in which every claim cites a source. A human approves every message, and Signal never sends anything itself.

I built it because finding leads by hand took me about 500 LinkedIn posts for 10 qualified leads, plus 10 to 20 minutes per lead to research and write. The full plan is in [docs/PLAN.md](docs/PLAN.md).

## How it works

```
collectors (HN, Greenhouse/Lever/Ashby, CSV)  ──►  raw_posts
                                                      │
  NEW ─pre-rules─► NORMALISE ─post-rules─► CLASSIFY ─► QUALIFIED ─► RESEARCH ─► MATCH ─► DRAFT ─► VERIFY ─► REVIEW
   │ (no model)    (small)    (no model)   (small)                 (small +    (FTS +   (large)  (rules +   (human,
   └─► REJECTED with a reason at every gate                         fetch tool) small)            judge)    web UI)
                                                                                                          │
                                                                    APPROVED ─► SENT ─► REPLIED ─► INTERVIEW
```

- **A state machine, not one big agent loop** ([ADR-001](docs/adr/001-state-machine-not-agent-loop.md)). Each lead is a row with a state, and every move is checked against `models.TRANSITIONS`. The only tool-using loop is research, and it has a page and turn budget.
- **Rules before the model** ([ADR-002](docs/adr/002-rules-before-llm.md)). Cheap filters for recruiters, "US only", stale posts and seniority run first.
- **Grounded drafts** ([ADR-003](docs/adr/003-grounded-drafting-with-citations.md)). A draft is a list of claims, and each claim cites an evidence id or a source URL. The verifier checks citations and numbers without a model, then a judge model (a different one from the drafter) scores the draft.
- **Observability.** Every stage writes a `step_runs` row with model, tokens, cost, latency and error. When Langfuse keys are set, the same steps also go to Langfuse as one trace per lead ([ADR-006](docs/adr/006-postgres-metrics-langfuse-traces.md)).

## Quick start

Needs Docker and [uv](https://docs.astral.sh/uv/).

```sh
make up                      # Postgres 16 + pgvector on 127.0.0.1:55433 (own compose project)
make migrate                 # apply migrations/*.sql
make test                    # 54 tests: fake model, real Postgres
make demo                    # seed a throwaway signal_demo DB with fake leads and open the UI on :8765
```

For real runs, put a model key in `~/.config/signal-agent/env` (outside the repo; see `.env.example`). With only `OPENROUTER_API_KEY` set, Signal runs on OpenRouter's free models; with `ANTHROPIC_API_KEY` it uses Claude ([ADR-012](docs/adr/012-openrouter-free-tier.md)). Then:

```sh
uv run signal collect --source hn     # latest "Who is hiring" thread; idempotent
uv run signal run --limit 25          # advance new leads through the pipeline
uv run signal serve                   # review UI at http://127.0.0.1:8765
uv run signal funnel                  # lead counts by state and total LLM spend
uv run signal collect --source ats    # public Greenhouse / Ashby / Lever boards in profiles/<name>/boards.yaml
```

On the free tier (50 requests a day) a run stops cleanly when the cap is hit and prints `paused`. Run it again the next day and it continues where it stopped: finished stages are kept, and every model reply is cached in `data/cache/llm`, so nothing is paid for twice.

A profile is `profiles/<name>/criteria.yaml` (what a good lead looks like) plus `evidence.yaml` (facts about you, each with an id). `profiles/example/` shows the format.

## Core and stretch roles

`criteria.yaml` sets a core range (for me: at most 4 years asked, not staff level) and an optional stretch range (`stretch_seniority: [senior]`, `stretch_max_years: 7`). Stretch posts pass the rules with a `stretch` flag: they're researched and drafted like any lead, show a "stretch" badge in review, and are reported separately by the eval. The golden labels describe core fit, so stretch leads don't count as qualified there.

## Review UI

`signal serve` runs a FastAPI app with one page. For each lead it shows why it was chosen (rule reasons, classifier rationale and confidence), the post facts, the company brief with sources, which evidence matches which requirement, the draft with its verification result and judge scores, and the cost and latency of every step. You can edit the draft, approve, reject, send it back for a redraft, then mark it sent, replied or interview. Edits are stored as diffs with an optional tag (tone, wrong fact, too long, generic), and the time from opening a lead to deciding is recorded, so "minutes per lead" is measured rather than guessed. Approved leads export to CSV.

Set `SIGNAL_UI_PASSWORD` to require basic auth (user `signal`).

## Deploy

[render.yaml](render.yaml) is a Render Blueprint: the review UI as a Docker web service plus a Postgres database, both on free plans. After creating it, set `OPENROUTER_API_KEY` and add `evidence.yaml` (and optionally `denylist.txt`) as Secret Files; Render mounts them in `/etc/secrets`. Render generates `SIGNAL_UI_PASSWORD`; the user name is `signal`.

The image runs migrations, then serves on `$PORT`. It refuses a public address without `SIGNAL_UI_PASSWORD`, never contains a personal `evidence.yaml`, and migrates on managed Postgres where the app's user can't create extensions (pgvector is optional in v1).

The pipeline runs from [nightly.yml](.github/workflows/nightly.yml), a GitHub Actions job at 00:30 UTC (after the free quota resets) against the deployed database. It needs the repository secrets `SIGNAL_DATABASE_URL`, `OPENROUTER_API_KEY` and `EVIDENCE_YAML_B64`, and skips itself until they're set.

Locally: `docker build -t signal-agent . && docker run -p 8765:8765 -e SIGNAL_DATABASE_URL=... -e SIGNAL_UI_PASSWORD=... signal-agent`.

## Quality

| Check | Where | Gate |
|---|---|---|
| Lint and types | `ruff`, `mypy` | must pass |
| Unit and integration tests | `pytest` over real Postgres with a deterministic fake model | must pass |
| Rules eval | `make eval` on `evals/data/posts_golden.jsonl` (68 posts) | recall ≥ 0.85 |
| Qualify eval (live model) | `make eval-live` | precision ≥ 0.85, recall ≥ 0.80 |
| Draft grounding | verifier on every draft | 100% of claims cited |
| Judge calibration | `evals/judge_calibration.py`: my real messages vs damaged copies (set is private, built by `scripts/build_drafts_golden.py`) | AUC ≥ 0.9 |

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs lint, types, migrations, tests and the rules eval on every push and PR. The live qualify eval spends API credit, so it runs only when started by hand (`workflow_dispatch` with `live_eval`) or on a PR labelled `eval`, and it skips itself if the repo has no `ANTHROPIC_API_KEY` secret.

## Results so far

Measured, not estimated. Rows marked "not yet" fill in from SQL views once real runs start.

| KPI | Value |
|---|---|
| HN November 2026 thread collected | 219 posts |
| Rules stage on the golden set (63 unambiguous posts) | recall 1.00, precision 0.26 (the model stages do the rest) |
| Qualification on the golden set (live, free models; labels confirmed 2026-11-10) | first run precision 0.71, recall 1.00: both misses were senior roles. With senior roles moved to a separate "stretch" class: core precision 1.00, recall 1.00 on 63 posts, plus 2 stretch leads. Tuned on the same 68 posts with 5 positives, so read it as "the rules match my labels", not as general accuracy |
| Grounding rate of drafts | 2 of 2 live drafts 100% grounded; both still flagged by style rules (an en dash, a 304-character note), now fixed by a deterministic tidy step |
| Cost and latency per qualified lead | $0 on free models; first full lead (Evaboot) took about 200 s: research 19 s, match 55 s, two drafts 71 s + 55 s. Over the 60 s target on the free tier |
| Median review time per lead | not yet |
| Messages sent, reply rate | not yet |

## Where the build differs from the plan

| Plan said | Built | Why | ADR |
|---|---|---|---|
| Embeddings in pgvector plus rerank for evidence | Postgres full-text search, then the small model maps requirements to evidence | No embedding provider or cost; the evidence bank is small and uses job-post vocabulary | [008](docs/adr/008-full-text-search-before-embeddings.md) |
| Research on the large model | Research and match on the small model; only drafting uses the large one | Research is the most token-heavy stage, and the target is ≤ $0.05 per lead | [009](docs/adr/009-research-on-small-model.md) |
| React + Vite review page | One static HTML page with plain JavaScript, served by FastAPI | One screen, no build step, one process to deploy | [010](docs/adr/010-static-review-page.md) |
| `profiles/ayush/evidence.yaml` committed; denylist checked in CI | Personal evidence and the denylist stay outside the repo | They reference private client work; CI uses the example profile and `criteria.yaml` | [011](docs/adr/011-private-evidence-outside-repo.md) |
| Live eval when prompts or pipeline change | Live eval on demand or on PRs labelled `eval` | Keeps API spend under my control | this README |
| Claude models | OpenRouter free models by default, Claude when its key is set | No paid budget yet; disk cache and quota pausing make the 50/day free tier workable | [012](docs/adr/012-openrouter-free-tier.md) |

All decisions: [docs/adr/](docs/adr/README.md).

## Layout

```
src/signal_agent/
  collectors/   hn.py, ats.py, csv_import.py     pure I/O, no model
  pipeline/     rules.py, stages.py, verify.py, runner.py (state machine)
  llm/          base.py (provider interface), anthropic_llm.py, openrouter_llm.py, cache.py, factory.py
  tools/web.py  research fetch tool: robots.txt, private-IP block, size caps
  api/          app.py + static/index.html       review UI
  tracing.py    step_runs rows;  observability.py  optional Langfuse export
prompts/        versioned prompts (*.v1.md)
migrations/     hand-written SQL
evals/          golden set + run_evals.py
```

## What Signal won't do

No LinkedIn scraping or automation ([ADR-007](docs/adr/007-no-linkedin-automation.md)), no sending, and no guessed email addresses. It reads public posts and CSV files you export yourself.
