"""
Generate supabase/migrations/0007_function_grants_hardening.sql

The generator exists so the redefined function bodies are byte-identical to the
ones already applied, except for the custody guard that is inserted after the
function's `begin`. Hand-copying a 375-line function is how subtle mistakes
get in.

Run:  python tools/gen_hardening_migration.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "supabase" / "migrations"
OUT = MIG / "0007_function_grants_hardening.sql"

CORE = (MIG / "0001_core_schema.sql").read_text(encoding="utf-8")
GATE = (MIG / "0005_gate_and_functions.sql").read_text(encoding="utf-8")


def extract(sql: str, name: str) -> str:
    """Return the full `create or replace function public.<name>(...) ... $$;` block."""
    start = sql.index(f"create or replace function public.{name}(")
    end = sql.index("$$;", start) + len("$$;")
    return sql[start:end]


def insert_guard(block: str, guard: str) -> str:
    """Insert the guard immediately after the plpgsql `begin` of the body.

    The guard is SQL that must run INSIDE the body, so it has to land after the
    standalone `begin` line and before the first statement. Matching a
    standalone `begin` line (rather than the first occurrence of the word)
    keeps this honest if a function ever gains a nested block.
    """
    body_start = block.index("as $$") + len("as $$")
    match = re.search(r"^[ \t]*begin[ \t]*$", block[body_start:], re.MULTILINE)
    if match is None:
        raise RuntimeError("no standalone `begin` line found in the function body")
    insert_at = body_start + match.end()
    # Move past the end of the `begin` line.
    newline = block.index("\n", insert_at)
    return block[: newline + 1] + "\n" + guard.rstrip() + "\n" + block[newline + 1 :]


FIRE_ALERT_GUARD = """\
  -- ---- custody guard ------------------------------------------------------
  -- The worker (service_role) may file an alert for anybody. A signed-in user
  -- may file one only for themselves. `anon` cannot reach this function at all:
  -- see the grants at the end of this migration.
  if not public.caller_is_privileged()
     and p_user_id is distinct from auth.uid() then
    raise exception 'Not permitted: an alert may only be filed for yourself.'
      using errcode = '42501';
  end if;
