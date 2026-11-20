"""Quantitative validation framework for A+++ qualification.

NOTE: the owner's "previously specified framework" document was not available in the
repository, so this module implements an explicit, conservative framework whose
thresholds live in one place (GATES) and can be replaced with the owner's values.

Evidence levels
  REJECTED                  - fails any gate below
  OOS_PASSED_FORWARD_PENDING - passes all historical gates; may be PAPER traded only
  VALIDATED                 - historical gates + forward paper gates  -> A+++ eligible

Historical gates are evaluated on a strategy x timeframe x asset-group POOL (more
statistical power than a single symbol), then each symbol must not contradict the pool.

Method
  * Chronological 60/40 split per symbol: first 60% of bars = in-sample (IS),
    last 40% = out-of-sample (OOS). Strategy parameters are fixed a priori, so
    IS is only used as a sign-consistency check.
  * OOS expectancy confidence: block bootstrap by ISO calendar week across the pool
    (trades in the same week move together across symbols; resampling whole weeks
    respects that dependence).
  * Multiple testing: Holm-Bonferroni across every pool tested, on the bootstrap
    p-value of H0 "mean net R <= 0".
  * Cost stress: fees x1.25 (all taker) and slippage x2 must still be positive.
  * Stability: share of OOS calendar quarters with positive total R.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .backtest import stats

IS_FRACTION = 0.60

GATES = {
    "pool_min_oos_trades": 100,
    "pool_min_mean_r": 0.10,          # net of all costs
    "pool_min_profit_factor": 1.25,
    "pool_ci_lower_gt": 0.0,          # 95% bootstrap lower bound of mean net R
    "pool_min_is_mean_r": 0.0,        # no sign flip between IS and OOS
    "pool_min_stress_mean_r": 0.0,
    "pool_min_positive_quarters": 0.60,
    "pool_max_dd_r": 25.0,
    "holm_alpha": 0.05,
    "symbol_min_oos_trades": 15,
    "symbol_min_mean_r": 0.0,
    "symbol_min_stress_mean_r": 0.0,
    "forward_min_trades": 20,         # forward paper trades required for VALIDATED
    "forward_min_mean_r": 0.0,
}

REJECTED = "REJECTED"
OOS_PASSED = "OOS_PASSED_FORWARD_PENDING"
VALIDATED = "VALIDATED"


@dataclass
class PoolResult:
    strategy: str
    interval: str
    group: str
    symbols: int
    is_trades: int
    is_mean_r: float
    oos_trades: int
    oos_mean_r: float
    oos_win_rate: float
    oos_profit_factor: float
    oos_total_r: float
    oos_max_dd_r: float
    ci_low: float
    ci_high: float
    p_value: float
    p_holm: float
    stress_mean_r: float
    positive_quarters: float
    failed_gates: list[str]
    status: str

    def to_dict(self) -> dict:
        return asdict(self)


def split_is_oos(trades: pd.DataFrame) -> pd.DataFrame:
    """Label each trade IS/OOS by its symbol's own chronological 60/40 bar split."""
    t = trades.copy()
    cut = t["first_bar_ts"] + IS_FRACTION * (t["last_bar_ts"] - t["first_bar_ts"])
    # a trade belongs to OOS only if its SIGNAL was generated after the cut
    t["sample"] = np.where(t["signal_ts"] >= cut, "OOS", "IS")
    return t


def week_block_bootstrap(trades: pd.DataFrame, n_boot: int = 5000, seed: int = 7) -> tuple[float, float, float]:
    """95% CI of mean net R and one-sided p-value for H0: mean <= 0, resampling whole weeks."""
    if trades.empty:
        return 0.0, 0.0, 1.0
    week = (trades["entry_ts"] // (7 * 86_400_000)).to_numpy()
    r = trades["net_r"].to_numpy()
    uniq, inv = np.unique(week, return_inverse=True)
    sums = np.bincount(inv, weights=r)
    counts = np.bincount(inv)
    k = len(uniq)
    if k < 8:
        return float("-inf"), float("inf"), 1.0
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, size=(n_boot, k))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    p = float((np.sum(means <= 0) + 1) / (n_boot + 1))
    return float(lo), float(hi), p


