from __future__ import annotations

import time
from typing import Any

import requests
from pydantic import HttpUrl

from asro.models import SourceItem

INTERESTING_FORMS = {"8-K", "10-Q", "10-K", "S-1", "S-1/A", "424B4", "6-K", "20-F"}


class SecCollector:
    name = "sec-edgar"

    def __init__(
        self,
        companies: list[dict[str, Any]],
        user_agent: str,
        request_delay_seconds: float = 0.2,
    ) -> None:
        self._companies = companies
        self._headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        self._delay = request_delay_seconds

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

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accession_numbers = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        primary_documents = recent.get("primaryDocument", [])

        items: list[SourceItem] = []

        for form, accession, filing_date, document in zip(
            forms,
            accession_numbers,
            filing_dates,
            primary_documents,
            strict=False,
        ):
            if form not in INTERESTING_FORMS:
                continue

            accession_no_dash = accession.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dash}/{document}"
            )

            items.append(
                SourceItem(
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
            )

        return items[:30]
