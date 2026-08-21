from pathlib import Path

from asro.dedupe import economic_fingerprint
from asro.extraction.deterministic import DeterministicEventExtractor
from asro.measurement import event_to_observation
from asro.models import SourceItem
from asro.reviewer import (
    EvidenceReviewer,
    ReviewBatch,
    ReviewDecision,
    _review_payload,
    preflight_reason,
)
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


class ConfirmingReviewer(EvidenceReviewer):
    calls = 0

    def _request(self, rows: list[dict]) -> ReviewBatch:
        self.calls += 1
        return ReviewBatch(
            decisions=[
                ReviewDecision(
                    fingerprint=row["fingerprint"],
                    decision="confirm",
                    canonical_fingerprint=None,
                    confidence=0.95,
                    reasoning="The excerpt directly supports the event.",
                )
                for row in rows
            ]
        )


def _event(repo: SqliteRepository, connection, n: int, date: str, amount_billions: int = 30) -> str:
    item = score(
        SourceItem(
            title=f"Nvidia guarantees ${amount_billions} billion financing for OpenAI.",
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
                canonical_fingerprint=None,
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


def test_reviewer_commits_small_batches(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "test.db")
    with repo.connect() as connection:
        for n in range(3):
            _event(repo, connection, n, f"2026-08-{20 + n}", amount_billions=30 + n)
        connection.commit()
    settings = Settings(database_path=tmp_path / "test.db", openai_api_key="test")
    reviewer = ConfirmingReviewer(settings, repo)

    assert reviewer.run(limit=3, batch_size=2) == 3
    assert reviewer.calls == 2
    with repo.connect() as connection:
        assert repo.review_counts(connection)["confirmed"] == 3


def test_reviewer_ignores_unknown_ids_and_uses_one_decision_per_input(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "test.db")
    with repo.connect() as connection:
        fingerprint = _event(repo, connection, 1, "2026-08-31")
        connection.commit()
    batch = ReviewBatch(
        decisions=[
            ReviewDecision(
                fingerprint="invented",
                decision="flag",
                canonical_fingerprint=None,
                confidence=0.2,
                reasoning="Unknown identifier.",
            ),
            ReviewDecision(
                fingerprint=fingerprint,
                decision="flag",
                canonical_fingerprint=None,
                confidence=0.6,
                reasoning="First repeated decision.",
            ),
            ReviewDecision(
                fingerprint=fingerprint,
                decision="confirm",
                canonical_fingerprint=None,
                confidence=0.95,
                reasoning="Final decision is directly supported.",
            ),
        ]
    )
    settings = Settings(database_path=tmp_path / "test.db", openai_api_key="test")

    assert FakeReviewer(settings, repo, batch).run(limit=1) == 1
    with repo.connect() as connection:
        assert repo.review_counts(connection)["confirmed"] == 1
        assert connection.execute("SELECT COUNT(*) FROM evidence_reviews").fetchone()[0] == 1


def test_flagged_event_receives_one_source_aware_retry(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "test.db")
    with repo.connect() as connection:
        fingerprint = _event(repo, connection, 1, "2026-08-31")
        repo.apply_review(
            connection,
            fingerprint,
            "flag",
            None,
            0.8,
            "The first review could not verify the event from the short excerpt.",
            "first-review",
            "2026-09-01T00:00:00+00:00",
        )
        connection.commit()
    settings = Settings(database_path=tmp_path / "test.db", openai_api_key="test")
    reviewer = ConfirmingReviewer(settings, repo)

    assert reviewer.run(limit=0, retry_flagged_limit=1) == 1
    assert reviewer.run(limit=0, retry_flagged_limit=1) == 0
    with repo.connect() as connection:
        assert repo.review_counts(connection)["confirmed"] == 1
        assert connection.execute("SELECT COUNT(*) FROM evidence_reviews").fetchone()[0] == 2


def test_review_payload_bounds_full_source_around_evidence() -> None:
    evidence = "Nvidia guaranteed $30 billion for OpenAI."
    payload = _review_payload(
        {
            "fingerprint": "event",
            "evidence_text": evidence,
            "source_text": f"{'before ' * 500}{evidence}{' after' * 500}",
        }
    )

    assert "source_text" not in payload
    assert evidence in payload["source_context"]
    assert len(payload["source_context"]) < 3_000


def test_preflight_flags_placeholder_hypothetical_and_implausible_evidence() -> None:
    base = {"event_type": "GUARANTEES", "amount": None}
    assert preflight_reason(
        {**base, "evidence_text": "Review for debt, guarantees, capital expenditure and leases."}
    )
    assert preflight_reason(
        {**base, "evidence_text": "These obligations could adversely affect our business."}
    )
    assert preflight_reason(
        {**base, "amount": 8_000_000_000_000, "evidence_text": "A guarantee was reported."}
    )
    assert (
        preflight_reason(
            {**base, "amount": 30_000_000_000, "evidence_text": "Nvidia guaranteed $30 billion."}
        )
        is None
    )
