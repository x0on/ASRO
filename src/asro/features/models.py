from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Aggregation(StrEnum):
    SUM = "sum"
    MEAN = "mean"
    LATEST = "latest"


class MissingnessReason(StrEnum):
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    NOT_YET_PUBLISHED = "not_yet_published"
    COLLECTION_FAILED = "collection_failed"
    DISPUTED = "disputed"


class FeatureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_key: str
    feature_version: str
    aggregation: Aggregation
    unit: str
    expected_facts_per_period: int = Field(ge=1)


class EcosystemFeatureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_feature_key: str
    source_feature_version: str
    feature_key: str
    feature_version: str
    aggregation: Aggregation
    unit: str


class EcosystemFeatureValue(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    ecosystem_feature_value_id: str
    build_id: str
    period_start: str
    period_end: str
    source_feature_key: str
    source_feature_version: str
    feature_key: str
    feature_version: str
    value_numeric: float | None = None
    missingness_reason: MissingnessReason | None = None
    coverage: float = Field(ge=0.0, le=1.0)
    reliability: float = Field(ge=0.0, le=1.0)
    source_feature_value_ids: list[str]
    fact_lineage: list[FactLineage]

    @model_validator(mode="after")
    def validate_ecosystem_value(self) -> EcosystemFeatureValue:
        if (self.value_numeric is None) == (self.missingness_reason is None):
            raise ValueError("exactly one of value or missingness reason is required")
        if self.value_numeric is not None and not self.fact_lineage:
            raise ValueError("numeric ecosystem values require facts")
        if self.value_numeric is None and self.fact_lineage:
            raise ValueError("missing ecosystem values cannot claim facts")
        if len(set(self.source_feature_value_ids)) != len(self.source_feature_value_ids):
            raise ValueError("entity feature contributors must be unique")
        return self


class FactLineage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_fact_id: str
    canonical_assignment_id: str
    representative_observation_id: str
    contributor_assignments: dict[str, str]

    @model_validator(mode="after")
    def validate_lineage(self) -> FactLineage:
        if not self.contributor_assignments:
            raise ValueError("fact lineage requires contributors")
        if self.representative_observation_id not in self.contributor_assignments:
            raise ValueError("representative must be a contributor")
        return self


class FeatureValue(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    feature_value_id: str
    build_id: str
    entity_id: str
    period_start: str
    period_end: str
    feature_key: str
    feature_version: str
    value_numeric: float | None = None
    missingness_reason: MissingnessReason | None = None
    coverage: float = Field(ge=0.0, le=1.0)
    reliability: float = Field(ge=0.0, le=1.0)
    fact_lineage: list[FactLineage]

    @model_validator(mode="after")
    def validate_value(self) -> FeatureValue:
        if (self.value_numeric is None) == (self.missingness_reason is None):
            raise ValueError("exactly one of value or missingness reason is required")
        if self.value_numeric is not None and not self.fact_lineage:
            raise ValueError("numeric feature values require economic facts")
        if self.value_numeric is None and self.fact_lineage:
            raise ValueError("missing feature values cannot claim economic facts")
        fact_ids = [item.canonical_fact_id for item in self.fact_lineage]
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("canonical facts must be unique within a feature value")
        return self
