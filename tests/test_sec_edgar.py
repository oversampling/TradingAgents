"""SEC EDGAR 10-Q retrieval tests; all network access is mocked."""

from unittest import mock

import pytest

from tradingagents.dataflows import sec_edgar


class FakeResponse:
    def __init__(self, *, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def recent_rows(forms, filing_dates, report_dates, accessions, documents):
    return {
        "form": forms,
        "filingDate": filing_dates,
        "reportDate": report_dates,
        "accessionNumber": accessions,
        "primaryDocument": documents,
    }


@pytest.mark.unit
def test_missing_user_agent_is_actionable():
    with mock.patch.dict("os.environ", {}, clear=True), pytest.raises(
        sec_edgar.SecEdgarNotConfiguredError, match="SEC_USER_AGENT"
    ):
        sec_edgar.get_user_agent()


@pytest.mark.unit
def test_cache_write_failure_panics_with_clear_error(tmp_path):
    target = tmp_path / "filing.htm"
    with mock.patch.object(
        type(target), "write_text", side_effect=OSError("disk full")
    ), pytest.raises(sec_edgar.SecEdgarCacheError, match="disk full"):
        sec_edgar._write_cache_text(target, "filing")


@pytest.mark.unit
def test_resolve_cik_accepts_dot_dash_alias_and_caches(tmp_path):
    payload = {"0": {"ticker": "BRK-B", "cik_str": 1067983}}
    with mock.patch.object(sec_edgar, "_cache_root", return_value=tmp_path), mock.patch.object(
        sec_edgar, "_get", return_value=FakeResponse(payload=payload)
    ) as getter:
        assert sec_edgar.resolve_cik("BRK.B") == 1067983
        assert sec_edgar.resolve_cik("BRK.B") == 1067983
    assert getter.call_count == 1


@pytest.mark.unit
def test_list_filings_enforces_cutoff_and_reads_archive():
    submissions = {
        "filings": {
            "recent": recent_rows(
                ["10-Q", "10-Q", "8-K"],
                ["2026-08-11", "2026-05-01", "2026-04-01"],
                ["2026-06-30", "2026-03-31", ""],
                ["0001-26-000003", "0001-26-000002", "0001-26-000001"],
                ["future.htm", "current.htm", "event.htm"],
            ),
            "files": [{"name": "CIK0000000001-submissions-001.json", "filingFrom": "2020-01-01"}],
        }
    }
    archived = recent_rows(
        ["10-Q"], ["2026-02-01"], ["2025-12-31"], ["0001-26-000000"], ["prior.htm"]
    )

    def fake_get(url):
        return FakeResponse(payload=archived if "submissions-001.json" in url else submissions)

    with mock.patch.object(sec_edgar, "_get", side_effect=fake_get):
        filings = sec_edgar.list_filings(1, "2026-08-10")

    assert [filing.primary_document for filing in filings] == ["current.htm", "prior.htm"]


@pytest.mark.unit
def test_select_quarters_uses_amendment_without_treating_original_as_prior():
    rows = [
        sec_edgar.Filing(1, "10-Q/A", "2026-05-10", "2026-03-31", "a", "amended.htm"),
        sec_edgar.Filing(1, "10-Q", "2026-05-01", "2026-03-31", "b", "original.htm"),
        sec_edgar.Filing(1, "10-Q", "2026-02-01", "2025-12-31", "c", "prior.htm"),
    ]
    selected = sec_edgar.select_quarters(rows)
    assert [row.primary_document for row in selected] == ["amended.htm", "prior.htm"]


@pytest.mark.unit
def test_extract_filing_text_removes_hidden_and_script_content():
    document = """
    <html><body><h1>Quarterly Report</h1>
    <script>ignore me</script><ix:hidden>hidden XBRL</ix:hidden>
    <div style="display: none"><span>also hidden</span></div>
    <p>Revenue &amp; income increased.</p></body></html>
    """
    result = sec_edgar.extract_filing_text(document)
    assert "Quarterly Report" in result
    assert "Revenue & income increased." in result
    assert "ignore me" not in result
    assert "hidden" not in result


@pytest.mark.unit
def test_fit_filing_text_preserves_later_mda_section():
    text = (
        "Opening financial statements.\n"
        + ("x" * 2_000)
        + "\nManagement's Discussion and Analysis\nRevenue declined."
    )
    fitted, truncated = sec_edgar._fit_filing_text(text, 1_000)
    assert truncated is True
    assert "Revenue declined." in fitted


@pytest.mark.unit
def test_get_quarterly_filing_returns_provenance_and_prior_comparison(tmp_path):
    filings = [
        sec_edgar.Filing(1, "10-Q", "2026-05-01", "2026-03-31", "0001-26-000002", "current.htm"),
        sec_edgar.Filing(1, "10-Q", "2026-02-01", "2025-12-31", "0001-26-000001", "prior.htm"),
    ]

    def fake_get(url):
        if url.endswith("current.htm"):
            return FakeResponse(text="<p>Current revenue was 100.</p>")
        if url.endswith("prior.htm"):
            return FakeResponse(text="<p>Prior revenue was 80.</p>")
        raise AssertionError(url)

    with mock.patch.object(sec_edgar, "resolve_cik", return_value=1), mock.patch.object(
        sec_edgar, "list_filings", return_value=filings
    ), mock.patch.object(sec_edgar, "_cache_root", return_value=tmp_path), mock.patch.object(
        sec_edgar, "_get", side_effect=fake_get
    ):
        result = sec_edgar.get_quarterly_filing("TEST", "2026-06-01")

    assert "Current quarter: SEC 10-Q" in result
    assert "Prior quarter comparison" in result
    assert "Current revenue was 100." in result
    assert "Prior revenue was 80." in result
    assert "0001-26-000002" in result
    assert "https://www.sec.gov/Archives/edgar/data/1/000126000002/current.htm" in result
    assert (tmp_path / "1" / "0001-26-000002" / "current.htm").exists()
    clean = tmp_path / "1" / "0001-26-000002" / "current.clean.txt"
    assert clean.exists()
    assert "Current revenue was 100." in clean.read_text(encoding="utf-8")
