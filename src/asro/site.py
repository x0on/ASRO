from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from asro.indicators import compute_convergence, compute_dimension_scores
from asro.settings import Settings
from asro.storage import SqliteRepository

THESIS = (
    "What if the AI boom isn’t a bubble—but the construction of a financial "
    "system that could make the next collapse everyone’s problem?"
)

THESIS_EXPLANATION = (
    "ASRO is an open-source experiment built to test a hypothesis: that unprecedented "
    "AI spending, circular investment, debt, guarantees, infrastructure commitments "
    "and increasingly concentrated valuations could create a deeply interconnected "
    "financial system—and that, as these companies enter public markets and major "
    "indexes, some of that risk could gradually migrate into funds, pensions and "
    "ordinary retirement savings. ASRO does not assume this collapse will happen. "
    "It continuously collects evidence to determine whether the conditions that "
    "could produce it are actually forming—or whether the hypothesis is wrong."
)


_INDEX_HTML = files("asro.templates") / "index.html"


def _safe_rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _build_network(
    events: list[dict[str, Any]], items: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    edge_counts: Counter[tuple[str, str, str]] = Counter()
    edge_mentions: Counter[tuple[str, str, str]] = Counter()
    node_counts: Counter[str] = Counter()

    for event in events:
        source = event.get("source_entity")
        target = event.get("target_entity")
        event_type = event.get("event_type") or "RELATIONSHIP"

        if source:
            node_counts[source] += 1
        if target:
            node_counts[target] += 1
        if source and target and source != target:
            edge_counts[(source, target, event_type)] += 1
            edge_mentions[(source, target, event_type)] += int(event.get("mention_count") or 1)

    nodes = [
        {"id": name, "label": name, "kind": "company", "weight": count}
        for name, count in node_counts.most_common(40)
    ]
    allowed = {node["id"] for node in nodes}
    edges = [
        {
            "source": s,
            "target": t,
            "type": typ,
            "weight": count,
            "mentions": edge_mentions[(s, t, typ)],
        }
        for (s, t, typ), count in edge_counts.items()
        if s in allowed and t in allowed
    ]

    for item in items or []:
        try:
            companies = json.loads(item.get("companies") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        linked = [company for company in companies if company in allowed]
        if not linked:
            continue
        evidence_id = f"evidence:{item.get('item_id')}"
        nodes.append(
            {
                "id": evidence_id,
                "label": item.get("title"),
                "kind": "evidence",
                "category": item.get("category"),
                "source": item.get("source"),
                "url": item.get("url"),
                "date": item.get("published_at"),
                "weight": 1,
            }
        )
        edges.extend(
            {
                "source": company,
                "target": evidence_id,
                "type": "EVIDENCE",
                "weight": 1,
                "mentions": 1,
            }
            for company in linked
        )
    return {"nodes": nodes, "edges": edges}


def _build_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline = []
    for event in events:
        when = event.get("effective_date") or event.get("published_at")
        if not when:
            continue
        timeline.append(
            {
                "date": str(when),
                "event_type": event.get("event_type"),
                "source_entity": event.get("source_entity"),
                "target_entity": event.get("target_entity"),
                "amount": event.get("amount"),
                "currency": event.get("currency"),
                "confidence": event.get("confidence"),
                "mentions": event.get("mention_count") or 1,
                "review_status": event.get("review_status") or "provisional",
                "evidence_text": event.get("evidence_text"),
                "title": event.get("title"),
                "url": event.get("url"),
                "source": event.get("source"),
            }
        )
    timeline.sort(key=lambda e: str(e["date"]))
    return timeline[-500:]


def build_static_site(output_dir: Path = Path("site"), database_path: Path | None = None) -> Path:
    settings = Settings()
    repository = SqliteRepository(database_path or settings.database_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    with repository.connect() as connection:
        items = _safe_rows(repository.top_items(connection, limit=600))
        events = _safe_rows(repository.canonical_events(connection, limit=1200))
        mention_count = repository.event_count(connection)
        runs = _safe_rows(repository.latest_runs(connection))
        observations = _safe_rows(repository.recent_observations(connection, limit=2000))
        history = _safe_rows(repository.recent_snapshots(connection, limit=365))
        review_counts = repository.review_counts(connection)

    dimensions = compute_dimension_scores(observations)
    convergence = compute_convergence(dimensions)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "thesis": THESIS,
        "thesis_explanation": THESIS_EXPLANATION,
        "signal": convergence.model_dump(),
        "dimensions": dimensions,
        "history": history,
        "network": _build_network(events, items[:350]),
        "timeline": _build_timeline(events),
        "collector_runs": runs,
        "document_count": len(items),
        "event_count": len(events),
        "mention_count": mention_count,
        "review_counts": review_counts,
    }

    (data_dir / "snapshot.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    (output_dir / "index.html").write_text(
        _INDEX_HTML.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")
    return output_dir
