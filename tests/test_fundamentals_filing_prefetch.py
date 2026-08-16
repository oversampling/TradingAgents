"""Deterministic SEC filing context for the Fundamentals Analyst."""

from unittest import mock

import pytest
from langchain_core.messages import HumanMessage

from tradingagents.agents.analysts import fundamentals_analyst


def state(messages=None, asset_type="stock"):
    return {
        "messages": messages or [HumanMessage(content="Analyze NOW")],
        "asset_type": asset_type,
        "company_of_interest": "NOW",
        "trade_date": "2026-08-10",
    }


@pytest.mark.unit
def test_stock_filing_is_prefetched_and_marked():
    filing = "## Current quarter: SEC 10-Q\n- Accession: 0001\nFiling evidence"
    with mock.patch.object(
        fundamentals_analyst, "fetch_quarterly_filing", return_value=filing
    ) as fetch:
        message = fundamentals_analyst._prefetch_filing_context(state())

    fetch.assert_called_once_with("NOW", "2026-08-10")
    assert message.content.startswith(fundamentals_analyst._FILING_CONTEXT_MARKER)
    assert "Filing evidence" in message.content


@pytest.mark.unit
def test_existing_context_prevents_duplicate_fetch():
    existing = HumanMessage(
        content=f"{fundamentals_analyst._FILING_CONTEXT_MARKER}\nalready fetched"
    )
    with mock.patch.object(fundamentals_analyst, "fetch_quarterly_filing") as fetch:
        assert fundamentals_analyst._prefetch_filing_context(state([existing])) is None
    fetch.assert_not_called()


@pytest.mark.unit
def test_crypto_skips_sec_prefetch():
    with mock.patch.object(fundamentals_analyst, "fetch_quarterly_filing") as fetch:
        assert fundamentals_analyst._prefetch_filing_context(state(asset_type="crypto")) is None
    fetch.assert_not_called()


@pytest.mark.unit
def test_unavailable_filing_becomes_context_instead_of_crashing():
    with mock.patch.object(
        fundamentals_analyst, "fetch_quarterly_filing", side_effect=ValueError("not an SEC issuer")
    ):
        message = fundamentals_analyst._prefetch_filing_context(state())
    assert "SEC quarterly filing unavailable" in message.content
    assert "not an SEC issuer" in message.content


@pytest.mark.unit
def test_cache_failure_panics_instead_of_silently_continuing():
    error = fundamentals_analyst.SecEdgarCacheError("disk full")
    with mock.patch.object(
        fundamentals_analyst, "fetch_quarterly_filing", side_effect=error
    ), pytest.raises(fundamentals_analyst.SecEdgarCacheError, match="disk full"):
        fundamentals_analyst._prefetch_filing_context(state())
