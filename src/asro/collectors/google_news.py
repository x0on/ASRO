from __future__ import annotations

from urllib.parse import quote_plus

import feedparser
from bs4 import BeautifulSoup
from pydantic import HttpUrl

from asro.models import SourceItem


def _clean_html(value: str) -> str:
    soup = BeautifulSoup(value or "", "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


class GoogleNewsCollector:
    name = "google-news-rss"

    def __init__(self, queries: list[str]) -> None:
        self._queries = queries

    def collect(self) -> list[SourceItem]:
        items: list[SourceItem] = []

        for query in self._queries:
            url = (
                "https://news.google.com/rss/search?"
                f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
            )
            feed = feedparser.parse(url)

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

        return items
