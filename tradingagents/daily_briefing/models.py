"""Small, dependency-free models used by the daily briefing."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MoomooSecurity:
    code: str
    name: str = ""


@dataclass(frozen=True)
class BrokerPnlPosition:
    """Read-only current-position P&L as returned by Moomoo OpenD."""

    security: MoomooSecurity
    market: str
    quantity: float
    currency: str
    market_value: float | None
    unrealized_pnl: float | None
    realized_pnl: float | None
    pnl_ratio: float | None


@dataclass(frozen=True)
class BrokerPnlSummary:
    """Currency-separated snapshot suitable for a deterministic email header."""

    retrieved_at: str
    position_count: int
    totals_by_currency: dict[str, dict[str, float]]
    unavailable_fields: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BriefingSymbol:
    moomoo_code: str
    ticker: str
    market: str
    name: str = ""
    is_watchlist: bool = False
    is_holding: bool = False


@dataclass
class SymbolResult:
    symbol: BriefingSymbol
    session_date: str
    status: str
    sentiment_report: str = ""
    news_report: str = ""
    duration_seconds: float = 0.0
    error_stage: str = ""
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["symbol"] = asdict(self.symbol)
        return data
