from __future__ import annotations

import math
from datetime import date
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup
from pydantic import HttpUrl

from asro.models import SourceItem


def _clean_html(value: str) -> str:
    soup = BeautifulSoup(value or "", "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


class GoogleNewsCollector:
    name = "google-news-rss"

    def __init__(self, queries: list[str], max_items: int = 12) -> None:
        self._queries = queries
        self._max_items = max_items

    def collect(self) -> list[SourceItem]:
        items: list[SourceItem] = []

        for query in self._queries:
            if len(items) >= self._max_items:
                break
            url = (
                "https://news.google.com/rss/search?"
                f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
            )
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            feed = feedparser.parse(response.content)

            for entry in feed.entries:
                source_name = "Google News"
                source = getattr(entry, "source", None)
                if isinstance(source, dict):
                    source_name = source.get("title") or source_name

                items.append(
                    SourceItem(
                        title=_clean_html(getattr(entry, "title", "")),
                        url=HttpUrl(getattr(entry, "link", "")),
                        summary=_clean_html(getattr(entry, "summary", "")),
                        published_at=getattr(entry, "published", None),
                        source=source_name,
                    )
                )
                if len(items) >= self._max_items:
                    break

        return items


class HistoricalGoogleNewsCollector:
    """Bounded, one-time news baseline distributed across every configured query."""

    name = "google-news-history"

    def __init__(
        self,
        queries: list[str],
        since: date,
        until: date,
        max_items: int = 140,
    ) -> None:
        self._queries = queries
        self._since = since
        self._until = until
        self._max_items = max_items

    def collect(self) -> list[SourceItem]:
        if not self._queries or self._max_items <= 0:
            return []

        per_query = max(1, math.ceil(self._max_items / len(self._queries)))
        items: list[SourceItem] = []
        seen_urls: set[str] = set()

        for query in self._queries:
            dated_query = (
                f"{query} after:{self._since.isoformat()} before:{self._until.isoformat()}"
            )
            for item in GoogleNewsCollector([dated_query], max_items=per_query).collect():
                url = str(item.url)
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                items.append(item)

        return items[: self._max_items]


class CompanyEconomicNewsCollector(HistoricalGoogleNewsCollector):
    """Recent company-level economic news, separate from narrow risk-signal queries."""

    name = "company-economic-news"
