# Signal: a hiring-signal outreach agent. Project plan

Status: written 2026-11-10; M0–M5 built 2026-11-10 (see "As built" below). Owner: Ayush Sinha.

> **As built (2026-11-10).** Four places differ from this plan, each with an ADR: evidence retrieval uses
> Postgres full-text search instead of embeddings ([ADR-008](adr/008-full-text-search-before-embeddings.md)),
> research runs on the small model ([ADR-009](adr/009-research-on-small-model.md)), the review UI is one static
> page instead of React + Vite ([ADR-010](adr/010-static-review-page.md)), and personal evidence plus the denylist
> stay out of the repo ([ADR-011](adr/011-private-evidence-outside-repo.md)). The live-model eval runs on demand
> or on PRs labelled `eval` rather than on every prompt change.
Repo (to be created): `github.com/Ayushsinha2004/signal-agent` (public).

---

## 1. Why this project exists

### 1.1 The problem, measured
Between 5 and 7 Nov 2026 I looked for founders hiring AI engineers, by hand, with LinkedIn post search, Hacker News and FullEnrich:
- I read about **500 LinkedIn posts** to end up with **10 qualified leads**.
- Most of the noise was recruiters, staffing firms, people looking for work, big companies, US-only roles, and posts older than 15 days.
- Each qualified lead then took 10 to 20 minutes: read the post, check the profile, find the company, find an email, and write a message that only uses true facts about me.

Doing that by hand doesn't scale. An agent that runs the filtering, research and grounded drafting, and leaves the final send to me, does.

### 1.2 Who it's for
1. **Me, from day one.** Every lead I contact goes through Signal, so usage data and outcomes (sent, replied, interview) are real.
2. **Later, other job seekers or freelancers.** Each of them brings their own evidence bank. Multi-user support is out of scope for v1, but the data model keeps a `profile_id` from the start.

### 1.3 What it proves (portfolio goals)
| Audience | What they asked for | How Signal answers |
|---|---|---|
| **Evaboot**, Agentic Python Engineer | A Python project that implements an agentic product or workflow, plus a 5-minute video on architecture, observability, quality assessment, problems and learnings. Bonus: AWS and Langfuse. | It's an SDR-style agent built on prospecting data, with Langfuse traces, an eval suite in CI, and deployment to AWS. |
| **ALBERT**, new-grad AI track | "Shipped something with an LLM in it that real people used", with a link. Stack: Python, Claude, Postgres, React. | It's live, I use it daily, the KPIs are real, and the stack matches theirs. |
| **Any AI engineering role** | Agents, RAG, evals, cost and latency, production engineering. | All of those show up in one small, readable codebase. |

### 1.4 Non-goals for v1
- No LinkedIn scraping or automation, since it breaks LinkedIn's terms. Instead, Signal imports CSV exports the user makes themselves (for example from Sales Navigator via Evaboot, or a manual copy).
- No sending. Signal drafts and a human sends. This also keeps it clear of CAN-SPAM and PECR problems.
- No email guessing. Emails come only from the post itself or from an enrichment provider that reports verification status.
- No multi-tenant auth, billing or teams.

---

## 2. Requirements

### 2.1 Functional
| ID | Requirement |
|---|---|
| F1 | Collect hiring posts from public sources on a schedule and on demand. |
| F2 | Normalise every post into one schema: company, role(s), location policy, seniority, stack, contact channel, posted_at, source URL. |
| F3 | Deduplicate the same company and role across sources and across runs. |
| F4 | Qualify each post against the user's criteria (remote and India-eligible, role family, max years, freshness ≤ 15 days, not a recruiter or staffing firm). Record a reason for every reject. |
| F5 | Research each qualified company with tools (fetch the site, careers page and post) within a step and token budget. |
| F6 | Match the post's requirements to the user's evidence bank (retrieval over `evidence.yaml`). |
| F7 | Draft an outreach message (email or InMail plus a ≤300-character connection note). Every factual claim must cite an evidence id or a source URL. |
| F8 | Verify each draft with deterministic checks plus an LLM judge. Drafts that fail go back to the drafter once, then to review flagged as failed. |
| F9 | Review UI: list leads, see why each was chosen, edit the draft, then approve, reject or mark sent. Each lead keeps the next status after that (replied, interview, dead). |
| F10 | Export approved leads to CSV and copy-ready text. |