"""

GET_TICKET_GUARD = """\
  -- ---- custody guard ------------------------------------------------------
  -- A ticket is computed from a user's capital, their open positions and their
  -- daily ticket count. It may therefore only be computed for the caller.
  -- The owner may compute one for anyone (that is the owner's job).
  if not public.caller_is_privileged()
     and p_user_id is distinct from auth.uid()
     and not public.is_owner() then
    raise exception 'Not permitted: a ticket may only be computed for yourself.'
      using errcode = '42501';
  end if;
"""

HEADER = """\
-- =============================================================================
-- 0007_function_grants_hardening.sql
--
-- Closes a hole found by the Supabase MCP security advisor on the live
-- project, and confirmed by hand against the anon key.
--
-- THE FINDING
--   Postgres grants EXECUTE on every new function to PUBLIC. Supabase then
--   adds explicit EXECUTE grants for `anon` and `authenticated` on all
--   functions in `public`. Migrations 0004 and 0005 only ever ADDED grants
--   (`grant execute ... to service_role`); they never revoked the defaults.
--   The result: every SECURITY DEFINER function in `public` was callable by
--   an unauthenticated caller at /rest/v1/rpc/<name>.
--
--   SECURITY DEFINER bypasses RLS, so this was not a "the grant exists but
--   the policy blocks it" situation. Confirmed against the live project with
--   the public anon key:
--
--     POST /rest/v1/rpc/record_signal  -> 200, inserted a row into public.signals
--     POST /rest/v1/rpc/claim_jobs     -> 200, executed against public.jobs
--     POST /rest/v1/rpc/fire_alert     -> 200, executed against public.alerts
--     POST /rest/v1/rpc/get_ticket     -> 200, executed against a caller-chosen
--                                          p_user_id
--
--   `public.signals` is the immutable record the Decision Center reads. An
--   anonymous caller could forge entries in it.
--
--   promote_strategy was already safe: it raises P0001 for a non-owner. That
--   was verified, not assumed.
--
-- THE FIX
--   1. Revoke EXECUTE from PUBLIC and from `anon` on every function in
--      `public`, and grant it only to the roles that need it.
--   2. Add an explicit custody guard to fire_alert() and get_ticket(), which
--      both take a caller-supplied user_id and previously trusted the caller.
--   3. Pin `search_path` on the two functions that had none
--      (function_search_path_mutable).
--   4. Stop the default privileges from granting EXECUTE to anon and
--      authenticated on functions created in future, so the hole cannot
--      reopen silently.
--
-- TABLE GRANTS ARE DELIBERATELY LEFT ALONE
--   Supabase grants table privileges to `anon` on purpose and RLS is what
--   filters rows. Every table in `public` here has RLS enabled and not one
--   policy applies to `anon` or PUBLIC, so an anonymous read returns zero
--   rows. That was verified. Revoking those grants would fight the platform's
--   model rather than close a hole.
--
-- Generated by tools/gen_hardening_migration.py. The redefined bodies are
-- byte-identical to 0001/0005 apart from the inserted guard.
-- =============================================================================

"""

FOOTER = """\
-- ------------------------------------------------------- caller role ------
-- Who is calling? PostgREST applies `set local role <jwt role>` per request,
-- so the `role` GUC is the caller's role. Supabase also exposes the decoded
-- JWT as `request.jwt.claims`, and the individual claims as
-- `request.jwt.claim.<name>`. All three are read, most specific first.
--
-- `current_user` is NOT usable here: inside a SECURITY DEFINER function it is
-- the function owner (postgres), not the caller.
create or replace function public.caller_role()
returns text
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
  v_role   text;
  v_claims text;
begin
  v_role := nullif(current_setting('request.jwt.claim.role', true), '');

  if v_role is null then
    v_claims := nullif(current_setting('request.jwt.claims', true), '');
    if v_claims is not null then
      begin
        v_role := nullif(v_claims::jsonb ->> 'role', '');
      exception when others then
        v_role := null;
      end;
    end if;
  end if;

  if v_role is null then
    v_role := nullif(current_setting('role', true), '');
  end if;

  return coalesce(v_role, '');
end;
$$;

-- ------------------------------------------------------ privileged --------
-- A caller is privileged when it is the worker, or when it is a direct
-- database session rather than an API request.
--
-- PostgREST applies `set local role <jwt role>` per request, so the `role`
-- setting is never 'none' for an API request: it is 'anon', 'authenticated'
-- or 'service_role'. A 'none' setting means no SET ROLE was applied, which is
-- a direct psql/session connection: the DBA, who can already do anything and
-- who is how the migration, the backup tool and the test harness call these
-- functions.
create or replace function public.caller_is_privileged()
returns boolean
language sql
stable
security invoker
set search_path = public
as $$
  select public.caller_role() in ('service_role', 'none', 'postgres');
$$;

-- =============================================================== grants ====
-- The rule this file enforces: a SECURITY DEFINER function in `public` is a
-- PUBLIC API ENDPOINT unless its EXECUTE grant says otherwise.

-- Worker-only. These are called by the live worker over the service-role key.
revoke all on function public.record_signal(uuid, text, public.signal_side, numeric, text,
  timestamptz, timestamptz, text, numeric, numeric, numeric, numeric, numeric, numeric,
  uuid, jsonb, jsonb, timestamptz) from public, anon, authenticated;
grant execute on function public.record_signal(uuid, text, public.signal_side, numeric, text,
  timestamptz, timestamptz, text, numeric, numeric, numeric, numeric, numeric, numeric,
  uuid, jsonb, jsonb, timestamptz) to service_role;

revoke all on function public.claim_jobs(text, integer) from public, anon, authenticated;
grant execute on function public.claim_jobs(text, integer) to service_role;

revoke all on function public.finish_job(uuid, boolean, jsonb, text) from public, anon, authenticated;
grant execute on function public.finish_job(uuid, boolean, jsonb, text) to service_role;

-- User-facing, and each one is guarded by its own custody check.
revoke all on function public.get_ticket(uuid, uuid, text) from public, anon;
grant execute on function public.get_ticket(uuid, uuid, text) to authenticated, service_role;

revoke all on function public.fire_alert(uuid, text, text, text, text, public.alert_severity, jsonb, text)
  from public, anon;
grant execute on function public.fire_alert(uuid, text, text, text, text, public.alert_severity, jsonb, text)
  to authenticated, service_role;

revoke all on function public.promote_strategy(uuid, text) from public, anon;
grant execute on function public.promote_strategy(uuid, text) to authenticated;

-- Helpers used by RLS policies. They must be callable by the policy, and by
-- nobody else.
revoke all on function public.is_owner() from public, anon;
grant execute on function public.is_owner() to authenticated, service_role;

revoke all on function public.is_owner_or_self(uuid) from public, anon;
grant execute on function public.is_owner_or_self(uuid) to authenticated, service_role;

revoke all on function public.engine_status() from public, anon;
grant execute on function public.engine_status() to authenticated, service_role;

-- A trigger function. No role has any business calling it.
revoke all on function public.set_updated_at() from public, anon, authenticated;

-- The helper above is called by the guards in this migration.
revoke all on function public.caller_role() from public, anon;
grant execute on function public.caller_role() to authenticated, service_role;

revoke all on function public.caller_is_privileged() from public, anon;
grant execute on function public.caller_is_privileged() to authenticated, service_role;

-- ================================================== future-proofing =======
-- Without this, every function created after this migration is EXECUTE-able by
-- `anon` again, by default. This is the root cause, so close it at the source.
alter default privileges in schema public revoke execute on functions from anon, authenticated;
alter default privileges in schema public revoke all on functions from public;
"""


def main() -> None:
    set_updated_at = extract(CORE, "set_updated_at")
    set_updated_at = set_updated_at.replace(
        "language plpgsql\nas $$",
        "language plpgsql\nset search_path = public\nas $$",
    )

    engine_status = extract(GATE, "engine_status")
    engine_status = engine_status.replace(
        "language sql\nstable\nas $$",
        "language sql\nstable\nset search_path = public\nas $$",
    )

    fire_alert = insert_guard(extract(GATE, "fire_alert"), FIRE_ALERT_GUARD)
    get_ticket = insert_guard(extract(GATE, "get_ticket"), GET_TICKET_GUARD)

    parts = [
        HEADER,
        "-- ------------------------------------------ set_updated_at (pinned) --\n",
        set_updated_at,
        "\n",
        "-- ------------------------------------------ engine_status (pinned) --\n",
        engine_status,
        "\n",
        "-- ------------------------------------------- fire_alert (guarded) --\n",
        fire_alert,
        "\n",
        "-- --------------------------------------------- get_ticket (guarded) --\n",
        get_ticket,
        "\n",
        FOOTER,
    ]
    OUT.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
