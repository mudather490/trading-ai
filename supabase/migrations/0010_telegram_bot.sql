-- =============================================================================
-- 0010_telegram_bot.sql
--
-- The Telegram bot: link a chat to a user, take commands, and deliver alerts.
--
-- The trust model, in one paragraph. A Telegram chat proves nothing about
-- who is holding the phone, so a chat is NOT trusted on its own. Linking is a
-- two-sided handshake: the signed-in browser asks the database for a
-- single-use code (10 minutes, stored only as a SHA-256 hash), the user
-- types that code into the bot, and only then are the two joined. After that,
-- the chat inherits exactly the user's rights and nothing more: it can read
-- that user's tickets and receive that user's alerts, and it can never read
-- another user's.
--
-- Four tables:
--   telegram_link_codes  the one-time codes (hash only, never the code)
--   telegram_links       chat <-> user, revocable
--   telegram_updates     every update Telegram sent, for dedupe and replay
--   alerts.channel       which channel an alert is destined for
--
-- Every write here is either made by a SECURITY DEFINER function with an
-- explicit grant, or made by the service role in the webhook. A signed-in
-- user can create their own code and read their own link. Nothing else.
-- =============================================================================

-- ------------------------------------------------------- link codes -------

create table public.telegram_link_codes (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users(id) on delete cascade,
  -- The SHA-256 of the code. The code itself is shown once and never stored.
  code_hash  text        not null unique,
  expires_at timestamptz not null,
  used_at    timestamptz,
  created_at timestamptz not null default now()
);

create index telegram_link_codes_user_idx
  on public.telegram_link_codes (user_id, created_at desc);

comment on table public.telegram_link_codes is
  'Single-use link codes, stored as SHA-256 hashes. Ten minute lifetime.';

-- ----------------------------------------------------------- links -------

create table public.telegram_links (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users(id) on delete cascade,
  chat_id    bigint      not null unique,
  username   text,
  linked_at  timestamptz not null default now(),
  revoked_at timestamptz
);

create index telegram_links_user_idx on public.telegram_links (user_id);

comment on table public.telegram_links is
  'A Telegram chat bound to a user. Revoking a link stops every delivery to it.';

-- --------------------------------------------------------- updates -------

create table public.telegram_updates (
  update_id   bigint primary key,
  chat_id     bigint,
  telegram_user_id bigint,
  message     text,
  payload     jsonb       not null default '{}'::jsonb,
  received_at timestamptz not null default now(),
  handled_at  timestamptz
);

create index telegram_updates_received_idx
  on public.telegram_updates (received_at desc);

comment on table public.telegram_updates is
  'Every update Telegram delivered. The primary key IS the dedupe: a replayed update is a no-op.';

-- ---------------------------------------------------------- alerts -------

alter table public.alerts
  add column if not exists channel text not null default 'in_app';

comment on column public.alerts.channel is
  'Where this alert is destined: in_app | telegram. The worker delivers telegram alerts.';

-- -------------------------------------------------------------- RLS -------

alter table public.telegram_link_codes enable row level security;
alter table public.telegram_links enable row level security;
alter table public.telegram_updates enable row level security;

-- The user may request codes and read their own. They may not read anyone
-- else's code, and the code is useless without the chat half of the pair.
create policy telegram_link_codes_own on public.telegram_link_codes
  for select to authenticated using (public.is_owner_or_self(user_id));

create policy telegram_link_codes_insert_own on public.telegram_link_codes
  for insert to authenticated with check (auth.uid() = user_id);

create policy telegram_link_codes_delete_own on public.telegram_link_codes
  for delete to authenticated using (auth.uid() = user_id);

-- A user may see their own link (and so know it worked). Only the owner (the
-- webhook) may create, change or revoke one.
create policy telegram_links_read_own on public.telegram_links
  for select to authenticated using (public.is_owner_or_self(user_id));

create policy telegram_links_owner_write on public.telegram_links
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