### 2.2 Non-functional
| ID | Requirement | Target |
|---|---|---|
| N1 | Cost per qualified lead (LLM spend) | ≤ $0.05 |
| N2 | Pipeline latency per lead (collect to draft) | p95 ≤ 60 s |
| N3 | Grounding: share of draft claims backed by a cited source | 100% (hard gate) |
| N4 | Idempotent runs: re-running a day creates no duplicates | 0 duplicates |
| N5 | Every LLM call traced (inputs, outputs, tokens, cost, latency) | 100% |
| N6 | Fully reproducible locally with one command | `make up` |
| N7 | Secrets only in env vars, never in logs (I've seen an HTTP client log leak an API key before) | lint + log scrubber |

All targets in this plan are goals to measure, not results.

---

## 3. Architecture

### 3.1 Shape
```
                    ┌────────────────────── Collectors (pure I/O, no LLM) ──────────────────────┐
 cron / CLI / UI ──►│ hn_whoishiring   greenhouse   lever   ashby   csv_import (Sales Nav export) │
                    └──────────────────────────────────┬─────────────────────────────────────────┘
                                                       ▼  raw_posts (Postgres, unique on source+external_id)
 ┌──────────────────────────────── Pipeline (explicit state machine, one row per lead) ──────────────────────────────┐
 │ NORMALISE ──► RULES ──► CLASSIFY ──► RESEARCH ──► MATCH ──► DRAFT ──► VERIFY ──► REVIEW                          │
 │ (LLM, small) (no LLM)  (LLM, small)  (agent+tools) (pgvector)(LLM)    (rules+judge) (human)                       │
 │   each step: idempotent, retryable, budgeted, traced; failures land in state=FAILED with a reason                 │
 └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
        │ Langfuse traces (one trace per lead, one span per step)          │ Postgres: leads, steps, drafts, evals
        ▼                                                                   ▼
   Langfuse UI                                        FastAPI ──► React review page (approve / edit / reject / sent)
```

### 3.2 Key decisions (each becomes an ADR in `docs/adr/`)
| # | Decision | Why |
|---|---|---|
| ADR-001 | **An explicit state machine, not one autonomous agent loop.** The only open-ended agent is RESEARCH, and it has a budget. | Every step can be tested, retried and traced on its own. A failure lands in a known state. Cost is bounded. It's easier to explain and debug in a 5-minute video. |
| ADR-002 | **Rules before LLM.** A cheap deterministic filter (recruiter words, "US only", age > 15 d, seniority > 5+ yrs) runs before any model call. | My manual funnel: roughly 98% of posts are noise. Rules cost nothing and are easy to explain. |
| ADR-003 | **Grounded drafting with citations.** The draft is structured output: `claims[] = {text, evidence_id | source_url}` plus the rendered message. | It makes invented personalisation and inflated facts impossible to hide. The verifier can check claims mechanically. |
| ADR-004 | **Postgres + pgvector as the only datastore.** | One system for state, dedupe, evidence retrieval and analytics. It matches ALBERT's stack. |
| ADR-005 | **Claude as the default model behind a provider-agnostic interface**: a small model for classify and normalise, a larger one for research and draft, and judge ≠ drafter. | Cost control, plus no self-grading. |
| ADR-006 | **Langfuse for traces and scores; Postgres for business metrics.** | Langfuse shows per-call debugging, while SQL answers funnel and cost questions. |
| ADR-007 | **No LinkedIn automation. CSV import only.** | Terms of service and account safety. It's also a design choice worth saying out loud. |

### 3.3 Pipeline stages: contracts
Each stage is a function `run(lead_id) -> StageResult`. It reads and writes Postgres, is wrapped in a Langfuse span, and is safe to re-run.

| Stage | Input | Output (Pydantic) | Model / tool | Budget | Failure handling |
|---|---|---|---|---|---|
| NORMALISE | raw_post.text | `PostFacts{company, roles[], location_policy, countries_allowed[], remote: bool, seniority, years_min, stack[], contact{emails[], apply_urls[]}, comp?}` | small model, JSON schema | 1 call, ≤2k tokens | schema-repair once, then FAILED |
| RULES | PostFacts + criteria | `RuleVerdict{pass, reasons[]}` | none | 0 | n/a |
| CLASSIFY | PostFacts + text | `Fit{role_family, is_recruiter, is_direct_hire, india_eligible: yes/no/unknown, confidence}` | small model | 1 call | low confidence goes to REVIEW tagged "unsure" |
| RESEARCH | company + urls | `CompanyBrief{what_they_build, stage, size?, ai_stack?, recent_signal, sources[]}` | larger model + tools: `fetch_url`, `search_site`, `read_hn_item` | ≤6 tool calls, ≤20k tokens, 45 s | partial brief allowed, marked incomplete |
| MATCH | PostFacts.requirements | `Match{req -> evidence_ids[], gaps[]}` | pgvector top-k over evidence + small-model rerank | 1 call | no evidence for a requirement goes in `gaps` |
| DRAFT | brief + match + channel | `Draft{subject, body, connection_note, claims[]}` | larger model | 1 call | n/a |
| VERIFY | Draft | `Verification{grounded_pct, numbers_ok, banned_phrases[], length_ok, judge{relevance, specificity, tone, 1–5}, pass}` | rules + judge model (different from drafter) | 1 call | fail sends it back to DRAFT once with feedback, then REVIEW flagged |
| REVIEW | all of the above | status change + edited text | human | n/a | n/a |

### 3.4 Deterministic verifier rules (VERIFY)
- Every number in the body appears in a cited evidence item's `metrics` list (the same rule as my resume engine's honesty lint).
- Every claim has a citation, and every cited evidence id exists.
- No client names: there's a denylist from the profile.
- No em or en dashes, no "not X but Y", no banned buzzwords (humanizer rules).
- Length: InMail 50–110 words, connection note ≤300 characters, subject ≤60 characters.
- The message names the company and the role, and references one concrete fact from the post or brief.

