from pathlib import Path

from asro.dedupe import economic_fingerprint
from asro.extraction.deterministic import DeterministicEventExtractor
from asro.measurement import event_to_observation
from asro.models import SourceItem
from asro.reviewer import EvidenceReviewer, ReviewBatch, ReviewDecision
from asro.scoring import score
from asro.settings import Settings
from asro.storage import SqliteRepository


class FakeReviewer(EvidenceReviewer):
    def __init__(
        self, settings: Settings, repository: SqliteRepository, batch: ReviewBatch
    ) -> None:
        super().__init__(settings, repository)
        self.batch = batch

    def _request(self, rows: list[dict]) -> ReviewBatch:
        return self.batch


def _event(repo: SqliteRepository, connection, n: int, date: str) -> str:
    item = score(
        SourceItem(
            title="Nvidia guarantees $30 billion financing for OpenAI.",
            url=f"https://example.com/{n}",
            source="Example",
            published_at=date,
        ),
        ["Nvidia", "OpenAI"],
    )
    assert repo.insert(connection, item)
    event = DeterministicEventExtractor(["Nvidia", "OpenAI"]).extract(item)[0]
    fingerprint = economic_fingerprint(event)
    assert repo.register_economic_event(connection, fingerprint, event.event_id, date)
    assert repo.insert_event(connection, event)
    observation = event_to_observation(event)
    assert observation is not None
    assert repo.insert_observation(connection, observation)
    return fingerprint


def test_reviewer_merges_provisional_events_without_deleting_provenance(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "test.db")
    with repo.connect() as connection:
        first = _event(repo, connection, 1, "2026-08-31")
        second = _event(repo, connection, 2, "2026-09-01")
        connection.commit()
    batch = ReviewBatch(
        decisions=[
            ReviewDecision(
                fingerprint=first,
                decision="confirm",
                confidence=0.98,
                reasoning="First account is canonical.",
            ),
            ReviewDecision(
                fingerprint=second,
                decision="merge",
                canonical_fingerprint=first,
                confidence=0.96,
                reasoning="Same transaction.",
            ),
        ]
    )
    settings = Settings(database_path=tmp_path / "test.db", openai_api_key="test")
    assert FakeReviewer(settings, repo, batch).run() == 2
    with repo.connect() as connection:
        assert repo.canonical_event_count(connection) == 1
        assert repo.event_count(connection) == 2
        canonical = repo.canonical_events(connection)[0]
        assert canonical["mention_count"] == 2
        assert canonical["review_status"] == "confirmed"
        assert len(repo.recent_observations(connection)) == 1
        assert connection.execute("SELECT COUNT(*) FROM evidence_reviews").fetchone()[0] == 2
