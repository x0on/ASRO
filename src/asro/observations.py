from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator


class Observation(BaseModel):
    observation_id: str
    event_id: str
    variable_key: str
    entity: str | None = None
    value: float
    unit: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    effective_date: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source_document_id: str
    evidence_text: str
    extractor: str
    polarity: str

    @model_validator(mode="after")
    def validate_severity_scale(self) -> Observation:
        if self.unit == "score" and not 0 <= self.value <= 5:
            raise ValueError("score severity must be within the declared 0–5 scale")
        return self