### 3.5 Research agent tools
| Tool | Does | Guards |
|---|---|---|
| `fetch_url(url)` | GET plus readability extraction to markdown | robots.txt respected, 10 s timeout, 200 KB cap, allowlist of schemes, no auth pages |
| `search_site(domain, query)` | fetches /careers, /jobs, /about and the sitemap, then filters | max 3 pages |
| `read_hn_item(id)` | HN Firebase API | none |
| `finish(brief)` | ends the loop with a structured brief | required within budget |

---

## 4. Data model (Postgres)

```sql
create table profiles (            -- the job seeker (v1: one row)
  id uuid primary key, name text, criteria jsonb, created_at timestamptz default now());

create table evidence (             -- loaded from evidence.yaml (a subset of my master.yaml)
  id text primary key, profile_id uuid references profiles, text text, metrics text[],
  skills text[], embedding vector(1024), updated_at timestamptz);

create table raw_posts (
  id bigserial primary key, source text, external_id text, url text, author text,
  posted_at timestamptz, text text, fetched_at timestamptz default now(),
  unique (source, external_id));

create table leads (
  id bigserial primary key, profile_id uuid references profiles, raw_post_id bigint references raw_posts,
  company_key text,                -- normalised domain or name, for cross-source dedupe
  state text not null,             -- NEW/NORMALISED/REJECTED/QUALIFIED/RESEARCHED/MATCHED/DRAFTED/VERIFIED/REVIEW/APPROVED/SENT/REPLIED/INTERVIEW/DEAD/FAILED
  facts jsonb, rule_verdict jsonb, fit jsonb, brief jsonb, match jsonb,
  reject_reason text, trace_id text, cost_usd numeric(10,5) default 0,
  created_at timestamptz default now(), updated_at timestamptz default now(),
  unique (profile_id, company_key, (facts->>'role_key')));

create table step_runs (            -- one row per stage execution (retries visible)
  id bigserial primary key, lead_id bigint references leads, stage text, status text,
  model text, input_tokens int, output_tokens int, cost_usd numeric(10,5), latency_ms int,
  error text, started_at timestamptz, finished_at timestamptz);

create table drafts (
  id bigserial primary key, lead_id bigint references leads, version int, channel text,
  subject text, body text, connection_note text, claims jsonb, verification jsonb,
  edited_body text, status text, created_at timestamptz default now());

create table outcomes (             -- what happened after I sent it: the real KPI
  lead_id bigint primary key references leads, sent_at timestamptz, replied_at timestamptz,
  reply_kind text, interview_at timestamptz, notes text);

create table eval_runs (
  id bigserial primary key, git_sha text, dataset text, metrics jsonb, passed bool,
  created_at timestamptz default now());
```

Migrations are hand-written SQL files in `migrations/`, applied with a small runner (no ORM magic). That's the same discipline I used for 58 hand-written migrations on a production service.

---

## 5. Quality: how I assess what the agent produces

