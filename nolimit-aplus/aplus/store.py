"""Persistence. Two interchangeable backends with the same small interface:
SQLiteStore (local default, used by tests) and SupabaseStore (PostgREST, schema `aplus`).
No secret is ever written to either."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

JSON_COLS = {"issues", "detail", "metrics", "reasons", "plan", "evidence", "data"}
PK = {"bot_allocations": "name", "candidates": "candidate_key", "trades": "trade_key",
      "orders": "client_oid", "heartbeats": "component"}

SQLITE_DDL = """
create table if not exists account_snapshots (id integer primary key autoincrement, ts text not null, mode text not null,
  equity real not null, available real not null, unrealized_pnl real not null default 0, gross_exposure real not null default 0,
  liabilities real not null default 0, complete integer not null, issues text not null default '[]', detail text not null default '{}');
create table if not exists bot_allocations (name text primary key, kind text not null, capital_usdt real not null,
  pnl_usdt real not null default 0, source text not null default 'manual', note text not null default '', updated_at text not null);
create table if not exists validation_pools (id integer primary key autoincrement, run_id text not null, ts text not null,
  strategy text not null, interval text not null, grp text not null, status text not null, metrics text not null,
  unique (run_id, strategy, interval, grp));
create table if not exists validation_symbols (id integer primary key autoincrement, run_id text not null, ts text not null,
  symbol text not null, strategy text not null, interval text not null, status text not null, metrics text not null,
  unique (run_id, symbol, strategy, interval));
create table if not exists candidates (candidate_key text primary key, ts text not null, symbol text not null, strategy text not null,
  interval text not null, side integer not null check (side in (-1,1)), signal_ts integer not null, grade text not null,
  validation_status text not null, approved integer not null, reasons text not null default '[]', plan text, evidence text not null default '{}');
create table if not exists trades (trade_key text primary key, mode text not null check (mode in ('demo','paper')), symbol text not null,
  strategy text not null, interval text not null, side integer not null check (side in (-1,1)), signal_ts integer not null, state text not null,
  plan text not null, qty_open real not null default 0, entry_fill real, stop_current real, realized_pnl real not null default 0,
  fees real not null default 0, net_r real, exit_reason text, opened_at text, closed_at text, created_at text not null, updated_at text not null);
create table if not exists orders (client_oid text primary key, trade_key text not null references trades(trade_key), role text not null
  check (role in ('entry','stop','tp1','tp2','flatten')), exchange_order_id text, status text not null, side text not null, qty real not null,
  price real, trigger_price real, reduce_only integer not null, detail text not null default '{}', created_at text not null, updated_at text not null,
  check (role = 'entry' or reduce_only = 1));
create table if not exists fills (id integer primary key autoincrement, ts text not null, exec_id text not null unique,
  client_oid text, trade_key text not null, role text not null, price real not null, qty real not null, fee real not null default 0);
create table if not exists events (id integer primary key autoincrement, ts text not null, level text not null, kind text not null,
  message text not null, data text not null default '{}');
create table if not exists heartbeats (component text primary key, ts text not null, status text not null, detail text not null default '{}');
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DuplicateKey(RuntimeError):
    pass


class Store:
    def insert(self, table: str, row: dict) -> None: raise NotImplementedError
    def upsert(self, table: str, row: dict) -> None: raise NotImplementedError
    def update(self, table: str, where: dict, values: dict) -> int: raise NotImplementedError
    def select(self, table: str, where: dict | None = None, order: str | None = None,
               limit: int | None = None) -> list[dict]: raise NotImplementedError

    # ---- convenience shared by both backends
    def event(self, level: str, kind: str, message: str, data: dict | None = None) -> None:
        self.insert("events", {"ts": now_iso(), "level": level, "kind": kind, "message": message, "data": data or {}})

    def heartbeat(self, component: str, status: str, detail: dict | None = None) -> None:
        self.upsert("heartbeats", {"component": component, "ts": now_iso(), "status": status, "detail": detail or {}})

    def one(self, table: str, where: dict) -> dict | None:
        rows = self.select(table, where, limit=1)
        return rows[0] if rows else None

    def period_start_equity(self, mode: str, since_iso: str) -> float | None:
        """Equity of the first COMPLETE snapshot at/after `since_iso` (circuit-breaker reference)."""
        rows = [r for r in self.select("account_snapshots", {"mode": mode}, order="ts.asc")
                if r["ts"] >= since_iso and r["complete"]]
        return float(rows[0]["equity"]) if rows else None


