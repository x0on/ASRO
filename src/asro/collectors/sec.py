from __future__ import annotations

import time
from datetime import date
from typing import Any

import requests
from pydantic import HttpUrl

from asro.models import SourceItem

INTERESTING_FORMS = {"8-K", "10-Q", "10-K", "S-1", "S-1/A", "424B4", "6-K", "20-F"}
CORE_HISTORICAL_FORMS = {"10-Q", "10-K", "S-1", "S-1/A", "424B4", "20-F"}


def _filing_records(data: dict[str, Any]) -> list[dict[str, str]]:
    recent = data.get("filings", {}).get("recent", data)
    return [
        {
            "form": str(form),
            "accession": str(accession),
            "filing_date": str(filing_date),
            "document": str(document),
        }
        for form, accession, filing_date, document in zip(
            recent.get("form", []),
            recent.get("accessionNumber", []),
            recent.get("filingDate", []),
            recent.get("primaryDocument", []),
            strict=False,
        )
    ]


class SecCollector:
    name = "sec-edgar"

    def __init__(
        self,
        companies: list[dict[str, Any]],
        user_agent: str,
        request_delay_seconds: float = 0.2,
        since: date | None = None,
        max_per_company: int = 3,
    ) -> None:
        self._companies = companies
        self._headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        self._delay = request_delay_seconds
        self._since = since
        self._max_per_company = max_per_company

    def collect(self) -> list[SourceItem]:
        items: list[SourceItem] = []

        for company in self._companies:
            items.extend(self._collect_company(company["name"], int(company["cik"])))
            time.sleep(self._delay)

        return items

    def _collect_company(self, name: str, cik: int) -> list[SourceItem]:
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        response = requests.get(url, headers=self._headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        items: list[SourceItem] = []

        for record in _filing_records(data):
            if record["form"] not in INTERESTING_FORMS:
                continue
            if self._since is not None and record["filing_date"] < self._since.isoformat():
                continue
            items.append(self._source_item(name, cik, record))

        return items[: self._max_per_company]

    @staticmethod
    def _source_item(name: str, cik: int, record: dict[str, str]) -> SourceItem:
        form = record["form"]
        filing_date = record["filing_date"]
        accession_no_dash = record["accession"].replace("-", "")
        filing_url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dash}/"
            f"{record['document']}"
        )
        return SourceItem(
            title=f"{name} SEC filing: {form} ({filing_date})",
            url=HttpUrl(filing_url),
            summary=(
                f"SEC filing {form} for {name}. Review for debt, guarantees, "
                "capital expenditure, customer concentration, refinancing, "
                "AI infrastructure commitments, or IPO information."
            ),
            published_at=filing_date,
            source="SEC EDGAR",
        )


class HistoricalSecCollector(SecCollector):
    """Key SEC filings for a bounded historical baseline."""

    name = "sec-edgar-history"

    def _collect_company(self, name: str, cik: int) -> list[SourceItem]:
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        response = requests.get(url, headers=self._headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        records = _filing_records(data)

        for archive in data.get("filings", {}).get("files", []):
            archive_end = str(archive.get("filingTo", ""))
            if self._since is not None and archive_end < self._since.isoformat():
                continue
            archive_url = f"https://data.sec.gov/submissions/{archive['name']}"
            archive_response = requests.get(archive_url, headers=self._headers, timeout=30)
            archive_response.raise_for_status()
            records.extend(_filing_records(archive_response.json()))
            time.sleep(self._delay)

        since = self._since.isoformat() if self._since is not None else ""
        relevant = {
            record["accession"]: record
            for record in records
            if record["form"] in INTERESTING_FORMS and record["filing_date"] >= since
        }
        ordered = sorted(relevant.values(), key=lambda record: record["filing_date"], reverse=True)
        core = [record for record in ordered if record["form"] in CORE_HISTORICAL_FORMS]
        current = [record for record in ordered if record["form"] not in CORE_HISTORICAL_FORMS]
        selected = (core + current)[: self._max_per_company]
        return [self._source_item(name, cik, record) for record in selected]
