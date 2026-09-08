-- =====================================================================
-- 010 - a company can connect its own Meta Ads account
-- =====================================================================
-- One row per company: the ad account it pulls from, and the access token
-- that authorises the pull. The token is a secret the same way the
-- Supabase secret key is — it is written by the backend and read by the
-- backend, and no policy here ever hands it to a browser. RLS still governs
-- the row (isolation must hold even if a query is ever written badly), but
-- the actual guarantee is that app/main.py never serialises the token
-- column into a response.
--
-- Reading is admin-or-editor here, not "everyone the way product data is."
-- This table is closer to a company setting (the company rename policy in
-- 009) than to the funnel numbers themselves: a viewer has no use for an ad
-- account id, and every added reader is one more place a token can leak
-- from.

create table if not exists meta_ads_connections (
  company_id      uuid primary key references companies(id) on delete cascade,
  ad_account_id   text not null,          -- e.g. "act_1234567890"
  access_token    text not null,          -- Meta user or system-user token
  token_expires_at timestamptz,           -- null when the token's lifetime is unknown
  connected_by    uuid references auth.users(id) on delete set null,
  connected_at    timestamptz not null default now(),
  last_synced_at  timestamptz
);

comment on table meta_ads_connections is
  'One Meta Ads connection per company. MVP: a manually pasted token, not '
  'OAuth -- see app/integrations/meta_ads.py. access_token is never returned '
  'by any API response; it is used server-side only.';

alter table meta_ads_connections enable row level security;

create policy "editors read own meta connection" on meta_ads_connections
  for select to authenticated
  using (company_id = public.current_company_id() and public.can_edit_data());

create policy "editors write own meta connection" on meta_ads_connections
  for insert to authenticated
  with check (company_id = public.current_company_id() and public.can_edit_data());

create policy "editors update own meta connection" on meta_ads_connections
  for update to authenticated
  using (company_id = public.current_company_id() and public.can_edit_data())
  with check (company_id = public.current_company_id() and public.can_edit_data());

create policy "editors delete own meta connection" on meta_ads_connections
  for delete to authenticated
  using (company_id = public.current_company_id() and public.can_edit_data());
