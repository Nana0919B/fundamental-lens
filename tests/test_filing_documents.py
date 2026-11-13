"""Reuse EDGAR submissions and cache; preserve original and amendment links."""

from tradingagents.dataflows.vendors import sec_edgar


def test_original_and_amended_reports_are_separate_and_date_filtered(monkeypatch):
    monkeypatch.setattr(sec_edgar, "cik_for", lambda t: "0000320193")
    monkeypatch.setattr(
        sec_edgar,
        "_cached_json",
        lambda *a: {
            "filings": {
                "recent": {
                    "form": ["10-K/A", "10-K", "10-Q", "8-K"],
                    "filingDate": ["2026-11-12", "2026-10-30", "2026-12-01", "2026-11-11"],
                    "reportDate": ["2026-09-30"] * 4,
                    "accessionNumber": [
                        "0001-26-00001",
                        "0001-26-00002",
                        "0001-26-00003",
                        "0001-26-00004",
                    ],
                    "primaryDocument": ["amendment.htm", "annual.htm", "future.htm", "event.htm"],
                }
            }
        },
    )
    result = sec_edgar.get_filing_documents("AAPL", "2026-11-13")
    assert len(result["documents"]) == 2
    assert result["documents"][0]["amendment"] is True
    assert result["documents"][1]["amendment"] is False
    assert result["documents"][0]["url"].endswith("/320193/00012600001/amendment.htm")


def test_non_sec_company_has_explicit_coverage_note(monkeypatch):
    monkeypatch.setattr(sec_edgar, "cik_for", lambda t: None)
    result = sec_edgar.get_filing_documents("0700.HK", "2026-11-13")
    assert result["documents"] == []
    assert result["note"]
