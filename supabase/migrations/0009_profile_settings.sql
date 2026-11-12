-- =============================================================================
-- 0009_profile_settings.sql
--
-- The settings screen needs one write path, and it must not be a hole in the
-- custody boundary.
--
-- `user_profiles` already has an RLS update policy, so a signed-in user can
-- change their own numbers. But two things must NOT be left to the client:
--
--   1. The caps. `risk_per_trade_pct <= 2%`, `max_position_pct <= 50%`, and
--      so on are safety limits, not preferences. A browser that posts 90%
--      risk must be clamped, not obeyed.
--   2. The audit trail. `audit_log` is owner-readable and has no insert
--      policy for users, because an audit row a user can forge is not an
--      audit row. A SECURITY DEFINER function is the only place allowed to
--      write it.
--
-- So the clamp and the audit entry live together, in one function, next to
-- the data they protect. The web tier calls this RPC with the user's own
-- session; it never needs the service-role key for settings.
--
-- Role is deliberately NOT updatable here. Only `promote_strategy()`-style
-- owner paths may change who someone is.
-- =============================================================================

create or replace function public.update_my_profile(
  p_display_name text default null,
  p_capital_usd numeric default null,
  p_risk_per_trade_pct numeric default null,
  p_max_position_pct numeric default null,
  p_max_gross_pct numeric default null,
  p_max_correlated_pct numeric default null,
  p_max_concurrent integer default null,
  p_max_tickets_per_day integer default null,
  p_timezone text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user     uuid := auth.uid();
  v_before   public.user_profiles%rowtype;
  v_after    public.user_profiles%rowtype;
  v_risk     numeric(6,4);
  v_position numeric(6,4);
  v_gross    numeric(6,4);
  v_corr     numeric(6,4);
  v_conc     integer;
  v_tickets  integer;
  v_tz       text;
begin
  if v_user is null then
    raise exception 'Not signed in.' using errcode = '42501';
  end if;

  -- A first sign-in may not have a profile row yet. Create the defaults, then
  -- apply the requested values to it.
  insert into public.user_profiles (user_id)
  values (v_user)
  on conflict (user_id) do nothing;

  select * into v_before from public.user_profiles where user_id = v_user;

  -- ---- clamps -------------------------------------------------------------
  -- The floor matters as much as the ceiling: a zero risk budget would make
  -- every ticket size zero, which is not a setting, it is a bug.
  v_risk := least(greatest(coalesce(p_risk_per_trade_pct, v_before.risk_per_trade_pct), 0.0001), 0.02);
  v_position := least(greatest(coalesce(p_max_position_pct, v_before.max_position_pct), 0.0001), 0.50);
  v_gross := least(greatest(coalesce(p_max_gross_pct, v_before.max_gross_pct), 0.01), 2.00);
  v_corr := least(greatest(coalesce(p_max_correlated_pct, v_before.max_correlated_pct), 0.01), 1.00);
  v_conc := least(greatest(coalesce(p_max_concurrent, v_before.max_concurrent), 1), 10);
  v_tickets := least(greatest(coalesce(p_max_tickets_per_day, v_before.max_tickets_per_day), 1), 20);

  if p_capital_usd is not null and p_capital_usd <= 0 then
    raise exception 'Capital must be greater than zero.' using errcode = '22023';
  end if;

  v_tz := coalesce(nullif(trim(p_timezone), ''), v_before.timezone);
  if length(v_tz) > 64 then
    raise exception 'Timezone name is too long.' using errcode = '22023';
  end if;

  update public.user_profiles
     set display_name = coalesce(nullif(trim(p_display_name), ''), display_name),
         capital_usd = coalesce(p_capital_usd, capital_usd),
         risk_per_trade_pct = v_risk,
         max_position_pct = v_position,
         max_gross_pct = v_gross,
         max_correlated_pct = v_corr,
         max_concurrent = v_conc,
         max_tickets_per_day = v_tickets,
         timezone = v_tz
   where user_id = v_user
  returning * into v_after;

  -- ---- the audit trail ----------------------------------------------------
  insert into public.audit_log (actor, action, entity, entity_id, before, after)
  values (
    v_user,
    'profile.update',
    'user_profiles',
    v_user::text,
    jsonb_build_object(
      'display_name', v_before.display_name,
      'capital_usd', v_before.capital_usd,
      'risk_per_trade_pct', v_before.risk_per_trade_pct,
      'max_position_pct', v_before.max_position_pct,
      'max_gross_pct', v_before.max_gross_pct,
      'max_correlated_pct', v_before.max_correlated_pct,
      'max_concurrent', v_before.max_concurrent,
      'max_tickets_per_day', v_before.max_tickets_per_day,
      'timezone', v_before.timezone
    ),
    jsonb_build_object(
      'display_name', v_after.display_name,
      'capital_usd', v_after.capital_usd,
      'risk_per_trade_pct', v_after.risk_per_trade_pct,
      'max_position_pct', v_after.max_position_pct,
      'max_gross_pct', v_after.max_gross_pct,
      'max_correlated_pct', v_after.max_correlated_pct,
      'max_concurrent', v_after.max_concurrent,
      'max_tickets_per_day', v_after.max_tickets_per_day,
      'timezone', v_after.timezone
    )
  );

  return to_jsonb(v_after);
end;
$$;

revoke all on function public.update_my_profile(
  text, numeric, numeric, numeric, numeric, numeric, integer, integer, text
) from public, anon;

grant execute on function public.update_my_profile(
  text, numeric, numeric, numeric, numeric, numeric, integer, integer, text
) to authenticated;

comment on function public.update_my_profile is
  'Clamps and persists the caller''s own risk settings, and writes the audit entry. Role changes are not possible here.';
