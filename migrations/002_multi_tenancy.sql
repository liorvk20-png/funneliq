-- 002 — multi-tenancy.
--
-- Turns a single-company table into an engine any company can sign up to.
-- Every company's data is invisible to every other company, enforced by the
-- database rather than by application code.
--
-- DESTRUCTIVE: drops the existing funnel_records. That is deliberate and
-- agreed — the 3,500 demo rows belonged to no registered company, and every
-- company now trains on its own upload.
--
-- Run once in the Supabase SQL editor.

-- ---------------------------------------------------------------- companies
create table if not exists companies (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  created_at  timestamptz not null default now()
);

comment on table companies is
  'One row per customer of the product. Everything else hangs off this id.';

-- ----------------------------------------------------------------- profiles
-- The link between a Supabase auth user and the company whose data they see.
-- Kept separate from companies so a company can have several people later
-- without changing anything here.
create table if not exists profiles (
  user_id     uuid primary key references auth.users(id) on delete cascade,
  company_id  uuid not null references companies(id) on delete cascade,
  email       text,
  role        text not null default 'admin' check (role in ('admin', 'viewer')),
  created_at  timestamptz not null default now()
);

create index if not exists profiles_company_idx on profiles(company_id);

-- ------------------------------------------------------------------ uploads
-- One row per file a company uploads. Unused until the upload feature exists,
-- and present now on purpose: adding it later would mean migrating live
-- customer data instead of creating an empty table.
create table if not exists uploads (
  id            uuid primary key default gen_random_uuid(),
  company_id    uuid not null references companies(id) on delete cascade,
  uploaded_by   uuid references auth.users(id) on delete set null,
  period        date not null,                 -- the month the data describes
  filename      text,
  row_count     integer not null default 0,
  status        text not null default 'pending'
                check (status in ('pending', 'analysing', 'ready', 'failed')),
  error         text,
  created_at    timestamptz not null default now()
);

create index if not exists uploads_company_period_idx on uploads(company_id, period desc);

comment on column uploads.period is
  'The month the data describes, not when it was uploaded. Re-uploading a '
  'corrected file for an earlier month must land in that month.';

-- ----------------------------------------------------------- model registry
-- Which model belongs to which company, and what it was trained on. Recorded
-- from the start because a prediction that cannot be traced to a model version
-- cannot be explained to a customer six months later.
create table if not exists model_registry (
  id            uuid primary key default gen_random_uuid(),
  company_id    uuid not null references companies(id) on delete cascade,
  upload_id     uuid references uploads(id) on delete set null,
  target        text not null,                 -- ltv_months, upsell, referred, profit
  version       integer not null default 1,
  trained_rows  integer not null,
  metrics       jsonb,                         -- MAE, PR-AUC and so on, as measured
  storage_path  text,
  created_at    timestamptz not null default now(),
  unique (company_id, target, version)
);

create index if not exists model_registry_company_idx on model_registry(company_id, target);

-- ------------------------------------------------------------ funnel records
drop table if exists funnel_records;

create table funnel_records (
  id                          bigserial primary key,
  company_id                  uuid not null references companies(id) on delete cascade,
  upload_id                   uuid references uploads(id) on delete cascade,
  ad_budget                   integer not null,
  num_leads                   integer not null,
  leads_answered              integer not null,
  leads_not_answered          integer not null,
  followup_1                  integer not null,
  followup_2                  integer not null,
  followup_3                  integer not null,
  followup_4                  integer not null,
  followup_5                  integer not null,
  not_closed                  integer not null,
  closed                      integer not null,
  calls_to_closed             integer not null,
  calls_to_not_closed         integer not null,
  customer_acquisition_cost   integer not null,
  ltv_months                  numeric,
  purchased                   boolean not null,
  upsell                      boolean not null,
  cumulative_profit           numeric,
  referred                    boolean not null,
  budget_tier                 text generated always as (
                                 case
                                   when ad_budget <= 1500 then 'Low'
                                   when ad_budget <= 5000 then 'Mid'
                                   else 'High'
                                 end
                               ) stored,
  created_at                  timestamptz not null default now()
);

