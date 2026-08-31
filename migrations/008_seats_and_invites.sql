-- =====================================================================
-- 008 - seats: a company is more than one person
-- =====================================================================
-- profiles has carried a `role` since 002 and nothing has ever set it to
-- anything but 'admin', because there has been no way to add a second person.
-- The gap is not cosmetic: a company that cannot give its campaign manager
-- access shares one password instead, which defeats every isolation guarantee
-- underneath it.

create table if not exists invitations (
  invitation_id  uuid primary key default gen_random_uuid(),
  company_id     uuid not null references companies(id) on delete cascade,
  email          text not null,
  role           text not null default 'viewer' check (role in ('admin', 'viewer')),
  invited_by     uuid references auth.users(id) on delete set null,
  -- The code the person types when signing up. Random, single-use, and the
  -- only thing that joins an account to an existing company rather than
  -- creating a new one.
  code           text not null unique default encode(gen_random_bytes(9), 'base64'),
  status         text not null default 'pending'
                 check (status in ('pending', 'accepted', 'revoked')),
  accepted_by    uuid references auth.users(id) on delete set null,
  expires_at     timestamptz not null default now() + interval '14 days',
  created_at     timestamptz not null default now()
);

create index if not exists invitations_company_idx on invitations(company_id, status);
create index if not exists invitations_code_idx on invitations(code);

-- One open invitation per address per company. Without this, clicking "invite"
-- twice leaves two codes for the same person and one of them stays valid
-- forever after the other is used.
create unique index if not exists invitations_one_open_per_email
  on invitations(company_id, lower(email)) where status = 'pending';

alter table invitations enable row level security;

-- Only an admin may see or manage the seats of their own company. A viewer who
-- could read this table could read the codes in it, and a code is an entry
-- ticket into the workspace.
create or replace function public.current_member_role()
returns text
language sql
stable
security definer
set search_path = public
as $$
  select role from public.profiles where user_id = auth.uid()
$$;

create policy "admins read own invitations" on invitations
  for select to authenticated
  using (company_id = public.current_company_id() and public.current_member_role() = 'admin');
create policy "admins create own invitations" on invitations
  for insert to authenticated
  with check (company_id = public.current_company_id() and public.current_member_role() = 'admin');
create policy "admins update own invitations" on invitations
  for update to authenticated
  using (company_id = public.current_company_id() and public.current_member_role() = 'admin')
  with check (company_id = public.current_company_id() and public.current_member_role() = 'admin');

comment on function public.current_member_role is
  'The caller''s role, or NULL if they have no profile. Used only to gate seat '
  'management; data access is decided by current_company_id() as before, so a '
  'viewer sees exactly the same rows an admin does.';

-- ---------------------------------------------------------------------
-- Sign-up, rewritten: join a company or start one
-- ---------------------------------------------------------------------
-- Until now every new account got a brand-new company, which is right for the
-- person opening a workspace and wrong for the colleague they invited. The
-- trigger now looks for an invitation code in the sign-up metadata first.
--
-- A code that is missing, expired, already used, or issued to a different
-- address falls through to creating a company. Failing closed the other way --
-- refusing the sign-up -- would leave someone with a mistyped code unable to
-- register at all, and they can always be invited again.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  supplied_code text;
  invite        record;
  new_company_id uuid;
begin
  supplied_code := nullif(trim(new.raw_user_meta_data ->> 'invitation_code'), '');

  if supplied_code is not null then
    select * into invite
    from public.invitations
    where code = supplied_code
      and status = 'pending'
      and expires_at > now()
      and lower(email) = lower(new.email)
    limit 1;

    if found then
      insert into public.profiles (user_id, company_id, email, role)
      values (new.id, invite.company_id, new.email, invite.role);

      update public.invitations
         set status = 'accepted', accepted_by = new.id
       where invitation_id = invite.invitation_id;

      return new;
    end if;
  end if;

  insert into public.companies (name)
  values (coalesce(
    nullif(new.raw_user_meta_data ->> 'company_name', ''),
    split_part(new.email, '@', 2),
    'My company'
  ))
  returning id into new_company_id;

  -- Whoever opens the workspace administers it. There is nobody else yet.
  insert into public.profiles (user_id, company_id, email, role)
  values (new.id, new_company_id, new.email, 'admin');

  return new;
end;
$$;

-- ---------------------------------------------------------------------
-- What a viewer may not do
-- ---------------------------------------------------------------------
-- Reading is the same for both roles: a colleague given a seat sees the whole
-- workspace, which is the point of the seat. Changing it is not.
drop policy if exists "create own uploads" on uploads;
drop policy if exists "delete own uploads" on uploads;
drop policy if exists "create own records" on funnel_records;
drop policy if exists "delete own records" on funnel_records;
drop policy if exists "rename own company" on companies;

create policy "admins create uploads" on uploads
  for insert to authenticated
  with check (company_id = public.current_company_id() and public.current_member_role() = 'admin');
create policy "admins delete uploads" on uploads
  for delete to authenticated
  using (company_id = public.current_company_id() and public.current_member_role() = 'admin');

create policy "admins create records" on funnel_records
  for insert to authenticated
  with check (company_id = public.current_company_id() and public.current_member_role() = 'admin');
create policy "admins delete records" on funnel_records
  for delete to authenticated
  using (company_id = public.current_company_id() and public.current_member_role() = 'admin');

create policy "admins rename own company" on companies
  for update to authenticated
  using (id = public.current_company_id() and public.current_member_role() = 'admin')
  with check (id = public.current_company_id() and public.current_member_role() = 'admin');