-- Updates are a webhook-side ledger. Only the service role touches them.
create policy telegram_updates_service on public.telegram_updates
  for all to service_role using (true) with check (true);

-- ------------------------------------------------------ the functions ----

-- Mint a single-use link code for the caller.
--
-- The plaintext code is returned ONCE, in the response. The database keeps
-- only its SHA-256, so a database dump does not hand anyone a working link.
create or replace function public.create_telegram_link_code()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_code text;
  v_hash text;
  v_expires timestamptz;
begin
  if v_user is null then
    raise exception 'Not signed in.' using errcode = '42501';
  end if;

  -- Invalidate any earlier unused code: one live code per user.
  update public.telegram_link_codes
     set used_at = now()
   where user_id = v_user and used_at is null;

  v_code := substr(md5(random()::text || clock_timestamp()::text || v_user::text), 1, 10);
  v_hash := encode(sha256(convert_to(v_code, 'UTF8')), 'hex');
  v_expires := now() + interval '10 minutes';

  insert into public.telegram_link_codes (user_id, code_hash, expires_at)
  values (v_user, v_hash, v_expires);

  return jsonb_build_object(
    'code', v_code,
    'expires_at', v_expires,
    'expires_in_seconds', 600
  );
end;
$$;

revoke all on function public.create_telegram_link_code() from public, anon;
grant execute on function public.create_telegram_link_code() to authenticated;

comment on function public.create_telegram_link_code() is
  'Returns a single-use, 10-minute link code for the caller. The code is never stored in plaintext.';

-- Redeem a code from a chat. Service role only: this is the webhook half.
create or replace function public.redeem_telegram_link(
  p_code text,
  p_chat_id bigint,
  p_username text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user  uuid;
  v_row   public.telegram_link_codes%rowtype;
begin
  if p_code is null or length(trim(p_code)) < 6 then
    return jsonb_build_object('ok', false, 'reason', 'That code is not valid.');
  end if;

  select * into v_row
    from public.telegram_link_codes
   where code_hash = encode(sha256(convert_to(trim(p_code), 'UTF8')), 'hex')
     and used_at is null
     and expires_at > now()
   limit 1;

  if not found then
    return jsonb_build_object(
      'ok', false,
      'reason', 'That code is expired, already used, or not a code.'
    );
  end if;

  v_user := v_row.user_id;
  update public.telegram_link_codes set used_at = now() where id = v_row.id;

  -- One chat, one user. A chat that relinks replaces the old binding; a user
  -- who links a second chat loses the first, so a chat cannot accumulate
  -- access.
  delete from public.telegram_links where user_id = v_user;
  insert into public.telegram_links (user_id, chat_id, username)
  values (v_user, p_chat_id, p_username)
  on conflict (chat_id) do update
    set user_id = excluded.user_id,
        username = excluded.username,
        linked_at = now(),
        revoked_at = null;

  return jsonb_build_object('ok', true, 'user_id', v_user);
end;
$$;

revoke all on function public.redeem_telegram_link(text, bigint, text)
  from public, anon, authenticated;
grant execute on function public.redeem_telegram_link(text, bigint, text)
  to service_role;

-- Revoke a link. The user may revoke their own; the owner may revoke any.
create or replace function public.revoke_telegram_link(
  p_chat_id bigint default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_count integer;
begin
  if v_user is null then
    raise exception 'Not signed in.' using errcode = '42501';
  end if;

  update public.telegram_links
     set revoked_at = now()
   where revoked_at is null
     and (p_chat_id is null or chat_id = p_chat_id)
     and (user_id = v_user or public.is_owner());

  get diagnostics v_count = row_count;
  return jsonb_build_object('ok', true, 'revoked', v_count);
end;
$$;

revoke all on function public.revoke_telegram_link(bigint) from public, anon;
grant execute on function public.revoke_telegram_link(bigint) to authenticated;
