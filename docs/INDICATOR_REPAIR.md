# Indicator repair: numeric-evidence-v2

## Verified starting evidence

Inspected immutable database `monitor-v1-e8b94dc438594b47.db.gz` from the
`asro-state` release, not the stale local working database.
Compressed SHA-256: `f5b9c45f742e563d5a97b65e50053d93d559f10a471aaa9e159522fbb99b2999`.
Database SHA-256: `e8b94dc438594b47c30bb76a47ca6e7b420ac9eedd887a86d9caf754f07907da`.
Both match the retained state pointer. Integrity and foreign-key checks pass when
run with the repository's registered SQLite functions.

171 snapshots, 12 daily endpoints: 51.4 to 44.6. External pressure contributes
-5.6225 points; capital -1.1888; circularity +0.2875; monetization -0.2875.
The rounded headline delta is -6.8. The former claim that expiring capital
evidence primarily caused the decline is withdrawn.

The directional/numeric threshold discontinuity exists in code. The old 61.1
reading matches the four-point directional estimator, while the final 16.12
numeric result reproduces against the artifact. The artifact alone does not
reconstruct the precise historical arrival order of every contributing fact.
All 279 currently confirmed legacy observations were rederived on September 1;
that proves current timestamp contamination, not the full absence of any past expiry.

Two confirmed transmission observations concern SpaceX. FILES_FOR_IPO had no
mapping. Three reviewed SB Energy reports misattribute the issuer to its backers.
The minimum-point count DOES come from the registry, not supplied observation fields.
The single-point transmission policy was explicit and remains a milestone policy.

## Decisions implemented

- Qualitative evidence remains available as direction and counts, never a fake
  numerical severity. Insufficient numeric support returns null. No estimator switch.
- Severity is explicitly 0–5; values outside it are rejected on new observations
  and excluded on legacy reads. Signal units are not numeric severity. Percentage
  inputs require 0–100; missing values do not become zero.
- Numeric aggregates divide by the sum of confidence-adjusted registry weights.
  Lower uniform confidence no longer scales a severity toward safety. These weights
  express relative evidential influence, not statistical uncertainty intervals.
- Transmission milestones use maximum observed stage, not an issuer average.
  Filing = 0.5, public trading = 1, index inclusion = 2 on the existing 0–5 scale.
  An earlier-stage new issuer cannot dilute established reach. Stage is NOT the
  amount of public exposure; no pension holdings are inferred from a filing.
- FILES_FOR_IPO maps only when the evidence identifies the source entity as the
  filing subject. Backer/issuer ambiguity stays visible as unresolved in alerts
  and does not become an issuer measurement. This conservative guard may miss
  alternate wording; it does not auto-confirm new sources or alter review records.
- Date selection uses effective event time when present, parsing RFC-2822 and ISO
  to UTC, with observation time still checked for availability. Invalid/future dates
  are excluded. Legacy rows lacking event time retain an explicit compatibility
  fallback to observation time. This is not full point-in-time historical replay:
  the mutable legacy table still lacks historical review-state reconstruction.
- Reviewed listing/index reports have independent source-linked alerts. Missing
  alerts are not evidence of safety. IPO filings and completed listings are distinct.
- Migration 19 tags old snapshots legacy-v1 and new snapshots numeric-evidence-v2.
  Old scores and V2 evidence are not rewritten. Comparisons stop at method or
  numeric-coverage changes. The UI says indicator change, not risk direction.
- Historical benchmark readiness is separate from validation of the revised
  indicator; the public banner explicitly says the revised indicator is not
  historically validated. Existing readiness gates are not weakened.

## Verification and limitations

Regression tests cover the scale boundary, nulls, threshold transition, confidence
scaling, event-time staleness, bad dates, filing mapping, issuer ambiguity, milestone
monotonicity, reviewed-only alerts, snapshot versioning and coverage boundaries.
Existing tests asserting numeric directional estimates were updated to expect null.

Dry runs use a COPY of the verified artifact; no live evidence is edited manually.
The repaired artifact has only two numerically supported risk dimensions and thus
withholds a composite score. Directional evidence and listing alerts remain visible.
This is not a claim that exposure disappeared or that the system is now calibrated.

Remaining work: review/acquire missing primary issuer evidence (including Anthropic
where absent from accepted events), correct ambiguous issuer lineage through the
review process, complete live V2/scoring lineage integration and replay validation,
and independently validate the revised indicator against accepted benchmark episodes.
New market-data providers are not dependencies of this repair.

Rollback: revert the calculation/UI commit if necessary, but keep migration 19 and
historical snapshots. Never relabel old scores as the new version or weaken a
readiness gate to restore a headline number.
