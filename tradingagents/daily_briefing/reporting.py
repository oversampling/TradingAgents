"""Safe, compact HTML/JSON artifacts for a daily briefing."""

import html
import json
import os
from datetime import datetime
from pathlib import Path

from .models import BrokerPnlPosition, BrokerPnlSummary, SymbolResult


def summarize_broker_pnl(positions: list[BrokerPnlPosition], retrieved_at: str) -> BrokerPnlSummary:
    totals: dict[str, dict[str, float]] = {}
    unavailable = 0
    for position in positions:
        bucket = totals.setdefault(
            position.currency, {"unrealized_pnl": 0.0, "realized_pnl": 0.0, "market_value": 0.0}
        )
        for field in ("unrealized_pnl", "realized_pnl", "market_value"):
            value = getattr(position, field)
            if value is None:
                unavailable += 1
            else:
                bucket[field] += value
    return BrokerPnlSummary(retrieved_at, len(positions), totals, unavailable)


def _pnl_header(summary: BrokerPnlSummary | None) -> str:
    if summary is None:
        return "<p><b>Broker P&amp;L snapshot:</b> unavailable for this run.</p>"
    rows = "".join(
        f"<li>{html.escape(currency)}: unrealized <b>{values['unrealized_pnl']:,.2f}</b> · "
        f"Moomoo-reported realized <b>{values['realized_pnl']:,.2f}</b> · "
        f"market value {values['market_value']:,.2f}</li>"
        for currency, values in sorted(summary.totals_by_currency.items())
    ) or "<li>No open positions.</li>"
    warning = "" if not summary.unavailable_fields else f" ({summary.unavailable_fields} source fields unavailable)"
    return (
        "<section style='padding:12px;border:1px solid #cbd5e1;background:#f8fafc'>"
        "<h2>Broker P&amp;L snapshot</h2>"
        f"<p>Retrieved {html.escape(summary.retrieved_at)} · {summary.position_count} open positions{warning}</p>"
        f"<ul>{rows}</ul>"
        "<p><small>Values come directly from Moomoo's current position response and are separated by currency. "
        "They are not a tax calculation or a historical FIFO realized-P&amp;L ledger.</small></p></section>"
    )


def render_html(generated_at: str, sessions: dict[str, str | None], results: list[SymbolResult], skipped: list[str], pnl_summary: BrokerPnlSummary | None = None) -> str:
    successful = sum(item.status == "success" for item in results)
    failed = sum(item.status == "failed" for item in results)
    cards = []
    for item in results:
        symbol = item.symbol
        sources = "Watchlist" + (" + Holding" if symbol.is_holding else "")
        body = (
            f"<h3>{html.escape(symbol.ticker)} — {html.escape(symbol.name or 'Unknown company')}</h3>"
            f"<p><b>{html.escape(symbol.market)}</b> session {html.escape(item.session_date)} · {html.escape(sources)} · {html.escape(item.status)}</p>"
        )
        if item.status == "success":
            body += f"<h4>Sentiment</h4><pre>{html.escape(item.sentiment_report)}</pre><h4>News</h4><pre>{html.escape(item.news_report)}</pre>"
        else:
            body += f"<p><b>Failure:</b> {html.escape(item.error_stage)} — {html.escape(item.error_message)}</p>"
        cards.append(f"<section>{body}</section>")
    skipped_html = "".join(f"<li>{html.escape(value)}</li>" for value in skipped)
    return f"""<!doctype html><html><body style='font-family:Arial,sans-serif;max-width:900px;margin:auto'>
<h1>TradingAgents Daily News &amp; Sentiment</h1>
<p>Generated: {html.escape(generated_at)}<br>Sessions: US={html.escape(str(sessions.get('US')))}, MY={html.escape(str(sessions.get('MY')))}<br>Success: {successful}; failed: {failed}; skipped: {len(skipped)}</p>
{_pnl_header(pnl_summary)}
<p><i>Research only — not financial advice. No trades were submitted.</i></p>
{''.join(cards)}
<h3>Skipped instruments</h3><ul>{skipped_html}</ul>
</body></html>"""


def write_artifacts(root: Path, generated_at: datetime, sessions: dict[str, str | None], results: list[SymbolResult], skipped: list[str], pnl_summary: BrokerPnlSummary | None = None) -> tuple[Path, Path, str]:
    folder = root / "reports" / generated_at.date().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    html_path, json_path = folder / "daily-news.html", folder / "daily-news.json"
    rendered = render_html(generated_at.isoformat(), sessions, results, skipped, pnl_summary)
    payload = {"generated_at": generated_at.isoformat(), "sessions": sessions, "pnl_summary": pnl_summary.to_dict() if pnl_summary else None, "results": [item.to_dict() for item in results], "skipped": skipped}
    for path, content in ((html_path, rendered), (json_path, json.dumps(payload, indent=2, ensure_ascii=False))):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    return html_path, json_path, rendered
