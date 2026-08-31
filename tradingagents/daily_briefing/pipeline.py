"""Orchestration for the read-only daily briefing."""

import copy
import os
from datetime import datetime
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

from .config import DailySettings
from .mailer import MailDeliveryError, send_html_report
from .market_calendar import latest_completed_session
from .models import SymbolResult
from .providers.moomoo import MoomooReadOnlyProvider
from .reporting import render_html, summarize_broker_pnl, write_artifacts
from .storage import DailyStore
from .symbol_mapper import merge_securities


class AlreadyRunning(RuntimeError):
    pass


class RunLock:
    def __init__(self, path: Path):
        self.path, self.acquired = path, False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise AlreadyRunning(f"Another daily briefing owns {self.path}") from exc
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        self.acquired = True
        return self

    def __exit__(self, *_):
        if self.acquired:
            self.path.unlink(missing_ok=True)


class DailyBriefingPipeline:
    def __init__(self, settings: DailySettings, provider=None, graph_factory=TradingAgentsGraph, clock=None):
        self.settings = settings
        self.provider = provider or MoomooReadOnlyProvider(
            settings.moomoo_host, settings.moomoo_port, settings.moomoo_account_id
        )
        self.graph_factory, self.clock = graph_factory, clock

    def _now(self) -> datetime:
        return self.clock() if self.clock else datetime.now(ZoneInfo(self.settings.timezone))

    def _graph(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config.update({
            "checkpoint_enabled": False,
            "data_cache_dir": str(self.settings.runtime_dir / "cache"),
            "results_dir": str(self.settings.runtime_dir / "agent-results"),
        })
        return self.graph_factory(
            selected_analysts=("social", "news"), execution_mode="analysts_only", config=config
        )

    def run(self, *, send_email: bool = True, force: bool = False) -> dict:
        # Fail before OpenD/LLM work if a scheduled run is configured to email
        # but its delivery credentials are absent.
        if send_email:
            self.settings.validate_email()
        now = self._now()
        sessions = {
            "US": latest_completed_session(self.settings.us_calendar, now),
            "MY": latest_completed_session(self.settings.my_calendar, now),
        }
        session_key = f"{self.settings.watchlist_group}|US:{sessions['US']}|MY:{sessions['MY']}"
        store = DailyStore(self.settings.runtime_dir / "daily.sqlite3")
        try:
            if not force and store.completed_run(session_key):
                return {"status": "already_completed", "session_key": session_key}
            fresh_markets = {
                market for market, date in sessions.items()
                if date and (force or not store.market_session_processed(market, date, self.settings.watchlist_group))
            }
            if not fresh_markets:
                return {"status": "no_new_completed_session", "session_key": session_key}
            with RunLock(self.settings.runtime_dir / "daily.lock"):
                run_id = store.start_run(
                    session_key, now.isoformat(), self.settings.watchlist_group, sessions["US"], sessions["MY"]
                )
                return self._execute(store, run_id, now, sessions, fresh_markets, send_email)
        finally:
            store.close()

    def _execute(self, store, run_id, now, sessions, fresh_markets, send_email):
        results: list[SymbolResult] = []
        skipped: list[str] = []
        pnl_summary = None
        try:
            watchlist = self.provider.watchlist(self.settings.watchlist_group)
            try:
                pnl_positions = self.provider.position_pnl()
                pnl_summary = summarize_broker_pnl(pnl_positions, now.isoformat())
                positions = [item.security for item in pnl_positions] if self.settings.include_positions else []
            except Exception as exc:
                # P&L is useful context but must not suppress the news briefing.
                positions = []
                skipped.append(f"Broker P&L snapshot unavailable: {type(exc).__name__}: {exc}")
            symbols, mapping_skipped = merge_securities(
                watchlist, positions, self.settings.max_symbols
            )
            skipped.extend(mapping_skipped)
            symbols = [item for item in symbols if item.market in fresh_markets]
            graph = self._graph()
            for symbol in symbols:
                started = monotonic()
                try:
                    state = graph.propagate_reports(symbol.ticker, sessions[symbol.market], asset_type="stock")
                    result = SymbolResult(
                        symbol, sessions[symbol.market], "success",
                        str(state.get("sentiment_report", "")), str(state.get("news_report", "")), monotonic() - started,
                    )
                except Exception as exc:  # a single ticker must not suppress the other symbols
                    result = SymbolResult(
                        symbol, sessions[symbol.market], "failed", duration_seconds=monotonic() - started,
                        error_stage="analysis", error_message=f"{type(exc).__name__}: {exc}",
                    )
                results.append(result)
                store.add_result(run_id, result)
            if not results:
                skipped.append("No supported securities were available for the newly completed sessions.")
            html_path, json_path, html = write_artifacts(
                self.settings.runtime_dir, now, sessions, results, skipped, pnl_summary
            )
            successes = sum(item.status == "success" for item in results)
            status = "completed" if successes == len(results) else "partial"
            if not successes and results and not self.settings.send_partial_reports:
                status = "failed"
            store.finish(run_id, now=now.isoformat(), status=status, results=results, html_path=html_path, json_path=json_path)
            if send_email and (successes or self.settings.send_partial_reports):
                try:
                    send_html_report(self.settings.gmail_address, self.settings.recipient, self.settings.gmail_app_password, f"TradingAgents Daily News & Sentiment — {now.date().isoformat()}", html)
                    store.mark_email_sent(run_id, now.isoformat())
                except MailDeliveryError as exc:
                    store.finish(run_id, now=now.isoformat(), status="report_ready_email_failed", results=results, html_path=html_path, json_path=json_path, error=f"{type(exc).__name__}: {exc}")
                    return {"status": "report_ready_email_failed", "run_id": run_id, "html_path": str(html_path), "json_path": str(json_path), "successes": successes, "failures": len(results) - successes}
            return {"status": status, "run_id": run_id, "html_path": str(html_path), "json_path": str(json_path), "successes": successes, "failures": len(results) - successes}
        except Exception as exc:
            store.finish(run_id, now=now.isoformat(), status="failed", results=results, error=f"{type(exc).__name__}: {exc}")
            if send_email and self.settings.alert_on_full_failure:
                try:
                    self.settings.validate_email()
                    body = render_html(
                        now.isoformat(), sessions, results,
                        skipped + [f"Operational failure: {type(exc).__name__}: {exc}"], pnl_summary,
                    )
                    send_html_report(self.settings.gmail_address, self.settings.recipient, self.settings.gmail_app_password, f"TradingAgents Daily Briefing FAILED — {now.date().isoformat()}", body)
                except Exception:
                    pass
            raise

    def send_only(self, run_id: int) -> dict:
        store = DailyStore(self.settings.runtime_dir / "daily.sqlite3")
        try:
            row = store.run(run_id)
            if row is None:
                raise ValueError(f"No saved report for run {run_id}")
            report_path = Path(row["report_html_path"]) if row["report_html_path"] else (
                self.settings.runtime_dir / "reports" / row["started_at"][:10] / "daily-news.html"
            )
            if not report_path.is_file():
                raise ValueError(f"No saved report for run {run_id}")
            self.settings.validate_email()
            html = report_path.read_text(encoding="utf-8")
            send_html_report(self.settings.gmail_address, self.settings.recipient, self.settings.gmail_app_password, "TradingAgents Daily News & Sentiment — resend", html)
            store.mark_email_sent(run_id, self._now().isoformat())
            return {"status": "sent", "run_id": run_id}
        finally:
            store.close()
