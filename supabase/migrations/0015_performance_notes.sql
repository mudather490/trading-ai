-- =============================================================================
-- 0015_performance_notes.sql
--
-- One column.
--
-- The research runner has always written a `notes` object into the
-- performance row: the holdout summary, the selection rule, the exposure
-- cap, and the flag that says the holdout of this dataset had already been
-- read by an earlier run.
--
-- The column did not exist. The write failed with
-- "column \"notes\" of relation \"performance\" does not exist", which is
-- why no candidate had ever been published: the runner had never been
-- able to finish.
--
-- The gate reads `notes->>'holdout_contaminated'` (migration 0013), so the
-- column is not optional bookkeeping. It is how the gate knows that a
-- candidate's holdout was already spent.
--
-- The column is nullable and defaulted, so nothing that exists changes
-- meaning: a row without notes means "nothing recorded", not "clean".
-- =============================================================================

alter table public.performance
  add column if not exists notes jsonb;

comment on column public.performance.notes is
  'The runner''s record of how the candidate was judged: the holdout '
  'summary, the selection rule, the exposure cap, and whether the dataset''s '
  'holdout had already been read.';
