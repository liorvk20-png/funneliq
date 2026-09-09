-- =====================================================================
-- 004 — let a company fix its own name
-- =====================================================================
-- The sign-up trigger falls back to the email domain when no company name was
-- given, so the first account on this project is called "gmail.com". There was
-- no way to change that from inside the product: companies had a SELECT policy
-- and nothing else, so the only fix was a new account under a different email.
--
-- Scoped like every other write here. A member may rename their own company
-- and no other, and the WITH CHECK clause is what stops an update from moving
-- a row into someone else's workspace on its way out.

create policy "rename own company" on companies
  for update to authenticated
  using (id = public.current_company_id())
  with check (id = public.current_company_id());

-- A blank name would render as an empty heading throughout the interface, and
-- the constraint is the only place that holds for every future writer.
alter table companies
  add constraint companies_name_not_blank check (length(btrim(name)) > 0);
