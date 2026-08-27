from pathlib import Path

from asro.dedupe import economic_fingerprint
from asro.extraction.deterministic import DeterministicEventExtractor
from asro.measurement import event_to_observation
from asro.models import EventType, FinancialEvent, SourceItem
from asro.scoring import score
from asro.site import _build_network
from asro.storage import SqliteRepository


def test_insert_deduplicates(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "test.db")
    item = SourceItem(
        title="Example",
        url="https://example.com/item",
        source="Example",
    )
    scored = score(item, [])

    with repo.connect() as connection:
        assert repo.insert(connection, scored) is True
        assert repo.insert(connection, scored) is False


def test_insert_event_and_observation_deduplicate(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "test.db")
    source = score(
        SourceItem(
            title="Nvidia guarantee filing",
            url="https://example.com/guarantee",
            source="Test filing",
        ),
        [],
    )
    event = FinancialEvent(
        event_id="e1",
        document_id=source.item_id,
        event_type=EventType.GUARANTEES,
        source_entity="Nvidia",
        target_entity="OpenAI",
        amount=30_000_000_000,
        currency="USD",
        confidence=0.9,
        evidence_text="Nvidia guarantees $30 billion.",
        extractor="test",
    )
    observation = event_to_observation(event)
    assert observation is not None

    with repo.connect() as connection:
        assert repo.insert(connection, source) is True
        assert repo.insert_event(connection, event) is True
        assert repo.insert_event(connection, event) is False
        assert repo.insert_observation(connection, observation) is True
        assert repo.insert_observation(connection, observation) is False
        assert repo.event_count(connection) == 1


def test_register_economic_event_counts_mentions(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "test.db")
    with repo.connect() as connection:
        assert repo.register_economic_event(connection, "fp", "e1", "2026-01-01") is True
        assert repo.register_economic_event(connection, "fp", "e2", "2026-01-02") is False
        row = connection.execute("SELECT mention_count, last_seen FROM economic_events").fetchone()
        assert (row["mention_count"], row["last_seen"]) == (2, "2026-01-02")


def test_twenty_reports_of_one_deal_count_once(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "test.db")
    extractor = DeterministicEventExtractor(["Nvidia", "OpenAI"])
    sentence = "Nvidia guarantees $30 billion financing for OpenAI."

    with repo.connect() as connection:
        observations = 0
        for n in range(20):
            item = score(
                SourceItem(
                    title=sentence,
                    url=f"https://example.com/story-{n}",
                    source=f"Outlet {n}",
                    published_at="2026-08-20",
                ),
                ["Nvidia", "OpenAI"],
            )
            assert repo.insert(connection, item)
            for event in extractor.extract(item):
                fingerprint = economic_fingerprint(event)
                is_new = repo.register_economic_event(
                    connection, fingerprint, event.event_id, "2026-08-20"
                )
                repo.insert_event(connection, event)
                if is_new:
                    observation = event_to_observation(event)
                    assert observation is not None
                    repo.insert_observation(connection, observation)
                    observations += 1

        canonical = [dict(r) for r in repo.canonical_events(connection)]
        assert repo.event_count(connection) == 20  # provenance mentions are kept
        assert repo.canonical_event_count(connection) == 1
        assert canonical[0]["mention_count"] == 20
        assert observations == 1

    network = _build_network(canonical)
    assert len(network["edges"]) == 1
    assert network["edges"][0]["weight"] == 1
    assert network["edges"][0]["mentions"] == 20
