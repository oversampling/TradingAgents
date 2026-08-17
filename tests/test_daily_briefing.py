from datetime import datetime

import pytest

from tradingagents.daily_briefing.config import DailySettings
from tradingagents.daily_briefing.models import MoomooSecurity, SymbolResult
from tradingagents.daily_briefing.reporting import render_html, write_artifacts
from tradingagents.daily_briefing.storage import DailyStore
from tradingagents.daily_briefing.symbol_mapper import merge_securities
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.setup import GraphSetup
from tradingagents.graph.trading_graph import TradingAgentsGraph


def test_merges_watchlist_and_positions_and_normalizes_symbols():
    symbols, skipped = merge_securities(
        [MoomooSecurity("US.INTC", "Intel"), MoomooSecurity("HK.00700")],
        [MoomooSecurity("US.INTC", "Intel Corporation"), MoomooSecurity("MY.1155", "Maybank")],
        10,
    )
    assert skipped == ["Unsupported Moomoo security code: 'HK.00700'"]
    assert [(x.ticker, x.is_watchlist, x.is_holding) for x in symbols] == [
        ("INTC", True, True), ("1155.KL", False, True)
    ]


def test_settings_rejects_invalid_maximum(monkeypatch, tmp_path):
    monkeypatch.setenv("DAILY_MAX_SYMBOLS", "0")
    monkeypatch.setenv("DAILY_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="at least 1"):
        DailySettings.load(tmp_path / "missing.env")


def test_store_tracks_completed_market_session(tmp_path):
    store = DailyStore(tmp_path / "daily.sqlite3")
    try:
        run_id = store.start_run("portfolio|US:2026-08-14|MY:2026-08-14", "now", "portfolio", "2026-08-14", "2026-08-14")
        store.finish(run_id, now="later", status="completed", results=[])
        assert store.market_session_processed("US", "2026-08-14", "portfolio")
        assert not store.market_session_processed("US", "2026-08-15", "portfolio")
    finally:
        store.close()


def test_reports_escape_untrusted_content_and_write_artifacts(tmp_path):
    symbol = merge_securities([MoomooSecurity("US.INTC", "<Intel>")], [], 1)[0][0]
    result = SymbolResult(symbol, "2026-08-14", "success", "<script>x</script>", "news")
    html = render_html("now", {"US": "2026-08-14", "MY": None}, [result], [])
    assert "&lt;script&gt;" in html
    html_path, json_path, _ = write_artifacts(tmp_path, datetime(2026, 8, 17), {}, [result], [])
    assert html_path.exists() and json_path.exists()


def test_propagate_reports_skips_trading_memory_and_signal_processing(monkeypatch):
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.execution_mode = "analysts_only"
    graph.ticker = None
    expected = {"sentiment_report": "sentiment", "news_report": "news"}
    monkeypatch.setattr(graph, "_invoke_graph", lambda *args, **kwargs: expected)
    assert graph.propagate_reports("INTC", "2026-08-14") == expected
    assert graph.ticker == "INTC"


def test_propagate_reports_requires_analyst_only_mode():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.execution_mode = "full"
    with pytest.raises(RuntimeError, match="analysts_only"):
        graph.propagate_reports("INTC", "2026-08-14")


def test_analyst_only_topology_ends_after_news_without_bull_researcher():
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model="gpt-4.1", api_key="not-used")
    owner = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph = GraphSetup(
        llm, llm, owner._create_tool_nodes(), ConditionalLogic(1, 1)
    ).setup_graph(("social", "news"), analysts_only=True).compile()
    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
    assert ("Msg Clear News", "__end__") in edges
    assert ("Msg Clear News", "Bull Researcher") not in edges
