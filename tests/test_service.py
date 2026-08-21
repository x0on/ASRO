from collections.abc import Iterator
from pathlib import Path

import pytest

from asro.documents import FetchedDocument
from asro.models import SourceItem
from asro.service import MonitorService
from asro.settings import Settings


def _item(n: int) -> SourceItem:
    return SourceItem(
        title=f"Nvidia guarantees $30 billion financing for OpenAI ({n}).",
        url=f"https://example.com/story-{n}",
        source="Example",
        published_at="2026-08-20",
    )


class FlakyCollector:
    name = "flaky"

    def collect(self) -> Iterator[SourceItem]:
        yield _item(1)  # fails mid-stream, after one good item
        raise RuntimeError("source went away")


class GoodCollector:
    name = "good"

    def collect(self) -> list[SourceItem]:
        return [_item(2), _item(3)]


def _service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collectors: list) -> MonitorService:
    monkeypatch.chdir(tmp_path)
    service = MonitorService(Settings(database_path=tmp_path / "t.db"))
    monkeypatch.setattr(service, "_collectors", lambda: collectors)
    monkeypatch.setattr(service, "_fetcher", _fetcher("ok"))
    monkeypatch.setitem(service._config["monitor"], "request_delay_seconds", 0)
    return service


def _fetcher(status: str) -> object:
    class Fetcher:
        def fetch(self, url: str) -> FetchedDocument:
            return FetchedDocument("", "text/html", status)

    return Fetcher()


def test_failed_collector_rolls_back_its_data_and_keeps_error_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, monkeypatch, [FlakyCollector(), GoodCollector()])

    summary = service.run()

    assert summary.failed == ["flaky"]
    assert summary.ok is False
    assert summary.new_items == 2  # only the good collector's items survived
    assert service.db_count() == 2
    runs = {r["collector"]: r for r in service.freshness()}
    assert runs["flaky"]["status"] == "error"
    assert "RuntimeError" in runs["flaky"]["error"]
    assert runs["good"]["status"] == "ok"
    assert service.event_count() == 1  # one economic fact, reported by two documents


def test_fetch_failures_mark_run_degraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path, monkeypatch, [GoodCollector()])
    monkeypatch.setattr(service, "_fetcher", _fetcher("error"))

    summary = service.run()

    assert summary.ok is True
    assert summary.degraded == ["good"]
    run = service.freshness()[0]
    assert run["status"] == "degraded"
    assert run["error"] == "2 of 2 document fetches failed"
