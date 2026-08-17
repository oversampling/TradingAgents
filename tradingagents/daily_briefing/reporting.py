"""Safe, compact HTML/JSON artifacts for a daily briefing."""

import html
import json
import os
from datetime import datetime
from pathlib import Path

from .models import SymbolResult


def render_html(generated_at: str, sessions: dict[str, str | None], results: list[SymbolResult], skipped: list[str]) -> str:
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
<p><i>Research only — not financial advice. No trades were submitted.</i></p>
{''.join(cards)}
<h3>Skipped instruments</h3><ul>{skipped_html}</ul>
</body></html>"""


def write_artifacts(root: Path, generated_at: datetime, sessions: dict[str, str | None], results: list[SymbolResult], skipped: list[str]) -> tuple[Path, Path, str]:
    folder = root / "reports" / generated_at.date().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    html_path, json_path = folder / "daily-news.html", folder / "daily-news.json"
    rendered = render_html(generated_at.isoformat(), sessions, results, skipped)
    payload = {"generated_at": generated_at.isoformat(), "sessions": sessions, "results": [item.to_dict() for item in results], "skipped": skipped}
    for path, content in ((html_path, rendered), (json_path, json.dumps(payload, indent=2, ensure_ascii=False))):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    return html_path, json_path, rendered