def positive_quarter_share(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    q = pd.to_datetime(trades["entry_ts"], unit="ms").dt.to_period("Q")
    tot = trades.groupby(q)["net_r"].sum()
    return float((tot > 0).mean())


def holm(pvals: list[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values (monotone)."""
    m = len(pvals)
    order = np.argsort(pvals)
    adj = np.empty(m)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, (m - rank) * pvals[i])
        adj[i] = min(1.0, running)
    return adj.tolist()


def validate_pools(trades: pd.DataFrame, stressed_trades: pd.DataFrame) -> list[PoolResult]:
    if trades.empty:
        return []
    t = split_is_oos(trades)
    s = split_is_oos(stressed_trades) if len(stressed_trades) else stressed_trades
    results: list[PoolResult] = []
    for (strategy, interval, group), g in t.groupby(["strategy", "interval", "group"]):
        g = g.sort_values("entry_ts")
        is_, oos = g[g["sample"] == "IS"], g[g["sample"] == "OOS"]
        st_is, st_oos = stats(is_["net_r"].to_numpy()), stats(oos["net_r"].to_numpy())
        lo, hi, p = week_block_bootstrap(oos)
        if len(s):
            so = s[(s["strategy"] == strategy) & (s["interval"] == interval) & (s["group"] == group)
                   & (s["sample"] == "OOS")]
            stress_mean = float(so["net_r"].mean()) if len(so) else 0.0
        else:
            stress_mean = 0.0
        results.append(PoolResult(strategy, interval, group, int(g["symbol"].nunique()),
                                  st_is["trades"], st_is["mean_r"], st_oos["trades"], st_oos["mean_r"],
                                  st_oos["win_rate"], st_oos["profit_factor"], st_oos["total_r"],
                                  st_oos["max_dd_r"], lo, hi, p, 1.0, stress_mean,
                                  positive_quarter_share(oos), [], REJECTED))
    for res, ph in zip(results, holm([r.p_value for r in results])):
        res.p_holm = ph
        f = []
        if res.oos_trades < GATES["pool_min_oos_trades"]: f.append("oos_trades")
        if res.oos_mean_r < GATES["pool_min_mean_r"]: f.append("oos_mean_r")
        if res.oos_profit_factor < GATES["pool_min_profit_factor"]: f.append("profit_factor")
        if not res.ci_low > GATES["pool_ci_lower_gt"]: f.append("ci_lower")
        if res.is_mean_r <= GATES["pool_min_is_mean_r"]: f.append("is_sign")
        if res.stress_mean_r <= GATES["pool_min_stress_mean_r"]: f.append("cost_stress")
        if res.positive_quarters < GATES["pool_min_positive_quarters"]: f.append("quarter_stability")
        if res.oos_max_dd_r > GATES["pool_max_dd_r"]: f.append("max_drawdown")
        if res.p_holm > GATES["holm_alpha"]: f.append("multiple_testing")
        res.failed_gates = f
        res.status = OOS_PASSED if not f else REJECTED
    return results


def validate_symbols(trades: pd.DataFrame, stressed_trades: pd.DataFrame, pools: list[PoolResult]) -> pd.DataFrame:
    """Per (symbol, strategy, interval) status. A symbol can only pass inside a passing pool."""
    cols = ["symbol", "strategy", "interval", "group", "oos_trades", "oos_mean_r", "oos_win_rate",
            "oos_profit_factor", "stress_mean_r", "pool_status", "status", "reason"]
    if trades.empty:
        return pd.DataFrame(columns=cols)
    t = split_is_oos(trades)
    s = split_is_oos(stressed_trades) if len(stressed_trades) else stressed_trades
    pool_status = {(p.strategy, p.interval, p.group): p.status for p in pools}
    rows = []
    for (sym, strategy, interval, group), g in t.groupby(["symbol", "strategy", "interval", "group"]):
        oos = g[g["sample"] == "OOS"].sort_values("entry_ts")
        st = stats(oos["net_r"].to_numpy())
        so = s[(s["symbol"] == sym) & (s["strategy"] == strategy) & (s["interval"] == interval)
               & (s["sample"] == "OOS")] if len(s) else s
        stress = float(so["net_r"].mean()) if len(so) else 0.0
        ps = pool_status.get((strategy, interval, group), REJECTED)
        reasons = []
        if ps == REJECTED: reasons.append("pool_rejected")
        if st["trades"] < GATES["symbol_min_oos_trades"]: reasons.append("symbol_oos_trades")
        if st["mean_r"] <= GATES["symbol_min_mean_r"]: reasons.append("symbol_mean_r")
        if stress <= GATES["symbol_min_stress_mean_r"]: reasons.append("symbol_cost_stress")
        rows.append([sym, strategy, interval, group, st["trades"], st["mean_r"], st["win_rate"],
                     st["profit_factor"], stress, ps, OOS_PASSED if not reasons else REJECTED, ",".join(reasons)])
    return pd.DataFrame(rows, columns=cols)


def apply_forward_gate(status: str, forward_net_r: list[float]) -> str:
    """Promote OOS_PASSED to VALIDATED only with enough positive forward paper evidence."""
    if status != OOS_PASSED:
        return status
    if len(forward_net_r) >= GATES["forward_min_trades"] and float(np.mean(forward_net_r)) > GATES["forward_min_mean_r"]:
        return VALIDATED
    return OOS_PASSED
