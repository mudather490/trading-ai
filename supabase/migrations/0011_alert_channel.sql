-- =============================================================================
-- 0011_alert_channel.sql
--
-- Alerts now say where they are destined.
--
-- `alerts.channel` exists (0010) but `fire_alert()` could not set it, so every
-- alert was born as 'in_app' and the Telegram worker had no way to find the
-- ones it should deliver. This migration replaces the function with the same
-- body plus one parameter, so the channel is chosen at the moment the alert
-- is filed and cannot drift afterwards.
--
-- The custody guard is unchanged: a signed-in user may file an alert only
-- for themselves; the service role (the worker) may file for anybody.
-- =============================================================================

drop function if exists public.fire_alert(
  uuid, text, text, text, text, public.alert_severity, jsonb, text
);

create or replace function public.fire_alert(
  p_user_id uuid,
  p_kind text,
  p_rule_id text,
  p_title text,
  p_body text default null,
  p_severity public.alert_severity default 'info',
  p_payload jsonb default '{}'::jsonb,
  p_dedupe_key text default null,
  p_channel text default 'in_app'
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_key text;
  v_id uuid;
  v_channel text := case
    when p_channel = 'telegram' then 'telegram'
    else 'in_app'
  end;
begin

  -- ---- custody guard ------------------------------------------------------
  -- The worker (service_role) may file an alert for anybody. A signed-in user
  -- may file one only for themselves. `anon` cannot reach this function at
  -- all: see the grants at the end of this migration.
  if not public.caller_is_privileged()
     and p_user_id is distinct from auth.uid() then
    raise exception 'Not permitted: an alert may only be filed for yourself.'
      using errcode = '42501';
  end if;

  v_key := coalesce(p_dedupe_key,
    p_kind || ':' || p_rule_id || ':' || coalesce(p_payload->>'instrument_id','') ||
    ':' || coalesce(p_payload->>'bar_close_time', now()::text));

  insert into public.alerts (
    user_id, kind, rule_id, title, body, severity, payload, dedupe_key, channel
  ) values (
    p_user_id, p_kind, p_rule_id, p_title, p_body, p_severity, p_payload, v_key,
    v_channel
  )
  on conflict (dedupe_key) do nothing
  returning id into v_id;

  return v_id;
end;
$$;

revoke all on function public.fire_alert(
  uuid, text, text, text, text, public.alert_severity, jsonb, text, text
) from public, anon;

grant execute on function public.fire_alert(
  uuid, text, text, text, text, public.alert_severity, jsonb, text, text
) to authenticated, service_role;

comment on function public.fire_alert is
  'Files one alert for one user. p_channel is ''in_app'' or ''telegram''; it is fixed at filing time.';
