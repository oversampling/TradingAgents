"""Convert broker security codes into TradingAgents/Yahoo symbols."""

from .models import BriefingSymbol, MoomooSecurity


class UnsupportedSecurity(ValueError):
    """A broker security is outside Phase 1's US/Malaysia scope."""


def normalize_security(security: MoomooSecurity) -> BriefingSymbol:
    code = security.code.strip().upper()
    if code.startswith("US.") and len(code) > 3:
        return BriefingSymbol(code, code[3:], "US", security.name)
    if code.startswith("MY.") and len(code) > 3:
        return BriefingSymbol(code, f"{code[3:]}.KL", "MY", security.name)
    raise UnsupportedSecurity(f"Unsupported Moomoo security code: {security.code!r}")


def merge_securities(
    watchlist: list[MoomooSecurity], positions: list[MoomooSecurity], max_symbols: int
) -> tuple[list[BriefingSymbol], list[str]]:
    """Normalize, deduplicate and bound a watchlist/holding union."""
    merged: dict[str, BriefingSymbol] = {}
    skipped: list[str] = []
    for source, is_watchlist, is_holding in (
        (watchlist, True, False),
        (positions, False, True),
    ):
        for security in source:
            try:
                item = normalize_security(security)
            except UnsupportedSecurity as exc:
                skipped.append(str(exc))
                continue
            key = item.ticker.upper()
            previous = merged.get(key)
            merged[key] = BriefingSymbol(
                item.moomoo_code,
                item.ticker,
                item.market,
                item.name or (previous.name if previous else ""),
                is_watchlist or bool(previous and previous.is_watchlist),
                is_holding or bool(previous and previous.is_holding),
            )
    return list(merged.values())[:max_symbols], skipped
