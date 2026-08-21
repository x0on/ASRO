from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class Dimension(StrEnum):
    CAPITAL = "capital"
    CIRCULARITY = "circularity"
    MONETIZATION = "monetization"
    CANNIBALIZATION = "cannibalization"
    FRAGILITY = "fragility"
    TRANSMISSION = "transmission"
    STRESS = "stress"
    EXTERNAL_PRESSURE = "external_pressure"
    COUNTER_EVIDENCE = "counter_evidence"


class VariableDefinition(BaseModel):
    key: str
    label: str
    dimension: Dimension
    description: str
    unit: str | None = None
    direction: str
    weight: float = 1.0
    minimum_points: int = 5
    evidence_basis: str = "aggregate"
