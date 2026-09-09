-- =====================================================================
-- 009 - who is in the workspace, and what they may do
-- =====================================================================
-- Two gaps this closes.
--
-- A workspace listed its members by email address alone, which is the one
-- thing a colleague already knows and the least useful for telling people
-- apart. Several people from one company open this product, each with a
-- different job, and the list should say who they are.
--
-- And `role` had two values where the work needs three. An analyst who imports
-- data and edits the account should not also be able to add and remove people;
-- an admin should. Splitting them is what makes it safe to give somebody a seat.

alter table profiles add column if not exists full_name  text;
alter table profiles add column if not exists job_title  text;
alter table profiles add column if not exists phone      text;
alter table profiles add column if not exists gender     text
  check (gender is null or gender in ('female', 'male', 'other', 'undisclosed'));
alter table profiles add column if not exists birth_year smallint
  check (birth_year is null or birth_year between 1900 and 2100);

-- editor sits between the two that existed: full control of the data, none of
-- the control over people.
alter table profiles drop constraint if exists profiles_role_check;
alter table profiles add constraint profiles_role_check
  check (role in ('admin', 'editor', 'viewer'));

alter table invitations drop constraint if exists invitations_role_check;
alter table invitations add constraint invitations_role_check
  check (role in ('admin', 'editor', 'viewer'));

-- A member may correct their own details. Nobody else's, and never their own
-- role -- a viewer who could edit their row would simply promote themselves.
create policy "update own profile" on profiles
  for update to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid() and role = public.current_member_role());

-- ---------------------------------------------------------------------
-- Writing data is now editor-or-admin; managing people stays admin only.
create or replace function public.can_edit_data()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select public.current_member_role() in ('admin', 'editor')
$$;

drop policy if exists "admins create uploads" on uploads;
drop policy if exists "admins delete uploads" on uploads;
drop policy if exists "admins create records" on funnel_records;
drop policy if exists "admins delete records" on funnel_records;
drop policy if exists "admins rename own company" on companies;

create policy "editors create uploads" on uploads
  for insert to authenticated
  with check (company_id = public.current_company_id() and public.can_edit_data());
create policy "editors delete uploads" on uploads
  for delete to authenticated
  using (company_id = public.current_company_id() and public.can_edit_data());

create policy "editors create records" on funnel_records
  for insert to authenticated
  with check (company_id = public.current_company_id() and public.can_edit_data());
create policy "editors delete records" on funnel_records
  for delete to authenticated
  using (company_id = public.current_company_id() and public.can_edit_data());

create policy "editors rename own company" on companies
  for update to authenticated
  using (id = public.current_company_id() and public.can_edit_data())
  with check (id = public.current_company_id() and public.can_edit_data());

-- ---------------------------------------------------------------------
-- Sign-up carries the details the person filled in.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  supplied_code  text;
  invite         record;
  new_company_id uuid;
  meta           jsonb := coalesce(new.raw_user_meta_data, '{}'::jsonb);
begin
  supplied_code := nullif(trim(meta ->> 'invitation_code'), '');

  if supplied_code is not null then
    select * into invite
    from public.invitations
    where code = supplied_code
      and status = 'pending'
      and expires_at > now()
      and lower(email) = lower(new.email)
    limit 1;

    if found then
      insert into public.profiles (user_id, company_id, email, role,
                                   full_name, job_title, phone, gender, birth_year)
      values (new.id, invite.company_id, new.email, invite.role,
              nullif(meta ->> 'full_name', ''), nullif(meta ->> 'job_title', ''),
              nullif(meta ->> 'phone', ''), nullif(meta ->> 'gender', ''),
              nullif(meta ->> 'birth_year', '')::smallint);

      update public.invitations
         set status = 'accepted', accepted_by = new.id
       where invitation_id = invite.invitation_id;
      return new;
    end if;
  end if;

  insert into public.companies (name)
  values (coalesce(
    nullif(meta ->> 'company_name', ''),
    split_part(new.email, '@', 2),
    'My company'
  ))
  returning id into new_company_id;

  insert into public.profiles (user_id, company_id, email, role,
                               full_name, job_title, phone, gender, birth_year)
  values (new.id, new_company_id, new.email, 'admin',
          nullif(meta ->> 'full_name', ''), nullif(meta ->> 'job_title', ''),
          nullif(meta ->> 'phone', ''), nullif(meta ->> 'gender', ''),
          nullif(meta ->> 'birth_year', '')::smallint);

  return new;
end;
$$;

comment on function public.can_edit_data is
  'True for admin and editor. Data access is still decided by '
  'current_company_id(); this only decides who may change what is there.';
