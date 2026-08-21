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
        },
        {
            "item_id": "unresolved",
            "title": "Economic evidence awaiting entity resolution",
            "companies": "[]",
            "category": "General AI capital",
        },
    ]

    network = _build_network(events, items)

    evidence = [node for node in network["nodes"] if node["kind"] == "evidence"]
    assert {node["label"] for node in evidence} == {
        "Nvidia reports earnings",
        "Economic evidence awaiting entity resolution",
    }
    assert any(edge["type"] == "EVIDENCE" for edge in network["edges"])
    assert {node["id"] for node in evidence} == {"evidence:abc", "evidence:unresolved"}


def test_network_uses_storage_item_id_for_unique_evidence_nodes() -> None:
    network = _build_network(
        [],
        [
            {"id": "first", "title": "First source", "companies": "[]"},
            {"id": "second", "title": "Second source", "companies": "[]"},
        ],
    )

    assert {node["id"] for node in network["nodes"]} == {
        "evidence:first",
        "evidence:second",
    }


def test_build_static_site_on_empty_db_reports_insufficient_evidence(tmp_path: Path) -> None:
    out = build_static_site(output_dir=tmp_path / "site", database_path=tmp_path / "empty.db")

    assert (out / "index.html").exists()
    assert (out / ".nojekyll").exists()
    html = (out / "index.html").read_text()
    assert 'id="companyLabelsToggle"' in html
    assert "drawCompanyLabels" in html
    payload = json.loads((out / "data" / "snapshot.json").read_text())
    assert payload["event_count"] == 0
    assert payload["review_counts"] == {
        "provisional": 0,
        "confirmed": 0,
        "flagged": 0,
        "flagged_retry_pending": 0,
    }
    assert payload["signal"]["score"] is None
    assert payload["signal"]["label"] == "INSUFFICIENT EVIDENCE"
    assert payload["signal"]["direction"] == "unknown"
    assert payload["network"] == {"nodes": [], "edges": []}
    assert len(payload["measurements"]) == 13
    assert "Anthropic" in payload["tracked_entities"]
    assert "DeepSeek" in payload["tracked_entities"]
    assert "Alibaba" in payload["tracked_entities"]
    assert payload["dimension_evidence"] == {}
    assert payload["dimension_evidence_items"] == {}
    assert all(value is None for value in payload["dimensions"].values())
