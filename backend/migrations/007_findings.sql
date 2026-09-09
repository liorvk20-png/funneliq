-- =====================================================================
-- 007 - WP7: findings and the deterministic narrative
-- =====================================================================
-- One adaptation, stated up front. The specification is written against
-- organizations(id) and auth_org(). This product's tenancy is companies(id)
-- and current_company_id(), established in 002 and enforced by every policy
-- since. Adding a second tenancy key alongside the first would create two
-- isolation systems that must agree forever, and the failure mode when they
-- stop agreeing is one customer reading another customer's analysis. So
-- org_id is company_id throughout, and nothing else changes.

-- ------------------------------------------------------------- runs
-- A run is one execution of the analysis over one window. Everything a report
-- says is reproducible from it, which is what makes "the same run produces the
-- same text" a property anyone can check rather than a claim.
create table if not exists analysis_runs (
  run_id       uuid primary key default gen_random_uuid(),
  company_id   uuid not null references companies(id) on delete cascade,
  upload_id    uuid references uploads(id) on delete set null,
  status       text not null default 'complete'
               check (status in ('running', 'complete', 'failed')),
  error        text,
  created_at   timestamptz not null default now()
);
create index if not exists analysis_runs_company_idx on analysis_runs(company_id, created_at desc);

-- ------------------------------------------------------- windows
create table if not exists analysis_windows (
  window_id       uuid primary key default gen_random_uuid(),
  run_id          uuid not null references analysis_runs(run_id) on delete cascade,
  company_id      uuid not null references companies(id) on delete cascade,
  current_start   date not null,
  current_end     date not null,
  baseline_start  date not null,
  baseline_end    date not null,
  baseline_method text not null default 'previous_period'
                  check (baseline_method in
                         ('previous_period', 'same_period_last_year', 'trailing_median')),
  -- Comparing four weeks against five is the commonest way a comparison lies,
  -- and it lies toward whichever period is longer. Rejected by the database as
  -- well as by the application, because this is the last place that holds.
  constraint windows_equal_length
    check (current_end - current_start = baseline_end - baseline_start),
  constraint windows_ordered
    check (current_end >= current_start and baseline_end >= baseline_start)
);

-- -------------------------------------------------------- findings
create table if not exists findings (
  finding_id          uuid primary key default gen_random_uuid(),
  company_id          uuid not null references companies(id) on delete cascade,
  run_id              uuid not null references analysis_runs(run_id) on delete cascade,
  window_id           uuid references analysis_windows(window_id) on delete cascade,

  finding_type        text not null,
  metric_key          text not null,
  dimension_path      jsonb not null default '{}'::jsonb,
  dimension_depth     smallint not null default 0,

  value_current       numeric,
  value_baseline      numeric,
  delta_abs           numeric,
  delta_pct           numeric,

  effect_type         text,
  contribution_abs    numeric,
  contribution_share  numeric,

  denom_current       numeric,
  denom_baseline      numeric,
  significance_p      numeric,
  ci_low              numeric,
  ci_high             numeric,

  direction           text check (direction in ('up', 'down', 'flat')),
  is_favorable        boolean,
  severity            smallint not null check (severity between 0 and 100),
  confidence_label    text not null
                      check (confidence_label in ('high', 'medium', 'low', 'insufficient')),

  parent_finding_id   uuid references findings(finding_id) on delete set null,
  evidence            jsonb not null default '{}'::jsonb,
  created_at          timestamptz not null default now()
);
create index if not exists findings_rank_idx on findings(company_id, run_id, severity desc);
create index if not exists findings_dimension_idx on findings using gin (dimension_path);

-- --------------------------------------------------- narrative output
create table if not exists narrative_outputs (
  output_id   uuid primary key default gen_random_uuid(),
  company_id  uuid not null references companies(id) on delete cascade,
  run_id      uuid not null references analysis_runs(run_id) on delete cascade,
  section     text not null
              check (section in ('headline', 'drivers', 'funnel', 'watch', 'quality')),
  ordinal     smallint not null,
  rule_key    text not null,
  -- NOT NULL, and a foreign key. This column is the iron rule made structural:
  -- a sentence with nothing behind it cannot be stored, so it cannot be shown.
  finding_id  uuid not null references findings(finding_id) on delete cascade,
  text_he     text not null,
  text_en     text,
  created_at  timestamptz not null default now()
);
create index if not exists narrative_run_idx on narrative_outputs(company_id, run_id, ordinal);

-- ---------------------------------------------------------- isolation
alter table analysis_runs     enable row level security;
alter table analysis_windows  enable row level security;
alter table findings          enable row level security;
alter table narrative_outputs enable row level security;

create policy "read own runs" on analysis_runs
  for select to authenticated using (company_id = public.current_company_id());
create policy "create own runs" on analysis_runs
  for insert to authenticated with check (company_id = public.current_company_id());
create policy "update own runs" on analysis_runs
  for update to authenticated using (company_id = public.current_company_id())
  with check (company_id = public.current_company_id());
create policy "delete own runs" on analysis_runs
  for delete to authenticated using (company_id = public.current_company_id());

create policy "read own windows" on analysis_windows
  for select to authenticated using (company_id = public.current_company_id());
create policy "create own windows" on analysis_windows
  for insert to authenticated with check (company_id = public.current_company_id());

create policy "read own findings" on findings
  for select to authenticated using (company_id = public.current_company_id());
create policy "create own findings" on findings
  for insert to authenticated with check (company_id = public.current_company_id());
create policy "delete own findings" on findings
  for delete to authenticated using (company_id = public.current_company_id());

create policy "read own narrative" on narrative_outputs
  for select to authenticated using (company_id = public.current_company_id());
create policy "create own narrative" on narrative_outputs
  for insert to authenticated with check (company_id = public.current_company_id());
create policy "delete own narrative" on narrative_outputs
  for delete to authenticated using (company_id = public.current_company_id());

comment on table findings is
  'The contract everything downstream reads. Dashboard cards, alerts, the PDF '
  'and any future agent consume these rather than re-deriving meaning from raw '
  'rows, which is why adding the next consumer costs nothing.';

comment on column narrative_outputs.finding_id is
  'Not null on purpose. Every sentence traces to the finding that justified it; '
  'a claim that cannot be traced is a bug, not a wording choice.';
