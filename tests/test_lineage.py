from pathlib import Path

from asro.lineage import VERIFIED_LINEAGE, seed_verified_lineage
from asro.storage import SqliteRepository


def test_verified_lineage_is_idempotent_and_confirmed(tmp_path: Path) -> None:
    repository = SqliteRepository(tmp_path / "lineage.db")

    assert seed_verified_lineage(repository) == len(VERIFIED_LINEAGE)
    assert seed_verified_lineage(repository) == 0

    with repository.connect() as connection:
        assert repository.canonical_event_count(connection) == len(VERIFIED_LINEAGE)
        assert repository.review_counts(connection)["confirmed"] == len(VERIFIED_LINEAGE)
        events = repository.canonical_events(connection)
        assert {row["event_type"] for row in events} >= {"ACQUIRES", "ASSUMES_DEBT"}
        assert any(row["amount"] == 20_000_000_000 for row in events)
