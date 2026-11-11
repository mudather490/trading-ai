-- =============================================================================
-- 0006_seed_universe.sql
--
-- The 12-instrument universe. Each row carries its DATA QUALITY honestly,
-- because that fact must reach the UI: a ticket built on IEX-only data
-- looks identical to one built on consolidated data unless we label it.
--
--   official    the venue's own full-market feed
--   partial     a real feed covering only part of the market
--   indicative  a broker's practice/demo pricing, not an executable feed
-- =============================================================================

insert into public.instruments
  (symbol, venue, asset_class, display_name, currency, tick_size, session_tz,
   in_universe, primary_source, data_quality, notes)
values
  -- ------------------------------------------------ crypto: full market ----
  ('BTCUSDT',  'binance', 'crypto', 'Bitcoin / USDT',  'USDT', 0.01,  'UTC', true,
   'binance_ws', 'official',
   'Binance public stream. Full market, real-time, keyless.'),
  ('ETHUSDT',  'binance', 'crypto', 'Ethereum / USDT',  'USDT', 0.01,  'UTC', true,
   'binance_ws', 'official',
   'Binance public stream. Full market, real-time, keyless.'),
  ('SOLUSDT',  'binance', 'crypto', 'Solana / USDT',    'USDT', 0.01,  'UTC', true,
   'binance_ws', 'official',
   'Binance public stream. Full market, real-time, keyless.'),

  -- ------------------------------- US equities: IEX only (partial) --------
  ('QQQ',      'alpaca',  'us_equity', 'Invesco QQQ Trust',        'USD', 0.01, 'America/New_York', true,
   'alpaca_iex', 'partial',
   'Alpaca Basic plan: IEX only. A fraction of consolidated volume. SWING ONLY.'),
  ('SPY',      'alpaca',  'us_equity', 'SPDR S&P 500 ETF Trust',   'USD', 0.01, 'America/New_York', true,
   'alpaca_iex', 'partial',
   'Alpaca Basic plan: IEX only. A fraction of consolidated volume. SWING ONLY.'),
  ('GLD',      'alpaca',  'us_equity', 'SPDR Gold Shares',         'USD', 0.01, 'America/New_York', true,
   'alpaca_iex', 'partial',
   'Alpaca Basic plan: IEX only. The defensive leg of the S18 rotation.'),
  ('AAPL',     'alpaca',  'us_equity', 'Apple Inc.',               'USD', 0.01, 'America/New_York', true,
   'alpaca_iex', 'partial',
   'Alpaca Basic plan: IEX only. SWING ONLY.'),
  ('NVDA',     'alpaca',  'us_equity', 'NVIDIA Corporation',       'USD', 0.01, 'America/New_York', true,
   'alpaca_iex', 'partial',
   'Alpaca Basic plan: IEX only. SWING ONLY.'),
  ('TLT',      'alpaca',  'us_equity', 'iShares 20+ Year Treasury Bond ETF', 'USD', 0.01, 'America/New_York', true,
   'alpaca_iex', 'partial',
   'Alpaca Basic plan: IEX only. Rate sensitivity reference.'),

  -- --------------------------------------- forex and metals: indicative ---
  ('EURUSD',   'oanda',   'forex', 'Euro / US Dollar',  'USD', 0.00001, 'UTC', true,
   'oanda_practice', 'indicative',
   'OANDA PRACTICE account. Indicative rates, not an executable feed.'),
  ('GBPUSD',   'oanda',   'forex', 'Pound / US Dollar', 'USD', 0.00001, 'UTC', true,
   'oanda_practice', 'indicative',
   'OANDA PRACTICE account. Indicative rates, not an executable feed.'),
  ('XAUUSD',   'oanda',   'metal', 'Gold / US Dollar',  'USD', 0.01,     'UTC', true,
   'oanda_practice', 'indicative',
   'OANDA PRACTICE account. Indicative rates, not an executable feed.')
on conflict (symbol) do update
  set venue         = excluded.venue,
      asset_class   = excluded.asset_class,
      display_name  = excluded.display_name,
      currency      = excluded.currency,
      tick_size     = excluded.tick_size,
      session_tz    = excluded.session_tz,
      in_universe   = excluded.in_universe,
      primary_source = excluded.primary_source,
      data_quality  = excluded.data_quality,
      notes         = excluded.notes,
      updated_at    = now();

comment on table public.instruments is
  'The tradable universe. in_universe=false instruments are tracked but never ticketed. '
  'data_quality is surfaced in the UI so a partial feed is never mistaken for the whole market.';
