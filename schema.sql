-- FunnelIQ database schema
-- One row per customer/campaign record, matching funnel_marketing_data.csv.
-- Load via scripts/load_csv_to_supabase.py — never insert rows by hand.
-- Run this whole file once in the Supabase SQL editor to provision the table.

create table if not exists funnel_records (
  id                          bigserial primary key,
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
  ltv_months                  numeric,          -- nullable: 4 rows missing in source CSV
  purchased                   boolean not null,
  upsell                      boolean not null,
  cumulative_profit           numeric,          -- nullable: 29 rows missing in source CSV
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

comment on table funnel_records is
  'One row per customer/campaign record. Source: funnel_marketing_data.csv. Loaded via scripts/load_csv_to_supabase.py.';
comment on column funnel_records.budget_tier is
  'Derived: Low <=1500, Mid 1501-5000, High >5000. The brief left the 1500-2000 range undefined between its Low and Mid bands; resolved here by folding it into Mid.';

-- Row Level Security: only signed-in Northbound team members may read.
alter table funnel_records enable row level security;

create policy "authenticated users can read funnel records"
  on funnel_records
  for select
  to authenticated
  using (true);

-- Deliberately no insert/update/delete policy for the authenticated role:
-- data is written exclusively via the service-role key from the server-side
-- load script (scripts/load_csv_to_supabase.py), which bypasses RLS entirely.
-- The running application never writes to this table.
