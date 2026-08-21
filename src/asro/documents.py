from __future__ import annotations

from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class FetchedDocument:
    text: str
    content_type: str
    status: str


class DocumentFetcher:
    def __init__(self, user_agent: str, max_chars: int = 400_000) -> None:
        self.headers = {
            "User-Agent": user_agent or "ASRO research observatory",
            "Accept": "text/html,application/xhtml+xml,text/plain",
        }
        self.max_chars = max_chars

    def fetch(self, url: str) -> FetchedDocument:
        try:
            r = requests.get(url, headers=self.headers, timeout=25)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").lower()
            if "html" in ctype or not ctype:
                soup = BeautifulSoup(r.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "noscript"]):
                    tag.decompose()
                text = " ".join(soup.get_text(" ", strip=True).split())
            elif "text" in ctype or "json" in ctype:
                text = " ".join(r.text.split())
            else:
                return FetchedDocument("", ctype, "unsupported")
            return FetchedDocument(text[: self.max_chars], ctype, "ok")
        except Exception:
            return FetchedDocument("", "", "error")
