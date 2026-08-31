-- =====================================================================
-- 005 — a company's models belong to that company
-- =====================================================================
-- Every prediction so far came from models fitted on the reference dataset the
-- project was built with: nobody's data. The page said so, which is honest and
-- not the same as useful. Training now happens when a file is uploaded, on that
-- company's rows, and the result is stored here.
--
-- model_registry already existed for this from 002 and was never written to.
-- Three things it needs before it can be.

-- The model itself, base64 encoded. bytea would be the natural type; PostgREST
-- carries JSON, and base64 in a text column survives that round trip without a
-- custom encoder on both sides. A company's four models come to roughly half a
-- megabyte, which Postgres stores out of line and never reads unless asked for.
alter table model_registry add column if not exists model_b64 text;

-- Whether the model beat the laziest possible alternative on this company's own
-- data. A model that did not is kept — the measurement is worth showing — but
-- never used for a prediction, and this column is what enforces that.
alter table model_registry add column if not exists useful boolean not null default false;
alter table model_registry add column if not exists note text;

-- Training runs inside the upload request, through the person's own token, so
-- the table needs the write policies it never had. Scoped like every other
-- write here: WITH CHECK is what stops a caller filing a model under another
-- company on the way out.
create policy "create own models" on model_registry
  for insert to authenticated with check (company_id = public.current_company_id());
create policy "delete own models" on model_registry
  for delete to authenticated using (company_id = public.current_company_id());

-- Reading the latest version of a target is the only query the API makes, and
-- it runs on every prediction.
create index if not exists model_registry_latest_idx
  on model_registry(company_id, target, version desc);

comment on column model_registry.useful is
  'Measured better than predicting the average on this company''s own held-out '
  'rows. False means the model exists and is not shown: a prediction no better '
  'than the average is not a weak answer to caveat, it is an answer we do not have.';