create index if not exists funnel_records_company_idx on funnel_records(company_id);
create index if not exists funnel_records_upload_idx on funnel_records(upload_id);

-- =====================================================================
-- ISOLATION
-- =====================================================================
-- The whole product rests on this section. The previous policy was
-- `using (true)` — every signed-in user could read every row, which was fine
-- with one company and becomes a data breach with two.
--
-- Isolation lives in the database, not the application. A bug in a query, a
-- forgotten filter, or a new endpoint written in a hurry cannot leak another
-- company's rows, because Postgres refuses before the query returns.

-- Which company does the caller belong to? SECURITY DEFINER so the lookup can
-- read profiles regardless of the caller's own policies, and a fixed
-- search_path so it cannot be redirected by a caller-set path.
create or replace function public.current_company_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select company_id from public.profiles where user_id = auth.uid()
$$;

comment on function public.current_company_id is
  'The caller''s company, or NULL if they have no profile. Every RLS policy '
  'compares against this, so a user with no profile sees nothing at all.';

alter table companies      enable row level security;
alter table profiles       enable row level security;
alter table uploads        enable row level security;
alter table model_registry enable row level security;
alter table funnel_records enable row level security;

-- A company is visible only to its own members.
create policy "read own company" on companies
  for select to authenticated
  using (id = public.current_company_id());

-- A profile is visible to its owner and to colleagues in the same company.
create policy "read own profile" on profiles
  for select to authenticated
  using (user_id = auth.uid() or company_id = public.current_company_id());

-- Data tables: read and write are both scoped, and the WITH CHECK clause is
-- what stops a caller inserting a row *into another company* — a hole that a
-- read-only policy would leave wide open.
create policy "read own uploads" on uploads
  for select to authenticated using (company_id = public.current_company_id());
create policy "create own uploads" on uploads
  for insert to authenticated with check (company_id = public.current_company_id());
create policy "update own uploads" on uploads
  for update to authenticated using (company_id = public.current_company_id())
  with check (company_id = public.current_company_id());
create policy "delete own uploads" on uploads
  for delete to authenticated using (company_id = public.current_company_id());

create policy "read own models" on model_registry
  for select to authenticated using (company_id = public.current_company_id());

create policy "read own records" on funnel_records
  for select to authenticated using (company_id = public.current_company_id());
create policy "create own records" on funnel_records
  for insert to authenticated with check (company_id = public.current_company_id());
create policy "delete own records" on funnel_records
  for delete to authenticated using (company_id = public.current_company_id());

-- Deliberately no UPDATE policy on funnel_records: uploaded data is a record of
-- what happened. Correcting a month means re-uploading it, which leaves a trail
-- in `uploads`, rather than editing rows in place and leaving none.

-- =====================================================================
-- SIGN-UP
-- =====================================================================
-- Every new user gets their own company. Chosen over matching on email domain
-- because domains lie: two colleagues on Gmail would be strangers, and two
-- strangers at a large host would be colleagues. Inviting a teammate into an
-- existing company is a deliberate act, added later.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  new_company_id uuid;
begin
  insert into public.companies (name)
  values (coalesce(
    nullif(new.raw_user_meta_data ->> 'company_name', ''),
    split_part(new.email, '@', 2),          -- a sensible default, not an identity
    'My company'
  ))
  returning id into new_company_id;

  insert into public.profiles (user_id, company_id, email)
  values (new.id, new_company_id, new.email);

  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

comment on function public.handle_new_user is
  'Creates a company and profile for every new sign-up. Without it a user '
  'authenticates successfully and then sees nothing, because current_company_id '
  'returns NULL and every policy fails closed.';