class SQLiteStore(Store):
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("pragma foreign_keys = on")
        self._db.execute("pragma journal_mode = wal")
        self._db.executescript(SQLITE_DDL)
        self._lock = threading.RLock()

    @staticmethod
    def _enc(v: Any) -> Any:
        if isinstance(v, (dict, list)):
            return json.dumps(v, default=str)
        if isinstance(v, bool):
            return int(v)
        return v

    @staticmethod
    def _dec(row: sqlite3.Row) -> dict:
        d = dict(row)
        for k in JSON_COLS & d.keys():
            if isinstance(d[k], str):
                d[k] = json.loads(d[k])
        for k in ("complete", "approved", "reduce_only"):
            if k in d and d[k] is not None:
                d[k] = bool(d[k])
        return d

    def insert(self, table: str, row: dict) -> None:
        cols = ",".join(row)
        sql = f"insert into {table} ({cols}) values ({','.join('?' * len(row))})"
        with self._lock:
            try:
                self._db.execute(sql, [self._enc(v) for v in row.values()])
            except sqlite3.IntegrityError as exc:
                if "UNIQUE" in str(exc) or "PRIMARY" in str(exc):
                    raise DuplicateKey(f"{table}: {exc}") from exc
                raise

    def upsert(self, table: str, row: dict) -> None:
        pk = PK[table]
        cols = ",".join(row)
        sets = ",".join(f"{c}=excluded.{c}" for c in row if c != pk)
        sql = f"insert into {table} ({cols}) values ({','.join('?' * len(row))}) on conflict({pk}) do update set {sets}"
        with self._lock:
            self._db.execute(sql, [self._enc(v) for v in row.values()])

    def update(self, table: str, where: dict, values: dict) -> int:
        sql = (f"update {table} set {','.join(f'{c}=?' for c in values)} "
               f"where {' and '.join(f'{c}=?' for c in where)}")
        with self._lock:
            cur = self._db.execute(sql, [self._enc(v) for v in values.values()] + list(where.values()))
            return cur.rowcount

    def select(self, table, where=None, order=None, limit=None):
        sql = f"select * from {table}"
        args: list = []
        if where:
            sql += " where " + " and ".join(f"{c}=?" for c in where)
            args = [self._enc(v) for v in where.values()]
        if order:
            col, _, direction = order.partition(".")
            sql += f" order by {col} {'desc' if direction == 'desc' else 'asc'}"
        if limit:
            sql += f" limit {int(limit)}"
        with self._lock:
            return [self._dec(r) for r in self._db.execute(sql, args).fetchall()]


class SupabaseStore(Store):
    """PostgREST client for schema `aplus` (expose the schema in Supabase API settings)."""

    def __init__(self, url: str, service_key: str, session: requests.Session | None = None):
        if not url or not service_key:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for the supabase store")
        self.base = url.rstrip("/") + "/rest/v1"
        self._s = session or requests.Session()
        self._h = {"apikey": service_key, "Authorization": f"Bearer {service_key}",
                   "Content-Type": "application/json", "Accept-Profile": "aplus", "Content-Profile": "aplus"}

    def _req(self, method: str, table: str, params: dict | None = None, body: Any = None, prefer: str = "") -> Any:
        h = dict(self._h)
        if prefer:
            h["Prefer"] = prefer
        r = self._s.request(method, f"{self.base}/{table}", params=params, json=body, headers=h, timeout=20)
        if r.status_code == 409:
            raise DuplicateKey(f"{table}: {r.text[:200]}")
        if r.status_code >= 400:
            raise RuntimeError(f"supabase {method} {table} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else None

    def insert(self, table, row):
        self._req("POST", table, body=row, prefer="return=minimal")

    def upsert(self, table, row):
        self._req("POST", table, params={"on_conflict": PK[table]}, body=row,
                  prefer="resolution=merge-duplicates,return=minimal")

    def update(self, table, where, values):
        res = self._req("PATCH", table, params={k: f"eq.{v}" for k, v in where.items()}, body=values,
                        prefer="return=representation")
        return len(res or [])

    def select(self, table, where=None, order=None, limit=None):
        params: dict = {"select": "*"}
        for k, v in (where or {}).items():
            params[k] = f"eq.{str(v).lower() if isinstance(v, bool) else v}"
        if order:
            params["order"] = order
        if limit:
            params["limit"] = str(limit)
        return self._req("GET", table, params=params) or []


def open_store(settings) -> Store:
    if settings.store == "supabase":
        return SupabaseStore(settings.supabase_url, settings.supabase_key)
    return SQLiteStore(settings.sqlite_path)
