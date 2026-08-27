from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from asro.evidence.time import TimePrecision, infer_time_precision, normalize_timestamp


class FactStatus(StrEnum):
    DIRECT = "direct"
    INFERRED = "inferred"
    ESTIMATED = "estimated"
    DISPUTED = "disputed"


class SourceTier(StrEnum):
    PRIMARY = "primary"
    AUTHORITATIVE_SECONDARY = "authoritative_secondary"
    REPUTABLE_SECONDARY = "reputable_secondary"
    OTHER = "other"


class EconomicScope(StrEnum):
    ENTITY = "entity"
    ECOSYSTEM = "ecosystem"
    NETWORK = "network"
    MARKET = "market"


class ObservationV2(BaseModel):
    """Append-only evidence record with explicit knowledge and provenance times."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    observation_id: str
    supersedes_observation_id: str | None = None
    event_id: str
    source_document_id: str
    source_locator: str
    evidence_text: str
    entity_id: str | None = None
    counterparty_entity_id: str | None = None
    entity_role: str | None = None
    feature_key: str
    feature_version: str
    value_numeric: float | None = None
    value_text: str | None = None
    unit: str | None = None
    currency: str | None = None
    denominator_feature_key: str | None = None
    economic_scope: EconomicScope | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    event_at: datetime | None = None
    event_time_precision: TimePrecision | None = None
    published_at: datetime
    published_time_precision: TimePrecision
    availability_at: datetime
    availability_time_precision: TimePrecision
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    fact_status: FactStatus
    source_tier: SourceTier
    source_quality: float = Field(ge=0.0, le=1.0)
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    review_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    extractor_name: str
    extractor_version: str
    review_id: int | None = None
    derivation_method: str | None = None
    derivation_inputs: list[str] = Field(default_factory=list)
    estimation_model: str | None = None
    dispute_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_times(cls, data: object, info: ValidationInfo) -> object:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if isinstance(normalized.get("derivation_inputs"), str):
            normalized["derivation_inputs"] = json.loads(normalized["derivation_inputs"])
        precision_pairs = {
            "event_at": "event_time_precision",
            "published_at": "published_time_precision",
            "availability_at": "availability_time_precision",
        }
        for time_name, precision_name in precision_pairs.items():
            time_value = normalized.get(time_name)
            supplied_precision = normalized.get(precision_name)
            if time_value is None:
                if supplied_precision is not None:
                    raise ValueError(f"{precision_name} requires {time_name}")
                continue
            actual_precision = infer_time_precision(time_value)
            from_storage = bool(info.context and info.context.get("from_storage"))
            if (
                supplied_precision is not None
                and supplied_precision != actual_precision
                and not from_storage
            ):
                raise ValueError(f"{precision_name} does not match {time_name} input precision")
            normalized[precision_name] = supplied_precision or actual_precision
        for name in (
            "period_start",
            "period_end",
            "event_at",
            "published_at",
            "availability_at",
            "extracted_at",
        ):
            if normalized.get(name) is not None:
                normalized[name] = normalize_timestamp(normalized[name])
        return normalized

    @model_validator(mode="after")
    def validate_semantics(self) -> ObservationV2:
        if (self.value_numeric is None) == (not self.value_text):
            raise ValueError("an observation requires exactly one of value_numeric or value_text")
        if self.value_text is not None and not self.value_text.strip():
            raise ValueError("value_text cannot be blank")
        if self.value_numeric is not None and not self.unit:
            raise ValueError("numeric observations require a unit")
        if self.value_numeric is not None and (
            self.period_start is None or self.period_end is None or self.economic_scope is None
        ):
            raise ValueError("numeric observations require a period and economic scope")
        if (self.currency is not None) != (self.unit == "currency"):
            raise ValueError("currency and unit='currency' must appear together")
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("period_end cannot precede period_start")
        if self.availability_at < self.published_at:
            raise ValueError("availability_at cannot precede published_at")
        if self.extracted_at < self.availability_at:
            raise ValueError("extracted_at cannot precede availability_at")
        if not self.source_locator.strip() or not self.evidence_text.strip():
            raise ValueError("source locator and evidence text are required")
        if self.fact_status in {
            FactStatus.INFERRED,
            FactStatus.ESTIMATED,
        } and (not self.derivation_method or not self.derivation_inputs):
            raise ValueError("inferred/estimated facts require method and derivation inputs")
        if self.fact_status is FactStatus.ESTIMATED and not self.estimation_model:
            raise ValueError("estimated facts require an estimation model")
        if self.fact_status is FactStatus.DISPUTED and not self.dispute_reason:
            raise ValueError("disputed facts require a dispute reason")
        return self


class FeatureDefinitionV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_key: str
    feature_version: str
    definition_json: str
    released_at: datetime
    deprecated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_times(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        for name in ("released_at", "deprecated_at"):
            if normalized.get(name) is not None:
                normalized[name] = normalize_timestamp(normalized[name])
        return normalized

    @model_validator(mode="after")
    def validate_dates(self) -> FeatureDefinitionV2:
        try:
            definition = json.loads(self.definition_json)
        except json.JSONDecodeError as exc:
            raise ValueError("definition_json must be valid JSON") from exc
        if not isinstance(definition, dict):
            raise ValueError("definition_json must contain a JSON object")
        if self.deprecated_at and self.deprecated_at < self.released_at:
            raise ValueError("deprecated_at cannot precede released_at")
        return self


class CanonicalFactAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str
    event_id: str
    canonical_fact_id: str
    available_at: datetime
    supersedes_assignment_id: str | None = None
    reviewer_id: int | None = None
    assigned_by: str
    assignment_method: str
    provenance: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="before")
    @classmethod
    def normalize_assignment_times(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        for field in ("available_at", "created_at"):
            if normalized.get(field) is not None:
                normalized[field] = normalize_timestamp(normalized[field])
        return normalized

    @model_validator(mode="after")
    def validate_assignment_times(self) -> CanonicalFactAssignment:
        if self.created_at < self.available_at:
            raise ValueError("created_at cannot precede available_at")
        return self
