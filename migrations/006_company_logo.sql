-- =====================================================================
-- 006 — a company's own mark
-- =====================================================================
-- Stored on the company row as a data URL rather than in object storage. A
-- logo is a few tens of kilobytes, it is read exactly when the company that
-- owns it loads the app, and keeping it here means it inherits the isolation
-- everything else already has instead of needing a bucket with its own
-- policies to get wrong.
--
-- The UPDATE policy from 004 already covers this column: it restricts the
-- statement to the caller's own company row, whichever columns the statement
-- touches.

alter table companies add column if not exists logo_b64 text;

-- Roughly 400KB of base64, which is about 300KB of image — far above any
-- sensible logo and far below anything that would make the row awkward. The
-- constraint is here rather than only in the API because this is the last
-- place that holds for every future writer.
alter table companies
  add constraint companies_logo_size check (logo_b64 is null or length(logo_b64) < 400000);

comment on column companies.logo_b64 is
  'The company mark as a data URL. Raster only — an SVG is a document that can '
  'carry script, and this one is chosen by a customer and shown to their '
  'colleagues.';
