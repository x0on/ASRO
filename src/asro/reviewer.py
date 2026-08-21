from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

import requests
from pydantic import BaseModel, ConfigDict, Field

from asro.settings import Settings
from asro.storage import SqliteRepository

_PLACEHOLDER_PREFIXES = (
    "review for debt, guarantees, capital expenditure",
    "review this filing for debt, guarantees, capital expenditure",
)
_HYPOTHETICAL_MARKERS = (
    "may adversely",
    "may not be able",
    "may not produce",
    "might adversely",
    "could adversely",
    "could result in",
    "would adversely",
    "we may incur",
)
_TRANSACTION_TYPES = {
    "ACQUIRES",
    "ALLOCATES_TO",
    "ASSUMES_DEBT",
    "CAPEX_COMMITMENT",
    "CANCELS_PROJECT",
    "COMPLETES_IPO",
    "ENTERS_INDEX",
    "FILES_FOR_IPO",
    "GUARANTEES",
    "IMPAIRMENT",
    "INVESTS_IN",
    "ISSUES_DEBT",
    "LEASES_FROM",
    "LENDS_TO",
    "PURCHASES_FROM",
    "REFINANCES",
    "SUPPLIES",
}


def preflight_reason(row: dict[str, Any]) -> str | None:
    """Return a conservative reason to quarantine clearly unsupported evidence."""
    evidence = re.sub(r"\s+", " ", str(row.get("evidence_text") or "")).strip().lower()
    if not evidence:
        return "The extracted event has no quoted source evidence."
    if evidence.startswith(_PLACEHOLDER_PREFIXES):
        return "The text is a filing-review placeholder, not evidence that an event occurred."
    amount = row.get("amount")
    if amount is not None and abs(float(amount)) > 5_000_000_000_000:
        return "The extracted amount exceeds the plausibility threshold and needs source checking."
    if str(row.get("event_type")) in _TRANSACTION_TYPES and any(
        marker in evidence for marker in _HYPOTHETICAL_MARKERS
    ):
        return "The excerpt is hypothetical risk language, not evidence of a completed event."
    return None


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str
    decision: Literal["confirm", "merge", "flag"]
    # Required by Structured Outputs; confirm/flag decisions return JSON null.
    canonical_fingerprint: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class ReviewBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: list[ReviewDecision]


class EvidenceReviewer:
    """Daily, auditable adjudication of provisional economic events."""

    def __init__(self, settings: Settings, repository: SqliteRepository | None = None) -> None:
        if not settings.openai_api_key:
            raise ValueError("ASRO_OPENAI_API_KEY is required for evidence review")
        self._settings = settings
        self._repository = repository or SqliteRepository(settings.database_path)

    def run(self, limit: int = 100, batch_size: int = 5) -> int:
        if limit < 1 or batch_size < 1:
            return 0
        reviewed = 0
        with self._repository.connect() as connection:
            preflight_rows = [
                dict(row) for row in self._repository.provisional_events(connection, limit)
            ]
            now = datetime.now(UTC).isoformat()
            for row in preflight_rows:
                reason = preflight_reason(row)
                if reason is None:
                    continue
                self._repository.apply_review(
                    connection,
                    str(row["fingerprint"]),
                    "flag",
                    None,
                    0.99,
                    reason,
                    "deterministic-preflight-v1",
                    now,
                )
                reviewed += 1
            connection.commit()
            while reviewed < limit:
                rows = [
                    dict(row)
                    for row in self._repository.provisional_events(
                        connection, min(batch_size, limit - reviewed)
                    )
                ]
                if not rows:
                    break
                applied = self._review_batch(connection, rows)
                if applied == 0:
                    raise ValueError("Reviewer returned no valid decisions")
                connection.commit()
                reviewed += applied
        return reviewed

    def _review_batch(self, connection: Any, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        batch = self._request(rows)
        allowed = {str(row["fingerprint"]) for row in rows}
        by_fingerprint = {decision.fingerprint: decision for decision in batch.decisions}
        for decision in batch.decisions:
            if decision.decision == "merge":
                target = by_fingerprint.get(str(decision.canonical_fingerprint))
                if target is None or target.decision != "confirm":
                    raise ValueError("Every merge target must be confirmed in the same review")
        seen: set[str] = set()
        now = datetime.now(UTC).isoformat()
        for decision in batch.decisions:
            if decision.fingerprint not in allowed or decision.fingerprint in seen:
                raise ValueError("Reviewer returned an unknown or repeated fingerprint")
            if decision.decision == "merge" and decision.canonical_fingerprint not in allowed:
                raise ValueError("Reviewer merge target must be in the reviewed batch")
            self._repository.apply_review(
                connection,
                decision.fingerprint,
                decision.decision,
                decision.canonical_fingerprint,
                decision.confidence,
                decision.reasoning,
                self._settings.review_model,
                now,
            )
            seen.add(decision.fingerprint)
        return len(seen)

    def _request(self, rows: list[dict[str, Any]]) -> ReviewBatch:
        schema = ReviewBatch.model_json_schema()
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {self._settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._settings.review_model,
                "store": False,
                "instructions": (
                    "Act as a skeptical financial-evidence editor. Confirm an event only when the "
                    "quoted source evidence actually states that the event occurred or reports a "
                    "specific measured result. A definite announcement that prices changed or will "
                    "change on a stated schedule is evidence of a confirmed pricing decision; "
                    "language such as considers, weighs, may, or could is not. Flag generic "
                    "risk-factor language, hypothetical or "
                    "forward-looking possibilities, unsupported entities, incorrect event types, "
                    "and amounts that are not clearly supported by the excerpt. Also detect "
                    "duplicate economic facts: merge only when two inputs describe the same "
                    "transaction and point to the strongest confirmed fingerprint in this batch. "
                    "Never infer or invent facts. Every input fingerprint must receive exactly one "
                    "decision. Keep reasoning specific and concise."
                ),
                "input": json.dumps(rows, default=str),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "asro_evidence_review",
                        "strict": True,
                        "schema": schema,
                    }
                },
            },
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        output_text = payload.get("output_text") or _output_text(payload)
        return ReviewBatch.model_validate_json(output_text)


def _output_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    return str(content["text"])
    raise ValueError("Reviewer response contained no structured output")
