"""SEC EDGAR quarterly-filing retrieval.

Fetches the latest two distinct 10-Q reporting periods available as of the
analysis date.  The filing date cutoff prevents future filings leaking into a
historical run, while the prior filing gives the fundamentals analyst a stable
comparison base.  Raw filing HTML is cached because EDGAR asks automated
clients to download only what they need.
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

import requests

from .config import get_config
from .errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError

DATA_BASE = "https://data.sec.gov"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
REQUEST_TIMEOUT = 30
MAX_CURRENT_CHARS = 80_000
MAX_PRIOR_CHARS = 40_000
MIN_REQUEST_INTERVAL = 0.12  # comfortably below SEC's 10 requests/second ceiling
_request_lock = threading.Lock()
_last_request_at = 0.0


class SecEdgarNotConfiguredError(VendorNotConfiguredError):
    """Raised when SEC's required identifying User-Agent is not configured."""


class SecEdgarRateLimitError(VendorRateLimitError):
    """Raised when EDGAR throttles an automated request."""


class SecEdgarCacheError(RuntimeError):
    """Raised when a downloaded SEC resource cannot be persisted locally."""


@dataclass(frozen=True)
class Filing:
    cik: int
    form: str
    filing_date: str
    report_date: str
    accession: str
    primary_document: str

    @property
    def url(self) -> str:
        accession_path = self.accession.replace("-", "")
        return (
            f"{ARCHIVES_BASE}/{self.cik}/{accession_path}/"
            f"{quote(self.primary_document, safe='._-')}"
        )


def get_user_agent() -> str:
    value = os.getenv("SEC_USER_AGENT", "").strip()
    if not value:
        raise SecEdgarNotConfiguredError(
            "SEC_USER_AGENT is not set. Add an identifying value such as "
            "'Your Name your-email@example.com' to .env."
        )
    return value


def _headers() -> dict[str, str]:
    return {
        "User-Agent": get_user_agent(),
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    }


def _get(url: str) -> requests.Response:
    global _last_request_at
    with _request_lock:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        response = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT)
        _last_request_at = time.monotonic()
    if response.status_code == 429:
        raise SecEdgarRateLimitError("SEC EDGAR request rate limit exceeded")
    response.raise_for_status()
    return response


def _cache_root() -> Path:
    root = Path(get_config()["data_cache_dir"]) / "sec_filings"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SecEdgarCacheError(f"Cannot create SEC filing cache directory {root}: {exc}") from exc
    return root


