from __future__ import annotations

import hashlib
import json
import tomllib
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from asro.evidence.time import normalize_timestamp


class EpisodeStratum(StrEnum):
    CRISIS = "crisis"
    BENIGN = "benign"
    CURRENT = "current"


class SourcePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_pattern: str
    tier: str
    required: bool = True


class ControlPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    series_id: str
    version: str
    unit: str
    required: bool = True
    provenance_schema: dict[str, str] = Field(
        default_factory=lambda: {
            "publisher": "required",
            "source_url": "required",
            "vintage": "required",
        }
    )


class FeatureRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_key: str
    feature_version: str
    required: bool = True
    minimum_reliability: float = Field(default=0.0, ge=0.0, le=1.0)


class CoverageGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_entity_month_feature_coverage: float = Field(default=1.0, ge=0.0, le=1.0)
    minimum_entity_source_coverage: float = Field(default=1.0, ge=0.0, le=1.0)
    minimum_control_month_coverage: float = Field(default=1.0, ge=0.0, le=1.0)


class EpisodeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: str
    version: str
    title: str
    stratum: EpisodeStratum
    period_start: date
    period_end: date
    availability_cutoff: datetime
    entities: list[str]
    controls: list[ControlPlan] = Field(default_factory=list)
    features: list[FeatureRequirement] = Field(default_factory=list)
    source_plan: list[SourcePlan]
    schema_version: str
    extractor_version: str
    feature_set_version: str
    coverage_gate: CoverageGate = Field(default_factory=CoverageGate)

    @model_validator(mode="before")
    @classmethod
    def normalize_cutoff(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if normalized.get("availability_cutoff") is not None:
            normalized["availability_cutoff"] = normalize_timestamp(
                normalized["availability_cutoff"]
            )
        return normalized

    @model_validator(mode="after")
    def validate_manifest(self) -> EpisodeManifest:
        if self.period_end < self.period_start:
            raise ValueError("episode period_end cannot precede period_start")
        if self.availability_cutoff.date() < self.period_end:
            raise ValueError("availability cutoff cannot precede the episode end")
        if not self.entities:
            raise ValueError("episode entities are required")
        if not self.source_plan:
            raise ValueError("episode source plan is required")
        if len(set(self.entities)) != len(self.entities):
            raise ValueError("episode entities must be unique")
        source_ids = [item.source_id for item in self.source_plan]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source plan IDs must be unique")
        control_ids = [(item.series_id, item.version) for item in self.controls]
        if len(set(control_ids)) != len(control_ids):
            raise ValueError("control series and versions must be unique")
        feature_ids = [(item.feature_key, item.feature_version) for item in self.features]
        if len(set(feature_ids)) != len(feature_ids):
            raise ValueError("feature requirements must be unique")
        return self

    @classmethod
    def from_toml(cls, path: Path) -> EpisodeManifest:
        with path.open("rb") as handle:
            return cls.model_validate(tomllib.load(handle))

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def checksum(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()
