import json
from pathlib import Path

from asro.site import _build_network, build_static_site


def test_network_weights_distinct_facts_and_carries_mentions() -> None:
    events = [
        {
            "source_entity": "Nvidia",
            "target_entity": "OpenAI",
            "event_type": "INVESTS_IN",
            "mention_count": 7,
        },
        {
            "source_entity": "Nvidia",
            "target_entity": "OpenAI",
            "event_type": "LENDS_TO",
            "mention_count": 1,
        },
    ]
    network = _build_network(events)
    assert {n["id"] for n in network["nodes"]} == {"Nvidia", "OpenAI"}
    by_type = {e["type"]: e for e in network["edges"]}
    assert by_type["INVESTS_IN"]["weight"] == 1
    assert by_type["INVESTS_IN"]["mentions"] == 7
    assert by_type["LENDS_TO"]["weight"] == 1


def test_network_includes_company_news_as_evidence_nodes() -> None:
    events = [{"source_entity": "Nvidia", "event_type": "REVENUE_REPORT"}]
    items = [
        {
            "item_id": "abc",
            "title": "Nvidia reports earnings",
            "companies": '["Nvidia"]',
            "category": "General AI capital",
            "source": "Example",
            "url": "https://example.com/news",
            "published_at": "2026-08-21",
        }
    ]

    network = _build_network(events, items)

    evidence = next(node for node in network["nodes"] if node["kind"] == "evidence")
    assert evidence["label"] == "Nvidia reports earnings"
    assert any(edge["type"] == "EVIDENCE" for edge in network["edges"])


def test_build_static_site_on_empty_db_reports_insufficient_evidence(tmp_path: Path) -> None:
    out = build_static_site(output_dir=tmp_path / "site", database_path=tmp_path / "empty.db")

    assert (out / "index.html").exists()
    assert (out / ".nojekyll").exists()
    payload = json.loads((out / "data" / "snapshot.json").read_text())
    assert payload["event_count"] == 0
    assert payload["review_counts"] == {"provisional": 0, "confirmed": 0, "flagged": 0}
    assert payload["signal"]["score"] is None
    assert payload["signal"]["label"] == "INSUFFICIENT EVIDENCE"
    assert payload["signal"]["direction"] == "unknown"
    assert payload["network"] == {"nodes": [], "edges": []}
    assert all(value is None for value in payload["dimensions"].values())
