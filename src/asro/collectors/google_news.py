from __future__ import annotations

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
