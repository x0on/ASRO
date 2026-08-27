# AI capital feature family

These additive features separate monthly flows from point-in-time exposures. They do not
change `ai_related_debt@1.0.0`, whose existing meaning remains debt issuance observed in
the event month.

## Semantics

| Feature | Grain | Unit | Aggregation | Carry-forward |
|---|---|---|---|---|
| `ai_related_debt@1.0.0` | entity-month flow | reported currency | distinct canonical-fact sum | never |
| `ai_compute_contract_value_flow@1.0.0` | entity-month disclosure flow | reported currency | distinct newly disclosed total-contract facts | never |
| `ai_infrastructure_debt_stock@1.0.0` | entity-month as-of | reported currency | latest single canonical point fact | only through the registered `max_age_months` |
| `ai_compute_commitment_stock@1.0.0` | entity-month as-of | reported currency | latest disclosed remaining obligation | only through the registered `max_age_months` |
| `ai_contingent_credit_support_stock@1.0.0` | entity-month as-of | reported currency | latest disclosed total guarantee/backstop | only through the registered `max_age_months` |
| `ai_infrastructure_capex_flow@1.0.0` | entity-month flow | reported currency | distinct canonical-fact sum | never |

The stock and commitment definitions are registered only when supporting evidence exists.
An announcement of a contract's total value is not treated as its remaining obligation.
Guidance is not actual capex. An incremental guarantee and a disclosed total guarantee are
not summed. Values in different currencies are never aggregated without a separately
versioned conversion rule.

## Temporal and missingness rules

- A monthly cell may use only evidence publicly available by that month's end, even when
  the overall build cutoff is later.
- Point observations may carry forward only for their registered maximum age. After that
  bound, the cell is explicitly `unknown`; the last value is not silently extended.
- A numeric point cell must resolve to exactly one active canonical fact. Multiple facts are
  rejected instead of summed, preventing stock/flow and component/total double counting.
- Silence, a missing filing, or a later filing does not establish zero. Unsupported cells
  remain `unknown`.
- Counter-evidence is retained as evidence and review provenance; it does not create a
  numeric value unless it directly satisfies the registered definition.
