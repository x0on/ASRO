from __future__ import annotations

from typing import Protocol

from asro.models import SourceItem


class Collector(Protocol):
    name: str

    def collect(self) -> list[SourceItem]:
        """Collect and normalize source items."""
        ...
