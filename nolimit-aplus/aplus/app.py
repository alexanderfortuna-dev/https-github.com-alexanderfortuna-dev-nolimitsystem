"""Wiring: builds the clients, store, risk engine, broker and execution engine from Settings."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .audit import AuditReport, run_audit
from .bitget.client import BitgetClient
from .bitget.models import Instrument
from .config import ROOT, Settings
from .execution.broker import PaperBroker
from .execution.engine import ExecutionEngine
from .risk import KillSwitch, RiskEngine
from .scanner import load_validation, scan
from .store import Store, now_iso, open_store
from .universe import CATEGORY, UniverseMember, build_universe

log = logging.getLogger("aplus.app")
REPORT_DIR = ROOT / "reports"


class TickerCache:
    """Live last prices for the paper broker, refreshed at most every `ttl` seconds."""

    def __init__(self, client: BitgetClient, ttl: float = 2.0):
        self.c, self.ttl, self._at, self._px = client, ttl, 0.0, {}

    def __call__(self, symbol: str) -> tuple[float, float]:
        if time.time() - self._at > self.ttl:
            self._px = {t["symbol"]: (float(t["lastPrice"]), float(t["ts"]) / 1000) for t in self.c.tickers(CATEGORY)}
            self._at = time.time()
        return self._px[symbol]


@dataclass
class App:
    settings: Settings
    public: BitgetClient
    store: Store
    kill: KillSwitch
    risk: RiskEngine
    engine: ExecutionEngine
    instruments: dict[str, Instrument]

    @classmethod
    def build(cls, settings: Settings | None = None) -> "App":
        s = settings or Settings.from_env()
        public = BitgetClient()
        store = open_store(s)
        kill = KillSwitch(s.var_dir / "KILL")
        risk = RiskEngine(s.risk, kill)
        insts = {d["symbol"]: Instrument.from_api(d) for d in public.instruments(CATEGORY)}
        if s.execution_mode == "demo":
            from .execution.demo import DemoBroker
            if not s.demo_creds.present:
                raise SystemExit("APLUS_EXECUTION_MODE=demo requires BITGET_DEMO_API_* keys in .env")
            demo_client = BitgetClient(s.demo_creds, demo=True)
            demo_client.sync_clock()
            broker = DemoBroker(demo_client)
        else:
            import os
            broker = PaperBroker(TickerCache(public), float(os.environ.get("APLUS_PAPER_EQUITY") or 10_000))
            broker.load(s.var_dir / "paper_state.json")
            for sym in broker.pos:
                broker.prepare(insts[sym], 1)
        engine = ExecutionEngine(broker, store, risk, kill, insts, s.risk)
        return cls(s, public, store, kill, risk, engine, insts)

    def tick(self) -> None:
        self.engine.tick()
        if isinstance(self.engine.b, PaperBroker):
            self.engine.b.save(self.settings.var_dir / "paper_state.json")

    # ------------------------------------------------------------------ operations
    def audit(self, manual_bots_usdt: float | None = None) -> AuditReport:
        if not self.settings.read_creds.present:
            raise SystemExit("Account audit needs the READ-ONLY production key (BITGET_READ_API_* in .env).")
        client = BitgetClient(self.settings.read_creds)
        client.sync_clock()
        rep = run_audit(client, manual_bots_usdt=manual_bots_usdt)
        self.store.insert("account_snapshots", {
            "ts": now_iso(), "mode": "production_readonly", "equity": rep.total_equity,
            "available": rep.available_usdt, "unrealized_pnl": rep.unrealized_pnl,
            "gross_exposure": rep.gross_exposure, "liabilities": rep.liabilities_usd, "complete": rep.complete,
            "issues": rep.issues, "detail": rep.to_dict()})
        for name, val in (rep.bots.get("accounts") or {}).items():
            if val is not None:
                self.store.upsert("bot_allocations", {"name": f"bot_account_{name}", "kind": f"grid_{name}",
                                                      "capital_usdt": val, "pnl_usdt": 0.0, "source": rep.bots["source"],
                                                      "note": "", "updated_at": now_iso()})
        for b in rep.bots.get("bots") or []:
            self.store.upsert("bot_allocations", {"name": f"bot_{b['botId']}", "kind": f"grid:{b.get('symbol')}",
                                                  "capital_usdt": float(b.get("margin") or 0), "source": "api",
                                                  "pnl_usdt": float(b.get("totalProfit") or 0),
                                                  "note": f"status={b.get('status')} roi={b.get('roi')}",
                                                  "updated_at": now_iso()})
        self.store.heartbeat("audit", "ok" if rep.complete else "incomplete", {"issues": rep.issues})
        return rep

    def universe(self) -> list[UniverseMember]:
        return build_universe(self.public)

    def scan(self, execute: bool = False) -> list[dict]:
        acct = self.engine.account_state()
        cands = scan(self.public, self.universe(), self.risk, acct, load_validation(REPORT_DIR), self.store)
        if execute:
            from .risk import TradePlan
            for c in cands:
                if c["approved"] and c["plan"]:
                    self.engine.submit(TradePlan(**c["plan"]), c["candidate_key"], c["evidence"]["expires_ms"])
            self.tick()
        return cands

    def connectivity(self) -> dict:
        out = {"public_api": {"ok": False}, "read_key": {"configured": self.settings.read_creds.present},
               "demo_key": {"configured": self.settings.demo_creds.present}, "store": {"backend": self.settings.store}}
        try:
            t0 = time.time()
            server = self.public.server_time_ms()
            out["public_api"] = {"ok": True, "latency_ms": round((time.time() - t0) * 1000),
                                 "clock_skew_ms": int(server - time.time() * 1000)}
        except Exception as exc:  # noqa: BLE001
            out["public_api"] = {"ok": False, "error": type(exc).__name__}
        try:
            self.store.select("heartbeats", limit=1)
            out["store"]["ok"] = True
        except Exception as exc:  # noqa: BLE001
            out["store"].update(ok=False, error=type(exc).__name__)
        return out
