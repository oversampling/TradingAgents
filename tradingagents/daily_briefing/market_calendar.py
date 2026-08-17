"""Exchange-calendar based session selection."""

from datetime import datetime, timedelta


class MarketCalendarError(RuntimeError):
    pass


def latest_completed_session(calendar_name: str, now: datetime) -> str | None:
    """Return the most recent session whose official close is before ``now``."""
    try:
        import exchange_calendars as xcals
        import pandas as pd
    except ImportError as exc:
        raise MarketCalendarError(
            "Install daily dependencies: pip install 'tradingagents[daily]'"
        ) from exc
    try:
        calendar = xcals.get_calendar(calendar_name)
        moment = pd.Timestamp(now)
        if moment.tzinfo is None:
            moment = moment.tz_localize("UTC")
        moment = moment.tz_convert("UTC")
        sessions = calendar.sessions_in_range(
            (moment - timedelta(days=10)).date(), moment.date()
        )
        completed = [session for session in sessions if calendar.session_close(session) <= moment]
        return completed[-1].date().isoformat() if completed else None
    except Exception as exc:
        raise MarketCalendarError(f"Unable to resolve calendar {calendar_name!r}: {exc}") from exc
