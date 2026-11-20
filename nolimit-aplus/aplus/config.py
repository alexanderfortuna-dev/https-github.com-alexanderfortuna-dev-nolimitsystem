"""Configuration loaded from environment (.env). Secrets never leave this module
except into the signing function of the Bitget client."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class ApiCredentials:
    key: str
    secret: str
    passphrase: str

    @property
    def present(self) -> bool:
        return bool(self.key and self.secret and self.passphrase)

    def __repr__(self) -> str:  # never print secrets
        tail = self.key[-4:] if self.key else ""
        return f"ApiCredentials(key=***{tail}, secret=***, passphrase=***)"

    __str__ = __repr__


# Risk defaults are the owner-approved initial controls. Env may TIGHTEN them
# but never loosen them beyond these ceilings.
RISK_CEILINGS = {
    "risk_per_trade": 0.005,
    "max_open_risk": 0.015,
    "daily_loss_limit": 0.015,
    "weekly_loss_limit": 0.04,
    "max_gross_exposure": 3.0,
}


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade: float = 0.005       # planned equity risk per trade
    max_open_risk: float = 0.015        # total open planned risk
    daily_loss_limit: float = 0.015     # circuit breaker
    weekly_loss_limit: float = 0.04     # circuit breaker
    max_gross_exposure: float = 3.0     # sum(|notional|) / equity, incl. existing user positions
    liq_buffer_mult: float = 2.0        # liquidation distance must be >= this x stop distance
    max_data_age_bars: float = 1.05     # last closed bar must be the most recent one (a missing bar = stale feed)
    max_ticker_age_s: float = 15.0      # reject order placement on stale tickers
    max_account_age_s: float = 120.0    # reject new entries on stale account snapshots

    @classmethod
    def from_env(cls) -> "RiskLimits":
        def tight(name: str, env: str) -> float:
            ceiling = RISK_CEILINGS[name]
            raw = os.environ.get(env)
            if not raw:
                return ceiling
            val = float(raw)
            if val <= 0:
                raise ValueError(f"{env} must be > 0")
            return min(val, ceiling)  # can only be tightened

        return cls(
            risk_per_trade=tight("risk_per_trade", "APLUS_RISK_PER_TRADE"),
            max_open_risk=tight("max_open_risk", "APLUS_MAX_OPEN_RISK"),
            daily_loss_limit=tight("daily_loss_limit", "APLUS_DAILY_LOSS_LIMIT"),
            weekly_loss_limit=tight("weekly_loss_limit", "APLUS_WEEKLY_LOSS_LIMIT"),
            max_gross_exposure=tight("max_gross_exposure", "APLUS_MAX_GROSS_EXPOSURE"),
        )


@dataclass(frozen=True)
class Settings:
    read_creds: ApiCredentials
    demo_creds: ApiCredentials
    execution_mode: str            # "paper" | "demo"  (there is no "live")
    store: str                     # "sqlite" | "supabase"
    sqlite_path: Path
    supabase_url: str
    supabase_key: str = field(repr=False)
    api_host: str = "127.0.0.1"
    api_port: int = 8787
    api_token: str = field(default="", repr=False)
    risk: RiskLimits = field(default_factory=RiskLimits)
    data_dir: Path = ROOT / "data"
    var_dir: Path = ROOT / "var"

    @classmethod
    def from_env(cls, dotenv: bool = True) -> "Settings":
        if dotenv:
            load_dotenv()
        env = os.environ.get
        mode = (env("APLUS_EXECUTION_MODE") or "paper").lower()
        if mode not in ("paper", "demo"):
            raise ValueError(
                f"APLUS_EXECUTION_MODE={mode!r} is not supported. Allowed: paper, demo. "
                "Production trading is disabled by design."
            )
        sqlite_path = Path(env("APLUS_SQLITE_PATH") or "var/aplus.db")
        if not sqlite_path.is_absolute():
            sqlite_path = ROOT / sqlite_path
        return cls(
            read_creds=ApiCredentials(env("BITGET_READ_API_KEY") or "", env("BITGET_READ_API_SECRET") or "",
                                      env("BITGET_READ_API_PASSPHRASE") or ""),
            demo_creds=ApiCredentials(env("BITGET_DEMO_API_KEY") or "", env("BITGET_DEMO_API_SECRET") or "",
                                      env("BITGET_DEMO_API_PASSPHRASE") or ""),
            execution_mode=mode,
            store=(env("APLUS_STORE") or "sqlite").lower(),
            sqlite_path=sqlite_path,
            supabase_url=(env("SUPABASE_URL") or "").rstrip("/"),
            supabase_key=env("SUPABASE_SERVICE_ROLE_KEY") or "",
            api_host=env("APLUS_API_HOST") or "127.0.0.1",
            api_port=int(env("APLUS_API_PORT") or 8787),
            api_token=env("APLUS_API_TOKEN") or "",
            risk=RiskLimits.from_env(),
        )
