"""Historical benchmark catalog, endpoint definitions and the calibration readiness gate."""

from asro.benchmark.catalog import (
    BENCHMARK_VARIABLES,
    CATALOG_VERSION,
    BenchmarkVariable,
    CausalRole,
    DerivedView,
    Direction,
    MeasurementScope,
    machine_derivable_variables,
    required_control_series,
    required_xbrl_concepts,
    variables_for_role,
)
from asro.benchmark.endpoints import (
    ASRO_HUNDRED,
    ASRO_ZERO,
    ENDPOINTS,
    EndpointCondition,
    EndpointDefinition,
)

__all__ = [
    "ASRO_HUNDRED",
    "ASRO_ZERO",
    "BENCHMARK_VARIABLES",
    "CATALOG_VERSION",
    "ENDPOINTS",
    "BenchmarkVariable",
    "CausalRole",
    "DerivedView",
    "Direction",
    "EndpointCondition",
    "EndpointDefinition",
    "MeasurementScope",
    "machine_derivable_variables",
    "required_control_series",
    "required_xbrl_concepts",
    "variables_for_role",
]

from asro.benchmark.readiness import (  # noqa: E402
    CalibrationClaimError,
    CalibrationReadiness,
    CalibrationRequirements,
    CalibrationVerdict,
    OutputTier,
    RoleCoverage,
    assert_claim_supported,
    episode_acceptances,
    evaluate_readiness,
    load_documented_insufficiency,
    observed_variable_keys,
    revised_only_control_series,
)

__all__ += [
    "CalibrationClaimError",
    "CalibrationReadiness",
    "CalibrationRequirements",
    "CalibrationVerdict",
    "OutputTier",
    "RoleCoverage",
    "assert_claim_supported",
    "episode_acceptances",
    "evaluate_readiness",
    "load_documented_insufficiency",
    "observed_variable_keys",
    "revised_only_control_series",
]
