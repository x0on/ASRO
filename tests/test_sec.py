from datetime import date

from asro.collectors.sec import HistoricalSecCollector


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_historical_sec_follows_archives_and_prioritizes_core_filings(monkeypatch) -> None:
    recent = {
        "filings": {
            "recent": {
                "form": ["8-K"],
                "accessionNumber": ["0001-26-000001"],
                "filingDate": ["2026-07-01"],
                "primaryDocument": ["current.htm"],
            },
            "files": [
                {
                    "name": "CIK0000000001-submissions-001.json",
                    "filingFrom": "2023-01-01",
                    "filingTo": "2024-12-31",
                }
            ],
        }
    }
    archive = {
        "form": ["10-K", "8-K"],
        "accessionNumber": ["0001-24-000001", "0001-24-000002"],
        "filingDate": ["2024-02-01", "2024-03-01"],
        "primaryDocument": ["annual.htm", "older-current.htm"],
    }
    responses = iter([_Response(recent), _Response(archive)])
    monkeypatch.setattr("asro.collectors.sec.requests.get", lambda *args, **kwargs: next(responses))

    collector = HistoricalSecCollector(
        [{"name": "Example", "cik": 1}],
        "ASRO test@example.com",
        request_delay_seconds=0,
        since=date(2023, 8, 21),
        max_per_company=1,
    )

    items = collector.collect()

    assert len(items) == 1
    assert "10-K (2024-02-01)" in items[0].title
