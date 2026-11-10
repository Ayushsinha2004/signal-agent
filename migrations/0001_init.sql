-- 0001: core schema. Hand-written; applied in order by `signal migrate`.
-- pgvector is optional (v1 retrieval is full-text search, ADR-008). Hosts where the app's database user may
-- not create extensions (e.g. a non-superuser on managed Postgres) still migrate; the embedding column is then
-- simply absent.
do $$ begin
  create extension if not exists vector;
exception when insufficient_privilege or undefined_file then
  raise notice 'pgvector not available: skipping the evidence.embedding column';
end $$;

create table profiles (
  id          text primary key,              -- e.g. 'ayush'
  name        text not null,
  criteria    jsonb not null,
  created_at  timestamptz not null default now()
);

create table evidence (
  id          text not null,
  profile_id  text not null references profiles(id),
  text        text not null,
  metrics     text[] not null default '{}',
  skills      text[] not null default '{}',
  tsv         tsvector,                       -- set on upsert (array_to_string is not immutable)
  updated_at  timestamptz not null default now(),
  primary key (profile_id, id)
);
create index evidence_tsv on evidence using gin (tsv);
do $$ begin
  if exists (select 1 from pg_extension where extname = 'vector') then
    alter table evidence add column embedding vector(1024);  -- unused in v1; kept for a future embeddings retriever
  end if;
end $$;

create table raw_posts (
  id           bigserial primary key,
  source       text not null,                 -- hn | greenhouse | lever | ashby | csv
  external_id  text not null,
  url          text,
  author       text,
  posted_at    timestamptz,
  text         text not null,
  fetched_at   timestamptz not null default now(),
  unique (source, external_id)
);

create table leads (
  id            bigserial primary key,
  profile_id    text not null references profiles(id),
  raw_post_id   bigint not null references raw_posts(id),
  company_key   text,
  role_key      text,
  state         text not null default 'NEW',
  facts         jsonb,
  rule_verdict  jsonb,
  fit           jsonb,
  brief         jsonb,
  match         jsonb,
  reject_reason text,
  trace_id      text not null default md5(random()::text),
  cost_usd      numeric(12,6) not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (profile_id, raw_post_id)
);
create index leads_state on leads (profile_id, state);
-- cross-source dedupe: one live lead per company+role per profile
create unique index leads_company_role on leads (profile_id, company_key, role_key)
  where company_key is not null and role_key is not null and state <> 'DUPLICATE';

create table step_runs (
  id            bigserial primary key,
  lead_id       bigint references leads(id),
  stage         text not null,
  status        text not null,                -- ok | error | skipped
  model         text,
  input_tokens  int default 0,
  output_tokens int default 0,
  cache_read_tokens int default 0,
  cost_usd      numeric(12,6) default 0,
  latency_ms    int,
  error         text,
  detail        jsonb,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz
);
create index step_runs_lead on step_runs (lead_id);

create table drafts (
  id              bigserial primary key,
  lead_id         bigint not null references leads(id),
  version         int not null,
  channel         text not null,              -- email | inmail
  subject         text,
  body            text not null,
  connection_note text,
  claims          jsonb not null,
  verification    jsonb,
  edited_body     text,
  status          text not null default 'draft', -- draft | approved | rejected
  created_at      timestamptz not null default now(),
  unique (lead_id, version)
);

create table outcomes (
  lead_id      bigint primary key references leads(id),
  sent_at      timestamptz,
  replied_at   timestamptz,
  reply_kind   text,
  interview_at timestamptz,
  notes        text
);

create table eval_runs (
  id         bigserial primary key,
  git_sha    text,
  dataset    text not null,
  metrics    jsonb not null,
  passed     boolean not null,
  created_at timestamptz not null default now()
);

create view v_funnel as
  select profile_id, state, count(*) as n from leads group by 1, 2;

create view v_cost_per_lead as
  select l.id as lead_id, l.state, coalesce(sum(s.cost_usd), 0) as cost_usd,
         coalesce(sum(s.latency_ms), 0) as latency_ms
  from leads l left join step_runs s on s.lead_id = l.id group by 1, 2;
