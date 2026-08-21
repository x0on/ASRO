from __future__ import annotations

from asro.collectors.google_news import GoogleNewsCollector
from asro.models import SourceItem


class ExternalPressureCollector:
    name = "external-competitive-pressure"

    def __init__(self) -> None:
        self._collector = GoogleNewsCollector(
            [
                '"frontier model" China benchmark price',
                '"open weight" frontier model benchmark',
                '"AI model" China cheaper than OpenAI',
                '"AI model" price performance China',
                '"frontier model" inference cost China',
                '"open source" AI model beats benchmark',
                '"DeepSeek" model pricing benchmark',
                '"Qwen" model pricing benchmark',
            ]
        )

    def collect(self) -> list[SourceItem]:
        return self._collector.collect()
