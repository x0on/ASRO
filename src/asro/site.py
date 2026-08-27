from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from asro.dictionary.registry import VARIABLES
from asro.entities import canonicalize, canonicalize_many
from asro.indicators import (
    compute_convergence,
    compute_dimension_scores,
    dimension_directional_readings,
    dimension_evidence_basis,
    dimension_evidence_counts,
    latest_observations,
    overall_evidence_direction,
)
from asro.settings import Settings, load_project_config
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
        raw_source = event.get("source_entity")
        raw_target = event.get("target_entity")
        source = canonicalize(str(raw_source)) if raw_source else None
        target = canonicalize(str(raw_target)) if raw_target else None
        event_type = event.get("event_type") or "RELATIONSHIP"

        if source:
            node_counts[source] += 1
        if target:
            node_counts[target] += 1
        if source and target and source != target:
            edge_counts[(source, target, event_type)] += 1
            edge_mentions[(source, target, event_type)] += int(event.get("mention_count") or 1)

    prepared_items: list[tuple[dict[str, Any], list[str]]] = []
    for item in items or []:
        try:
            raw_companies = json.loads(item.get("companies") or "[]")
            companies = canonicalize_many([str(value) for value in raw_companies if value])
        except (json.JSONDecodeError, TypeError):
            companies = []
        prepared_items.append((item, companies))
        for company in companies:
            node_counts[company] += 0

    index_entities = {"Nasdaq", "Nasdaq-100"}
    nodes: list[dict[str, Any]] = [
        {
            "id": name,
            "label": name,
            "kind": "index" if name in index_entities else "company",
            "weight": count,
        }
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

    for item, companies in prepared_items:
        linked = [company for company in companies if company in allowed]
        item_id = item.get("item_id") or item.get("id")
        if not item_id:
            continue
        evidence_id = f"evidence:{item_id}"
        nodes.append(
            {
                "id": evidence_id,
                "label": item.get("title"),
                "kind": "evidence",
                "category": item.get("category"),
                "source": item.get("source"),
                "url": item.get("url"),
                "date": item.get("published_at"),
                "summary": item.get("summary"),
                "score": item.get("score"),
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
                "source_entity": canonicalize(event.get("source_entity")),
                "target_entity": canonicalize(event.get("target_entity")),
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
    return timeline


def _build_dimension_evidence(
    observations: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Expose the exact reviewed records eligible for each current dimension score."""
    event_by_id = {str(event.get("event_id")): event for event in events}
    result: dict[str, list[dict[str, Any]]] = {}
    for observation in latest_observations(observations, datetime.now(UTC)):
        definition = VARIABLES.get(str(observation.get("variable_key")))
        if definition is None:
            continue
        event = event_by_id.get(str(observation.get("event_id")), {})
        unit = str(observation.get("unit") or "")
        role = "directional signal" if unit == "signal" else "measured value"
        dimension = definition.dimension.value
        result.setdefault(dimension, []).append(
            {
                "variable": definition.label,
                "entity": canonicalize(observation.get("entity")),
                "value": observation.get("value"),
                "unit": unit,
                "role": role,
                "effect": (
                    "raises the warning"
                    if observation.get("polarity") == "risk"
                    else "lowers the warning"
                ),
                "confidence": observation.get("confidence"),
                "date": event.get("effective_date") or event.get("published_at"),
                "event_type": event.get("event_type"),
                "title": event.get("title") or observation.get("evidence_text"),
                "source": event.get("source"),
                "url": event.get("url"),
                "evidence": observation.get("evidence_text"),
            }
        )
    for evidence in result.values():
        evidence.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    return result


def build_static_site(output_dir: Path = Path("site"), database_path: Path | None = None) -> Path:
    settings = Settings()
    config = load_project_config(settings.config_path)
    repository = SqliteRepository(database_path or settings.database_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    with repository.connect() as connection:
        items = _safe_rows(repository.top_items(connection, limit=1500))
        events = _safe_rows(repository.canonical_events(connection, limit=5000))
        mention_count = repository.event_count(connection)
        runs = _safe_rows(repository.latest_runs(connection))
        observations = _safe_rows(repository.recent_observations(connection, limit=2000))
        history = _safe_rows(repository.recent_snapshots(connection, limit=365))
        review_counts = repository.review_counts(connection)
        feature_family = _feature_family_rows(connection)

    dimensions = compute_dimension_scores(observations)
    convergence = compute_convergence(dimensions)
    dimension_evidence = dimension_evidence_counts(observations)
    dimension_basis = dimension_evidence_basis(observations)
    dimension_direction = dimension_directional_readings(observations)
    signal = convergence.model_dump()
    signal["evidence_direction"] = overall_evidence_direction(dimension_direction)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "thesis": THESIS,
        "thesis_explanation": THESIS_EXPLANATION,
        "signal": signal,
        "dimensions": dimensions,
        "dimension_evidence": dimension_evidence,
        "dimension_basis": dimension_basis,
        "dimension_direction": dimension_direction,
        "dimension_evidence_items": _build_dimension_evidence(observations, events),
        "tracked_entities": canonicalize_many(config["entities"]["companies"]),
        "measurements": [
            {
                "key": definition.key,
                "label": definition.label,
                "dimension": definition.dimension.value,
                "description": definition.description,
                "unit": definition.unit,
                "direction": definition.direction,
                "weight": definition.weight,
            }
            for definition in VARIABLES.values()
        ],
        "history": history,
        "network": _build_network(events, items[:800]),
        "timeline": _build_timeline(events),
        "collector_runs": runs,
        "document_count": len(items),
        "event_count": len(events),
        "mention_count": mention_count,
        "review_counts": review_counts,
        "feature_family": feature_family,
    }

    (data_dir / "snapshot.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    (output_dir / "index.html").write_text(
        _INDEX_HTML.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")
    return output_dir


def _feature_family_rows(connection: sqlite3.Connection) -> list[dict[str, object]]:
    """Return only finalized cells from the latest reviewed feature-family build."""
    build = connection.execute(
        """SELECT build.build_id FROM dataset_build build
           JOIN dataset_build_finalization finalized ON finalized.build_id=build.build_id
           WHERE build.feature_set_version='current-ai-feature-family-1.0.0'
           ORDER BY build.created_at DESC, build.build_id DESC LIMIT 1"""
    ).fetchone()
    if build is None:
        return []
    rows = connection.execute(
        """SELECT value.entity_id,value.period_start,value.period_end,value.feature_key,
                  value.feature_version,value.value_numeric,value.missingness_reason,
                  value.coverage,value.reliability,observation.availability_at,
                  observation.evidence_text,item.url
           FROM finalized_entity_feature_value value
           LEFT JOIN feature_value_fact fact
             ON fact.feature_value_id=value.feature_value_id
           LEFT JOIN observation_v2 observation
             ON observation.observation_id=fact.representative_observation_id
           LEFT JOIN items item ON item.id=observation.source_document_id
           WHERE value.build_id=? ORDER BY value.feature_key,value.entity_id,value.period_start""",
        (build[0],),
    )
    return [dict(row) for row in rows]
