-- NO LIMIT A+++ : Supabase / Postgres schema
-- Security model: RLS enabled on every table with NO policies for anon/authenticated.
-- Only the service_role key (used server-side by the Python engine) can read/write.
-- No API keys or secrets are ever stored in this database.

create schema if not exists aplus;

create table if not exists aplus.account_snapshots (
  id            bigint generated always as identity primary key,
  ts            timestamptz not null default now(),
  mode          text not null check (mode in ('production_readonly','demo','paper')),
  equity        numeric not null,
  available     numeric not null,
  unrealized_pnl numeric not null default 0,
  gross_exposure numeric not null default 0,
  liabilities   numeric not null default 0,
  complete      boolean not null,
  issues        jsonb not null default '[]',
  detail        jsonb not null default '{}'
);
create index if not exists account_snapshots_mode_ts on aplus.account_snapshots (mode, ts desc);

create table if not exists aplus.bot_allocations (
  name          text primary key,
  kind          text not null,
  capital_usdt  numeric not null,
  pnl_usdt      numeric not null default 0,
  source        text not null default 'manual',
  note          text not null default '',
  updated_at    timestamptz not null default now()
);

create table if not exists aplus.validation_pools (
  id            bigint generated always as identity primary key,
  run_id        text not null,
  ts            timestamptz not null default now(),
  strategy      text not null,
  interval      text not null,
  grp           text not null,
  status        text not null check (status in ('REJECTED','OOS_PASSED_FORWARD_PENDING','VALIDATED')),
  metrics       jsonb not null,
  unique (run_id, strategy, interval, grp)
);

create table if not exists aplus.validation_symbols (
  id            bigint generated always as identity primary key,
  run_id        text not null,
  ts            timestamptz not null default now(),
  symbol        text not null,
  strategy      text not null,
  interval      text not null,
  status        text not null check (status in ('REJECTED','OOS_PASSED_FORWARD_PENDING','VALIDATED')),
  metrics       jsonb not null,
  unique (run_id, symbol, strategy, interval)
);
create index if not exists validation_symbols_lookup on aplus.validation_symbols (symbol, strategy, interval, ts desc);

create table if not exists aplus.candidates (
  candidate_key text primary key,              -- symbol|strategy|interval|signal_ts : one row per signal
  ts            timestamptz not null default now(),
  symbol        text not null,
  strategy      text not null,
  interval      text not null,
  side          smallint not null check (side in (-1, 1)),
  signal_ts     bigint not null,
  grade         text not null,                 -- A+++ | A+ (paper only) | REJECTED
  validation_status text not null,
  approved      boolean not null,
  reasons       jsonb not null default '[]',
  plan          jsonb,
  evidence      jsonb not null default '{}'
);
create index if not exists candidates_ts on aplus.candidates (ts desc);

create table if not exists aplus.trades (
  trade_key     text primary key,              -- mode|candidate_key : idempotency key for execution
  mode          text not null check (mode in ('demo','paper')),   -- there is no 'live'
  symbol        text not null,
  strategy      text not null,
  interval      text not null,
  side          smallint not null check (side in (-1, 1)),
  signal_ts     bigint not null,
  state         text not null,
  plan          jsonb not null,
  qty_open      numeric not null default 0,
  entry_fill    numeric,
  stop_current  numeric,
  realized_pnl  numeric not null default 0,
  fees          numeric not null default 0,
  net_r         numeric,
  exit_reason   text,
  opened_at     timestamptz,
  closed_at     timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists trades_state on aplus.trades (mode, state);

create table if not exists aplus.orders (
  client_oid    text primary key,              -- deterministic: duplicate submissions collide here first
  trade_key     text not null references aplus.trades (trade_key),
  role          text not null check (role in ('entry','stop','tp1','tp2','flatten')),
  exchange_order_id text,
  status        text not null,
  side          text not null,
  qty           numeric not null,
  price         numeric,
  trigger_price numeric,
  reduce_only   boolean not null,
  detail        jsonb not null default '{}',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists orders_trade on aplus.orders (trade_key);

create table if not exists aplus.fills (
  id            bigint generated always as identity primary key,
  ts            timestamptz not null default now(),
  exec_id       text not null unique,          -- exchange execution id: a fill is never booked twice
  client_oid    text,
  trade_key     text not null,
  role          text not null,
  price         numeric not null,
  qty           numeric not null,
  fee           numeric not null default 0
);
create index if not exists fills_trade on aplus.fills (trade_key);

create table if not exists aplus.events (
  id            bigint generated always as identity primary key,
  ts            timestamptz not null default now(),
  level         text not null,
  kind          text not null,
  message       text not null,
  data          jsonb not null default '{}'
);
create index if not exists events_ts on aplus.events (ts desc);

create table if not exists aplus.heartbeats (
  component     text primary key,
  ts            timestamptz not null default now(),
  status        text not null,
  detail        jsonb not null default '{}'
);

-- a trade can never be recorded as live, and a filled non-entry order must be reduce-only
alter table aplus.orders drop constraint if exists orders_exit_reduce_only;
alter table aplus.orders add constraint orders_exit_reduce_only check (role = 'entry' or reduce_only);

do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'aplus' loop
    execute format('alter table aplus.%I enable row level security', t);
    execute format('alter table aplus.%I force row level security', t);
  end loop;
end $$;

revoke all on schema aplus from anon, authenticated;
revoke all on all tables in schema aplus from anon, authenticated;
grant usage on schema aplus to service_role;
grant all on all tables in schema aplus to service_role;
grant all on all sequences in schema aplus to service_role;
