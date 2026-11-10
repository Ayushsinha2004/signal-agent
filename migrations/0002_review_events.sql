-- 0002: audit trail for the review UI. Every human action is one row, so edits are kept as diffs
-- and "minutes of my time per lead" (PLAN.md §12.5) is measured instead of estimated.
create table review_events (
  id          bigserial primary key,
  lead_id     bigint not null references leads(id),
  action      text not null,          -- view | edit | approve | reject | redraft | sent | replied | interview | dead | rescue | retry
  from_state  text,
  to_state    text,
  detail      jsonb not null default '{}',
  created_at  timestamptz not null default now()
);
create index review_events_lead on review_events (lead_id, created_at);

-- Seconds from the first time a lead was opened in the UI to the approve/reject decision.
create view v_review_seconds as
  select e.lead_id,
         extract(epoch from min(e.created_at) filter (where e.action in ('approve', 'reject'))
                          - min(e.created_at) filter (where e.action = 'view'))::int as seconds
  from review_events e
  group by e.lead_id
  having min(e.created_at) filter (where e.action in ('approve', 'reject')) is not null
     and min(e.created_at) filter (where e.action = 'view') is not null;
