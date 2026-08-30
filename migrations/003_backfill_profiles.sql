-- =====================================================================
-- 003 — give every existing account a workspace
-- =====================================================================
-- Migration 002 added a trigger that builds a company and a profile for each
-- new sign-up. Triggers only fire on new rows, so every account that existed
-- before 002 was left with no profile at all.
--
-- That is not a cosmetic gap. current_company_id() returns NULL for such a
-- user, and because every RLS policy compares against it, the policies fail
-- closed: the account authenticates perfectly and then sees an empty product,
-- with nothing in the interface to explain why. The first account on this
-- project — the owner's — was in exactly that state.
--
-- Safe to run more than once: the insert skips users who already have a
-- profile, so a second run changes nothing.

do $$
declare
  u record;
  new_company_id uuid;
begin
  for u in
    select id, email, raw_user_meta_data
    from auth.users
    where id not in (select user_id from public.profiles)
  loop
    insert into public.companies (name)
    values (coalesce(
      nullif(u.raw_user_meta_data ->> 'company_name', ''),
      split_part(u.email, '@', 2),
      'My company'
    ))
    returning id into new_company_id;

    insert into public.profiles (user_id, company_id, email)
    values (u.id, new_company_id, u.email);

    raise notice 'workspace created for %', u.email;
  end loop;
end $$;

-- ---------------------------------------------------------------------
-- Tidy up companies nothing points at.
-- The isolation check creates throwaway users and deletes them afterwards.
-- Deleting a user cascades to their profile, but companies has no foreign key
-- to auth.users, so the company itself survives as an orphan — along with any
-- funnel_records still hanging off it. Left alone these accumulate on every
-- run and quietly hold rows no one can ever read.
delete from public.companies c
where not exists (select 1 from public.profiles p where p.company_id = c.id);