### 5.1 Golden datasets (in `evals/data/`, hand-labelled, committed)
| Set | Size | Labels | Source |
|---|---|---|---|
| `posts_golden` | 60 posts | qualify yes/no + reason, is_recruiter, india_eligible, role_family, years_min | the real posts from 5–7 Nov (10 kept, plus a sample of rejects) and the HN Nov 2026 thread |
| `extract_golden` | 30 posts | full PostFacts | a subset of the above |
| `drafts_golden` | 15 leads | my approved final messages (the ones I actually sent) | 33-HIRING-POSTS and 34-HIRING-POSTS folders |

### 5.2 Metrics and gates
| Metric | How | Gate (CI fails below) |
|---|---|---|
| Qualification precision | on posts_golden | ≥ 0.85 |
| Qualification recall | on posts_golden | ≥ 0.80 |
| Recruiter detection F1 | on posts_golden | ≥ 0.90 |
| Field extraction accuracy | exact match per field on extract_golden | ≥ 0.90 on remote, countries, contact |
| Grounding rate | verifier on all drafts | = 1.00 |
| Judge score (relevance, specificity, tone) | judge model against a rubric, calibrated on drafts_golden | mean ≥ 4.0 / 5 |
| Edit distance to my final message | normalised Levenshtein, approved drafts | tracked, no gate (it's a learning signal) |
| Cost per qualified lead | step_runs sum | ≤ $0.05 |

### 5.3 Judge calibration
- The judge scores my 15 real sent messages and 15 deliberately bad drafts. It must separate them: AUC ≥ 0.9 before I trust it.
- The judge model differs from the drafter, so nothing grades its own output.
- The rubric is versioned in `prompts/judge_rubric.v1.md`, and every score stores its rubric version.

### 5.4 Online quality (production)
- Funnel per day: collected → rules-pass → qualified → drafted → verified → approved → sent → replied.
- My edits are the main signal. For every approved draft I store the diff and tag it (tone, wrong fact, too long, generic). Weekly, the most common tag becomes a prompt or rubric change, which must pass the eval gate before it ships.
- The final KPI is the **reply rate on sent messages**. It's slow and noisy, so it gets reported but isn't used as a CI gate.

---

## 6. Observability
- **Langfuse:** one trace per lead (`trace_id` stored on the lead) and one span per stage. Generations record the model, tokens and cost. Eval scores are attached to traces. Tags: `stage`, `source`, `prompt_version`.
- **Postgres:** `step_runs` for retries, latency and cost. SQL views `v_funnel_daily`, `v_cost_per_lead` and `v_failures_by_stage`.
- **Logs:** structured JSON (structlog), with a scrubber for keys and emails. HTTP client logs are kept at WARNING (lesson learned: HTTP client INFO logs once leaked an API key in another project).
- **The review UI shows the "why"** for each lead: rule reasons, classifier confidence, cited evidence and the judge's scores. It's the same data the video will show.

---

## 7. Cost and latency budget (estimates to validate in M2)
| Stage | Model tier | Calls / lead | Est. tokens | Notes |
|---|---|---|---|---|
| NORMALISE | small | 1 | 2.5k in / 0.4k out | runs on every post |
| CLASSIFY | small | 1 | 1.5k / 0.2k | only on rules-pass (~10–20%) |
| RESEARCH | large | ≤6 tool turns | ≤20k | only on qualified |
| MATCH rerank | small | 1 | 2k / 0.3k | |
| DRAFT | large | 1–2 | 4k / 0.6k | |
| VERIFY judge | mid | 1–2 | 2k / 0.3k | |

Levers: prompt caching on static system prompts and the evidence bank, rules first, a hard cap on research turns, and a cache of company briefs per domain (TTL 7 days). Real numbers get recorded in M2 and replace this table.

---

## 8. Repository layout
```
signal-agent/
├── README.md                    # what, why, demo GIF, live link, KPIs
├── Makefile                     # up, migrate, seed, run, eval, test, lint
├── docker-compose.yml           # postgres+pgvector, langfuse (local), api, web
├── pyproject.toml               # uv-managed; ruff, mypy, pytest
├── migrations/                  # 0001_init.sql …
├── docs/
│   ├── PLAN.md                  # this file
│   ├── adr/                     # ADR-001 … ADR-007
│   └── video-script.md          # the 5-minute Evaboot walkthrough
├── profiles/ayush/
│   ├── evidence.yaml            # subset of master.yaml (no client names)
│   └── criteria.yaml            # remote, India ok, role families, max years, freshness
├── src/signal_agent/
│   ├── config.py                # pydantic-settings, env only
│   ├── db.py                    # psycopg 3 pool, tiny query helpers
│   ├── models.py                # Pydantic contracts from §3.3
│   ├── llm/                     # provider interface, anthropic impl, pricing table, retries
│   ├── collectors/              # hn.py, greenhouse.py, lever.py, ashby.py, csv_import.py
│   ├── pipeline/                # normalise.py, rules.py, classify.py, research.py,
│   │                            # match.py, draft.py, verify.py, runner.py (state machine)
│   ├── tools/                   # fetch_url.py, search_site.py, hn_item.py (research tools)
│   ├── evidence/                # loader + embeddings + retrieval
│   ├── observability.py         # langfuse wiring, log scrubber
│   ├── api/                     # FastAPI app: leads, drafts, review actions, metrics
│   └── cli.py                   # `signal collect|run|eval|export`
├── prompts/                     # versioned prompt files (normalise.v1.md, draft.v1.md, judge_rubric.v1.md)
├── evals/
│   ├── data/                    # golden sets (JSONL)
│   ├── run_evals.py             # computes §5.2 metrics, writes eval_runs, exits non-zero on gate fail
│   └── judge_calibration.py
├── web/                         # React + Vite review page (single screen + lead drawer)
├── tests/                       # unit (rules, verifier, dedupe, parsers), integration (pipeline on fixtures)
└── .github/workflows/
    ├── ci.yml                   # lint, types, unit, integration (pg service), evals (on prompt/pipeline changes)
    └── nightly.yml              # optional: collect + run on schedule
```

---

## 9. Milestones (about 7 working days)
Each milestone ends with a demo-able state and green CI.

### M0: Skeleton (½ day)
- Repo, uv project, ruff, mypy and pytest, docker-compose with Postgres+pgvector, migration runner plus `0001_init.sql`, Makefile, CI.
- **Done when:** `make up && make migrate && make test` passes locally and in CI.

### M1: Collect + rules + golden set (1 day)
- HN Who-is-hiring collector (Algolia to find the thread, Firebase for comments), Greenhouse, Lever and Ashby board collectors (company list in config), CSV import.
- Dedupe (`source+external_id`, then `company_key+role_key`).
- RULES stage plus unit tests.
- Label `posts_golden` (60) from the real 5–7 Nov data.
- **Done when:** `signal collect --source hn` stores the Nov 2026 thread (224 posts), rules reject with reasons, and the rules alone already show precision and recall on the golden set.

### M2: LLM stages + tracing (1½ days)
- LLM interface (Anthropic), pricing table, retries with backoff, and structured output through Pydantic.
- NORMALISE and CLASSIFY with versioned prompts, Langfuse wired in (local self-hosted or cloud free tier), and step_runs written.
- `evals/run_evals.py` for extraction and qualification metrics.
- **Done when:** classification metrics are measured, the gates from §5.2 are met or the gaps are written down, and every call shows in Langfuse with cost.

### M3: Research agent + evidence match (1½ days)
- Tools with guards (§3.5), the agent loop with budget, and the CompanyBrief schema.
- Evidence loader: `evidence.yaml` → embeddings → pgvector. MATCH with top-k plus rerank.
- Company brief cache.
- **Done when:** for 10 golden qualified leads, briefs cite their sources, no run exceeds its budget, and requirement-to-evidence mappings look right on manual review (logged in `docs/m3-review.md`).

### M4: Draft + verify + judge (1 day)
- DRAFT with claims and citations, VERIFY rules (§3.4), the judge with a versioned rubric, and judge calibration (AUC check).
- **Done when:** grounding = 1.00 on all eval drafts, the judge separates real from bad drafts (AUC ≥ 0.9), and CI runs evals on prompt changes.

### M5: Review UI + API (1 day)
- FastAPI endpoints: list leads by state, lead detail (facts, reasons, brief, match, drafts, verification, trace link), edit, approve, reject, mark sent, mark replied.
- React page: queue, lead drawer, copy buttons, outcome buttons. Edits stored as diffs.
- **Done when:** I can go from "new posts" to "approved draft copied" in under a minute per lead.

### M6: Deploy + real use + video (1 day, then ongoing)
- Docker image, deployed to AWS (App Runner or ECS Fargate, plus RDS Postgres or Neon free tier if cost matters). Nightly collect job. Basic auth on the UI.
- README with architecture diagram, a demo GIF, and live KPIs pulled from SQL views.
- Use it for every outreach from that day on.
- Write and record the Evaboot video (`docs/video-script.md`, 5 minutes, unedited).
- **Done when:** it's live, a week of real usage is recorded, the KPIs are in the README, and the video is recorded.

---

## 10. Testing strategy
- **Unit:** rules, verifier (numbers, citations, dashes, lengths), dedupe keys, HN/ATS parsers (fixtures saved from real responses), pricing math, state transitions (illegal transitions raise).
- **Integration:** the full pipeline on 5 fixture posts against real Postgres (a CI service container) with a **recorded LLM layer**, so CI is deterministic and free. Live-model runs happen only in the eval job.
- **Evals:** §5. They run when `prompts/`, `pipeline/` or `llm/` change, and on demand.
- **Property tests (hypothesis):** dedupe keys are stable under whitespace, case and URL variations.

---

## 11. Security, privacy, compliance
- Only public data plus the user's own exports. No LinkedIn automation, and robots.txt is respected.
- Emails are stored only if published in the post or returned by a provider with a status. The status is stored and shown. No guessing.
- No sending. The human sends. The UI reminds you about opt-out lines and UK sole-trader rules (PECR) on email drafts.
- Secrets live in env or AWS Secrets Manager. The log scrubber is tested.
- `profiles/*/evidence.yaml` has no client names (the denylist is checked in CI).

---

## 12. KPIs I will report (README + video)
Measured, not estimated. Each comes from a SQL view:
1. Posts processed per run, and the funnel conversion at each stage
2. Qualification precision and recall on the golden set
3. Grounding rate of drafts (target 1.00)
4. Median cost per qualified lead and p95 latency per lead
5. Minutes of my time per lead (before: 10–20 min by hand; after: measured in the UI)
6. Messages sent, reply rate and interviews (once real)

---

## 13. The Evaboot video: 5 minutes, unedited, facecam + screen
Full script goes in `docs/video-script.md` at M6. Outline:
1. **0:00–0:40 Context:** the 500-posts-to-10-leads problem, and why I built Signal.
2. **0:40–1:50 Architecture:** the diagram, the state machine over one agent loop (ADR-001), rules before LLM, grounded drafting.
3. **1:50–2:50 Observability:** a live Langfuse trace for one lead (spans, tool calls, tokens, cost), and the funnel and cost SQL views.
4. **2:50–3:50 Quality:** golden sets, the CI eval gate, the verifier catching an invented number, judge calibration.
5. **3:50–4:30 Problems:** recruiter noise, location ambiguity, invented personalisation, research budget blowups.
6. **4:30–5:00 Still figuring out:** reply rate as the final eval, how to turn my edits into prompt changes automatically, and the cost of research.

## 14. ALBERT submission
- Email recruiting@albert.com with the live link, the GitHub repo and the KPI block from the README.
- Message Andrzej Baraniak with a 3-line version.

---

## 15. Risks and mitigations
| Risk | Mitigation |
|---|---|
| HN gives only one thread a month, so volume is low | ATS board collectors over a curated list of remote-friendly AI companies, plus CSV import |
| Research agent loops or overspends | hard turn, token and time budget; `finish` required; a test for budget exhaustion |
| Judge is lenient | calibration gate (AUC), a different model from the drafter, a versioned rubric |
| Scope creep before the video deadline | M0–M4 are the minimum for the video; M5 UI can be a plain HTML page if time is short |
| Deployed costs | Neon/RDS free tier, App Runner minimum instance 0 or a single small Fargate task, brief cache |
| Overclaiming in drafts | numeric honesty check against evidence metrics; client-name denylist |

## 16. Open decisions (defaults chosen; change if needed)
| Decision | Default |
|---|---|
| Agent framework | Plain Python + Anthropic SDK tool use. No LangGraph, because the state machine is small and fully visible. |
| Embeddings | Voyage or OpenAI small embeddings, 1024-dim. Swap behind an interface. |
| Langfuse | Cloud free tier first, self-host later if needed |
| Hosting | AWS App Runner + Neon Postgres (pgvector) |
| UI | React + Vite single page served by FastAPI |
| Model ids | Latest Claude family: a small model for normalise and classify, a larger one for research and draft, and a different model for the judge. Read from config. |

## 17. Definition of done (v1)
- Public repo, green CI, eval gates passing, README with live KPIs.
- Deployed and used for every outreach for at least 7 days.
- 5-minute Evaboot video recorded. ALBERT email sent with links.