def _write_cache_text(path: Path, value: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except OSError as exc:
        raise SecEdgarCacheError(f"Cannot save SEC filing cache file {path}: {exc}") from exc


def _cached_json(url: str, cache_path: Path) -> dict:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    payload = _get(url).json()
    _write_cache_text(cache_path, json.dumps(payload))
    return payload


def resolve_cik(ticker: str) -> int:
    """Resolve an exchange ticker to its integer SEC CIK."""
    requested = ticker.strip().upper()
    aliases = {requested, requested.replace(".", "-"), requested.replace("-", ".")}
    payload = _cached_json(TICKER_MAP_URL, _cache_root() / "company_tickers.json")
    rows = payload.values() if isinstance(payload, dict) else payload
    for row in rows:
        if isinstance(row, dict) and str(row.get("ticker", "")).upper() in aliases:
            return int(row["cik_str"])
    raise NoMarketDataError(ticker, ticker, "ticker is not present in the SEC company map")


def _rows_from_recent(cik: int, recent: dict) -> list[Filing]:
    keys = (
        "form", "filingDate", "reportDate", "accessionNumber", "primaryDocument"
    )
    count = max((len(recent.get(key, [])) for key in keys), default=0)
    rows: list[Filing] = []
    for index in range(count):
        values = {key: (recent.get(key, []) + [""] * count)[index] for key in keys}
        if not values["accessionNumber"] or not values["primaryDocument"]:
            continue
        rows.append(
            Filing(
                cik=cik,
                form=values["form"],
                filing_date=values["filingDate"],
                report_date=values["reportDate"],
                accession=values["accessionNumber"],
                primary_document=values["primaryDocument"],
            )
        )
    return rows


def list_filings(cik: int, trade_date: str) -> list[Filing]:
    """Return eligible 10-Q filings, newest first, including archived history."""
    try:
        cutoff = date.fromisoformat(trade_date)
    except ValueError as exc:
        raise ValueError(f"trade_date must be YYYY-MM-DD, got {trade_date!r}") from exc

    padded = f"{cik:010d}"
    submissions = _get(f"{DATA_BASE}/submissions/CIK{padded}.json").json()
    rows = _rows_from_recent(cik, submissions.get("filings", {}).get("recent", {}))

    def eligible(source: list[Filing]) -> list[Filing]:
        return [
            row
            for row in source
            if row.form in {"10-Q", "10-Q/A"}
            and row.filing_date
            and date.fromisoformat(row.filing_date) <= cutoff
        ]

    # Recent submissions normally contain both quarters, so avoid touching the
    # archive. For old analysis dates, read archive files newest-first and stop
    # as soon as two distinct reporting periods are available.
    archive_files = sorted(
        submissions.get("filings", {}).get("files", []),
        key=lambda item: item.get("filingTo", ""),
        reverse=True,
    )
    for item in archive_files:
        eligible_rows = sorted(eligible(rows), key=lambda row: row.filing_date, reverse=True)
        if len(select_quarters(eligible_rows)) >= 2:
            break
        filing_from = item.get("filingFrom", "")
        if filing_from and filing_from > trade_date:
            continue
        name = item.get("name")
        if name:
            archived = _get(f"{DATA_BASE}/submissions/{quote(name, safe='._-')}").json()
            rows.extend(_rows_from_recent(cik, archived))

    return sorted(eligible(rows), key=lambda row: (row.filing_date, row.accession), reverse=True)


def select_quarters(filings: list[Filing], count: int = 2) -> list[Filing]:
    """Choose the newest filing for each distinct fiscal reporting period."""
    selected: list[Filing] = []
    seen_periods: set[str] = set()
    for filing in filings:
        period = filing.report_date or filing.filing_date
        if period in seen_periods:
            continue
        selected.append(filing)
        seen_periods.add(period)
        if len(selected) == count:
            break
    return selected


class _FilingTextParser(HTMLParser):
    BREAK_TAGS = {
        "br", "p", "div", "tr", "table", "section", "article", "h1", "h2", "h3",
        "h4", "h5", "h6", "li",
    }
    SKIP_TAGS = {"script", "style", "noscript", "ix:hidden"}
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attrs_dict = {str(key).lower(): str(value or "").lower() for key, value in attrs}
        hidden = (
            "hidden" in attrs_dict
            or "display:none" in attrs_dict.get("style", "").replace(" ", "")
        )
        if self.skip_stack:
            if tag not in self.VOID_TAGS:
                self.skip_stack.append(tag)
        elif tag in self.SKIP_TAGS or hidden:
            self.skip_stack.append(tag)
        elif tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_stack:
            if tag == self.skip_stack[-1]:
                self.skip_stack.pop()
        elif tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_stack:
            self.parts.append(data)


def extract_filing_text(document: str) -> str:
    parser = _FilingTextParser()
    parser.feed(document)
    text = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _load_document(filing: Filing) -> str:
    document_name = Path(filing.primary_document).name
    if document_name != filing.primary_document or document_name in {"", ".", ".."}:
        raise ValueError(f"Unsafe SEC primary-document name: {filing.primary_document!r}")
    path = _cache_root() / str(filing.cik) / filing.accession / document_name
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    body = _get(filing.url).text
    _write_cache_text(path, body)
    return body


def _save_clean_text(filing: Filing, text: str) -> Path:
    """Persist the parser input in a human-readable companion text file."""
    document_name = Path(filing.primary_document).name
    clean_path = (
        _cache_root()
        / str(filing.cik)
        / filing.accession
        / f"{Path(document_name).stem}.clean.txt"
    )
    _write_cache_text(clean_path, text)
    return clean_path


def _fit_filing_text(text: str, limit: int) -> tuple[str, bool]:
    """Fit a filing to context while retaining later decision-relevant sections."""
    if len(text) <= limit:
        return text, False

    prefix_size = limit // 2
    parts = [text[:prefix_size].rsplit("\n", 1)[0]]
    remaining = limit - len(parts[0])
    lower = text.lower()
    headings = (
        "management's discussion and analysis",
        "management’s discussion and analysis",
        "risk factors",
        "controls and procedures",
        "legal proceedings",
        "financial statements",
        "notes to consolidated financial statements",
    )
    used_ranges: list[tuple[int, int]] = [(0, prefix_size)]
    for heading in headings:
        # The first occurrence is often only the table of contents; prefer a
        # later occurrence where the substantive section begins.
        positions = [match.start() for match in re.finditer(re.escape(heading), lower)]
        position = positions[1] if len(positions) > 1 else (positions[0] if positions else -1)
        if position < 0 or any(start <= position < end for start, end in used_ranges):
            continue
        take = min(7_000, remaining)
        if take < 500:
            break
        end = min(len(text), position + take)
        parts.append(f"\n\n--- Extracted section: {heading.title()} ---\n{text[position:end]}")
        used_ranges.append((position, end))
        remaining -= take
    return "".join(parts)[:limit], True


def _render_filing(filing: Filing, label: str, limit: int) -> str:
    text = extract_filing_text(_load_document(filing))
    clean_path = _save_clean_text(filing, text)
    text, truncated = _fit_filing_text(text, limit)
    suffix = "\n\n[Document excerpt truncated to fit the analyst context.]" if truncated else ""
    return (
        f"## {label}: SEC {filing.form}\n"
        f"- Filing date: {filing.filing_date}\n"
        f"- Reporting period ended: {filing.report_date or 'not reported'}\n"
        f"- Accession: {filing.accession}\n"
        f"- Official filing: {filing.url}\n\n"
        f"- Local clean text: {clean_path}\n\n"
        f"{text}{suffix}"
    )


def get_quarterly_filing(ticker: str, trade_date: str) -> str:
    """Return the latest SEC 10-Q and prior distinct quarter as of trade_date."""
    cik = resolve_cik(ticker)
    selected = select_quarters(list_filings(cik, trade_date))
    if not selected:
        raise NoMarketDataError(ticker, ticker, f"no SEC 10-Q filed on or before {trade_date}")

    sections = [_render_filing(selected[0], "Current quarter", MAX_CURRENT_CHARS)]
    if len(selected) > 1:
        sections.append(_render_filing(selected[1], "Prior quarter comparison", MAX_PRIOR_CHARS))
    else:
        sections.append("## Prior quarter comparison\nNo earlier 10-Q was available.")
    return "\n\n".join(sections)
