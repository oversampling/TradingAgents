"""Validated environment-backed settings for the unattended job."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class DailySettings:
    runtime_dir: Path
    watchlist_group: str
    include_positions: bool
    max_symbols: int
    moomoo_host: str
    moomoo_port: int
    moomoo_account_id: int | None
    timezone: str
    us_calendar: str
    my_calendar: str
    gmail_address: str
    gmail_app_password: str
    recipient: str
    send_partial_reports: bool
    alert_on_full_failure: bool

    @classmethod
    def load(cls, env_path: str | Path = ".env") -> "DailySettings":
        # This is an explicit CLI/deployment argument, not an incidental
        # discovery. It must win over blank values inherited by launchd or a
        # previously loaded parent .env file.
        load_dotenv(env_path, override=True)
        runtime_dir = Path(os.getenv("DAILY_RUNTIME_DIR", "~/.tradingagents/daily")).expanduser()
        group = os.getenv("MOOMOO_WATCHLIST_GROUP", "portfolio").strip()
        max_symbols = int(os.getenv("DAILY_MAX_SYMBOLS", "10"))
        if not group:
            raise ValueError("MOOMOO_WATCHLIST_GROUP must not be empty")
        if max_symbols < 1:
            raise ValueError("DAILY_MAX_SYMBOLS must be at least 1")
        account = os.getenv("MOOMOO_ACCOUNT_ID", "").strip()
        return cls(
            runtime_dir=runtime_dir,
            watchlist_group=group,
            include_positions=_bool("INCLUDE_PORTFOLIO_POSITIONS", True),
            max_symbols=max_symbols,
            moomoo_host=os.getenv("MOOMOO_HOST", "127.0.0.1"),
            moomoo_port=int(os.getenv("MOOMOO_PORT", "11111")),
            moomoo_account_id=int(account) if account else None,
            timezone=os.getenv("REPORT_TIMEZONE", "Asia/Kuala_Lumpur"),
            us_calendar=os.getenv("US_CALENDAR", "XNYS"),
            my_calendar=os.getenv("MY_CALENDAR", "XKLS"),
            gmail_address=os.getenv("GMAIL_ADDRESS", "").strip(),
            gmail_app_password=os.getenv("GMAIL_APP_PASSWORD", "").strip(),
            recipient=os.getenv("REPORT_RECIPIENT", "").strip(),
            send_partial_reports=_bool("SEND_PARTIAL_REPORTS", True),
            alert_on_full_failure=_bool("ALERT_ON_FULL_FAILURE", True),
        )

    def validate_email(self) -> None:
        if not (self.gmail_address and self.gmail_app_password and self.recipient):
            raise ValueError("GMAIL_ADDRESS, GMAIL_APP_PASSWORD and REPORT_RECIPIENT are required")
