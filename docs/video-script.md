# Evaboot video: 5 minutes, unedited, facecam + screen

What to have open before recording: the README, `docs/adr/`, a terminal in the repo, the review UI (`make serve`)
with at least one lead in REVIEW, `src/signal_agent/pipeline/runner.py`, and (if keys are set) Langfuse on that
lead's trace. Fill every `[TBD]` from `signal funnel`, `make eval`, `make eval-live` and the UI header first.
Say only numbers you can show on screen.

---

## 0:00–0:40 The problem
**Show:** README top.

"Last week I looked for founders hiring AI engineers, by hand. I read about 500 LinkedIn posts to get 10 qualified
leads, and each lead then took 10 to 20 minutes: read the post, check the company, write a message that only says
true things about me. Signal is the agent I built to do the filtering, research and drafting, and leave the
send to me. I use it for my own outreach."

## 0:40–1:50 Architecture
**Show:** the diagram in the README, then `runner.py` `_advance` and `models.TRANSITIONS`.

- "It's a state machine, not one agent loop. Each lead is a Postgres row with a state, and every move is checked
  against this transition table. An illegal move raises, and that's tested." (ADR-001)
- "Rules first. Recruiter wording, 'US only', stale posts and staff-level roles are rejected before any model
  call. On my golden set the rules keep every good post (recall 1.00) and throw away most of the noise for free."
  (ADR-002)
- "The only open-ended part is research: a tool loop with a fetch tool that respects robots.txt, blocks private
  IPs and caps page size, with a hard page budget."
- "Drafts are structured: a list of claims, and each claim cites an evidence id or a source URL." (ADR-003)
- "The model sits behind an interface. Today it runs on OpenRouter's free models; switching to Claude is a config
  change. Because the free tier allows 50 requests a day, every reply is cached and hitting the cap pauses the
  run instead of failing leads." (ADR-012)

## 1:50–2:50 Observability
**Show:** the UI's Trace panel on one lead, then `signal funnel`, then Langfuse if enabled.

- "Every stage writes a step_runs row: model, tokens, cost, latency, error. Here's one lead: [TBD] steps,
  [TBD] seconds, $0 because these are free models."
- "Funnel and cost are SQL views, so the numbers don't depend on a third-party service. Langfuse gets the same
  steps as one trace per lead when the keys are set." (ADR-006)
- "The review screen shows why each lead is here: which rules passed, the classifier's rationale and
  confidence, the brief's sources, which evidence matches which requirement."

## 2:50–3:50 Quality
**Show:** `evals/data/posts_golden.jsonl`, CI workflow, `make eval-live` output, a failed draft in the UI.

- "68 hand-labelled posts from the HN November thread. CI runs lint, types, 49 tests on real Postgres with a fake
  model, and the rules eval on every push. The live eval runs on demand: precision [TBD], recall [TBD] on
  [TBD model]."
- "The verifier checks drafts without a model: every number must appear in a source, every citation must exist,
  no client names, no dashes or buzzwords. Here it caught [TBD: e.g. a number not in any source]."
- "The judge is a different model family from the drafter, and I calibrate it on my own messages versus
  deliberately damaged copies: AUC [TBD], gate 0.9."
- "My edits are the main signal. The UI stores every edit as a diff with a tag, and times how long I spend per
  lead: median [TBD] seconds."

## 3:50–4:30 Problems I hit
- "Recruiter noise and location ambiguity: 'remote' often means 'remote in the US'. That's why the rules gate on
  recall, and the classifier gets the final word with a confidence score."
- "Invented personalisation: the first drafts were confident and wrong, which is why drafts became claims with
  citations."
- "Free-tier limits: busy providers and a 50-a-day cap. Fallback models per tier, a disk cache, and pausing on
  the cap made it workable. Free models are also slow: one draft call took over a minute."

## 4:30–5:00 Still figuring out
- "The real metric is reply rate, and it's slow and noisy, so I report it but don't gate on it."
- "Turning my edit tags into prompt changes automatically, with the eval gate in between."
- "Research cost on paid models, and whether embeddings beat full-text search for matching evidence."
