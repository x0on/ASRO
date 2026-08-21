# ASRO Data Dictionary v1.0

## Purpose

ASRO is an early-warning observatory, not a crash-probability machine. Its job is to detect whether the financial conditions required by the monitored AI-systemic-risk hypothesis are **forming, accelerating, propagating, interacting, or receding**.

The model follows a vulnerability-first principle: shocks are difficult to predict; vulnerabilities can be observed as they build or recede. ASRO extends conventional financial-stability monitoring with AI-specific measures of circularity, monetization, technological obsolescence, public-market transmission, and external competitive pressure.

## Evidence model

Every observation must retain: `observation_id`, `variable_id`, `entity_id`, `as_of_date`, `value`, `unit`, `source_url`, `source_type`, `source_document_id`, `evidence_text`, `reported_or_derived`, `confidence`, `freshness`, and `method_version`.

Unknown values remain **unknown**. Missing data must never be silently converted to zero.

### Source confidence hierarchy

| Tier | Typical evidence | Base confidence |
|---|---|---:|
| A | Regulatory filing / government dataset / audited financial statement | 0.95–1.00 |
| B | Official company IR release, fund holding file, exchange/index notice | 0.85–0.95 |
| C | High-quality financial reporting citing identifiable documents/people | 0.65–0.85 |
| D | Analyst estimate / secondary aggregation with disclosed method | 0.45–0.70 |
| E | Social post, rumor, unattributed claim | 0.10–0.40; discovery only |

ASRO may use lower-tier evidence to discover an event, but high-impact state changes should seek corroboration from independent higher-tier evidence.

## Nine dimensions

### D1 — Capital Build-up
**Question:** How rapidly are financial claims and physical commitments to AI growing?

| ID | Variable | Unit | Preferred source | Refresh | Direction |
|---|---|---|---|---|---|
| CAP_01 | AI capex announced | USD | 10-K/10-Q/8-K, IR | daily/quarterly | ↑ risk pressure |
| CAP_02 | AI capex actually spent | USD | cash-flow statements, filings | quarterly | ↑ |
| CAP_03 | AI infrastructure contractual commitments | USD | filings/contracts/IR | daily | ↑ |
| CAP_04 | AI-linked equity financing | USD | filings, official financing releases | daily | ↑ |
| CAP_05 | AI-linked debt issuance | USD | filings, prospectuses, ratings | daily | ↑ |
| CAP_06 | AI-linked lease obligations | USD | filings | quarterly | ↑ |
| CAP_07 | AI power/data-center commitments | MW / USD | utility/regulatory/company sources | weekly | ↑ |
| CAP_08 | capital growth rate | % YoY/QoQ | derived | each update | acceleration ↑ |

**Derived centerpiece:** `financial_claims_growth / external_AI_cash_growth`.

### D2 — Circularity & Dependency
**Question:** Is reported growth increasingly financed or purchased by the same small network?

| ID | Variable | Unit | Source | Refresh | Direction |
|---|---|---|---|---|---|
| CIR_01 | reciprocal capital/customer relationships | count/USD | graph-derived from evidence | daily | ↑ |
| CIR_02 | vendor-financed purchases | USD | filings/contracts/reporting | daily | ↑ |
| CIR_03 | guarantees / backstops | USD | filings/contracts | daily | ↑ |
| CIR_04 | customer concentration | % revenue | filings | quarterly | ↑ |
| CIR_05 | supplier concentration | % spend / qualitative | filings | quarterly | ↑ |
| CIR_06 | network centralization | 0–1 | graph-derived | daily | ↑ |
| CIR_07 | weighted closed-loop capital paths | index | graph-derived | daily | ↑ |
| CIR_08 | single-node dependency | % network exposure | graph stress test | daily | ↑ |

A repeated article about one transaction counts once. Events are deduplicated by transaction/evidence identity before graph scoring.

### D3 — Monetization & Economic Carrying Capacity
**Question:** Is genuine external demand producing enough cash to support the claims being built on AI?

| ID | Variable | Unit | Source | Refresh | Direction |
|---|---|---|---|---|---|
| MON_01 | external AI revenue | USD | filings/IR + carefully labeled private estimates | quarterly | ↑ is counter-risk |
| MON_02 | AI free cash flow | USD | filings / derived | quarterly | ↑ counter-risk |
| MON_03 | AI gross margin | % | filings/IR/derived | quarterly | ↑ counter-risk |
| MON_04 | inference/training unit price | USD/token or workload | official pricing | weekly | contextual |
| MON_05 | compute utilization | % | company/infra disclosures | quarterly | ↑ counter-risk |
| MON_06 | backlog conversion | % / USD | filings | quarterly | ↑ counter-risk |
| MON_07 | external cash / AI commitments | ratio | derived | quarterly | ↓ risk |
| MON_08 | revenue growth vs obligation growth | spread | derived | quarterly | negative spread ↑ risk |

**Critical rule:** intra-ecosystem payments are tagged separately from cash originating from end customers outside the monitored AI financing network.

### D4 — Cannibalization & Real-Economy Displacement
**Question:** Is AI destroying income/cash flow elsewhere faster than it creates sustainable new economic value?

| ID | Variable | Unit | Source | Refresh | Direction |
|---|---|---|---|---|---|
| CAN_01 | revenue compression in exposed sectors | % | filings/economic data | quarterly | ↑ |
| CAN_02 | pricing compression in software/services | % | filings/pricing | monthly | ↑ |
| CAN_03 | AI-attributed workforce reductions | jobs | WARN/company releases/reporting | weekly | ↑ |
| CAN_04 | employment trend in exposed occupations | jobs/% | BLS | monthly | ↓ employment = pressure |
| CAN_05 | wage trend in exposed occupations | % | BLS | monthly/quarterly | ↓ pressure |
| CAN_06 | enterprise budget substitution | %/USD | filings/surveys, lower confidence | quarterly | ↑ |
| CAN_07 | displaced-value / new-external-value ratio | ratio | derived | quarterly | ↑ |

Causality labels are mandatory: `observed`, `company-attributed`, `model-inferred`, or `unknown`.

### D5 — Leverage & Fragility
**Question:** Can the system absorb disappointment without forced deleveraging?

| ID | Variable | Unit | Source | Refresh | Direction |
|---|---|---|---|---|---|
| LEV_01 | debt / EBITDA or cash flow | ratio | filings | quarterly | ↑ |
| LEV_02 | interest coverage | ratio | filings | quarterly | ↓ |
| LEV_03 | floating-rate debt share | % | filings/credit docs | quarterly | ↑ |
| LEV_04 | debt maturity wall | USD by year | filings/prospectuses | quarterly | near-term ↑ |
| LEV_05 | refinancing requirement | USD | derived | monthly | ↑ |
| LEV_06 | private-credit dependence | USD/% | filings/BDC/lender disclosures | quarterly | ↑ |
| LEV_07 | collateral concentration in AI assets | USD/% | credit docs | quarterly | ↑ |
| LEV_08 | covenant headroom | ratio/% | credit docs where public | quarterly | ↓ |
| LEV_09 | liquidity runway | months | filings/derived | quarterly | ↓ |
| LEV_10 | asset-life / financing-life mismatch | months/ratio | derived | quarterly | ↑ |

### D6 — Transmission to the Broader Financial System
**Question:** Who ultimately owns the risk, and is it migrating toward ordinary savers?

| ID | Variable | Unit | Source | Refresh | Direction |
|---|---|---|---|---|---|
| TRN_01 | mutual-fund AI exposure | % NAV/USD | N-PORT/fund files | monthly/quarterly | ↑ |
| TRN_02 | ETF AI exposure | % NAV/USD | official holdings | daily/monthly | ↑ |
| TRN_03 | target-date fund exposure | % NAV/USD | holdings/look-through | monthly/quarterly | ↑ |
| TRN_04 | pension direct exposure | USD/% | annual reports/5500/public pensions | quarterly/annual | ↑ |
| TRN_05 | pension indirect exposure | USD/% | look-through derived | quarterly | ↑ |
| TRN_06 | insurer exposure | USD/% assets | statutory/public disclosures | quarterly | ↑ |
| TRN_07 | bank exposure | USD/% capital | filings/regulatory data | quarterly | ↑ |
| TRN_08 | BDC/private-credit exposure | USD/% NAV | filings | quarterly | ↑ |
| TRN_09 | index weight of monitored AI complex | % | index provider + constituents | event/daily | ↑ |
| TRN_10 | household retirement look-through | estimated % | derived, confidence-labeled | quarterly | ↑ |

**Important:** 13F measures institutional manager positions, not household retirement ownership. ASRO must not equate the two.

Public-market transmission is a staged pathway, not a binary 0-or-100 result:

1. **Public trading begins (~20):** exposure is available to investors who deliberately buy it.
2. **Major-index entry (~40):** passive index products begin distributing exposure more broadly.
3. **Material passive weight (~60):** measured index and fund weights show meaningful exposure across multiple products.
4. **Retirement exposure (~80):** filings or holdings document material pension, target-date, or retirement-fund exposure.
5. **Broad transmission (~100):** exposure is both widespread and concentrated enough to transmit company-specific losses across ordinary portfolios.

One company entering one major index cannot by itself justify a near-100 transmission score. Later stages require measured index weight and documented fund, pension, or retirement exposure.

### D7 — Market & Funding Stress
**Question:** Are cracks appearing now?

| ID | Variable | Unit | Source | Refresh | Direction |
|---|---|---|---|---|---|
| STR_01 | corporate spread / OAS | bps | public market series | daily | ↑ |
| STR_02 | issuer spread change | bps | market/rating sources | daily | ↑ |
| STR_03 | rating downgrade / negative outlook | event | ratings/company filing | daily | ↑ |
| STR_04 | failed/delayed refinancing | event/USD | filings/reporting | daily | ↑ |
| STR_05 | canceled/deferred AI capex | USD/event | filings/IR | daily | ↑ |
| STR_06 | BDC redemption pressure/gates | %/event | filings | monthly/quarterly | ↑ |
| STR_07 | covenant amendment/distress exchange | event | filings/credit docs | daily | ↑ |
| STR_08 | asset write-down / impairment | USD | filings | quarterly | ↑ |
| STR_09 | liquidity draw / revolver usage | USD | filings | quarterly | ↑ |
| STR_10 | equity drawdown + volatility regime | % | market data | daily | contextual amplifier |

### D8 — External Competitive & Technological Pressure
**Question:** Could external innovation or geopolitics undermine the economics of existing AI commitments?

| ID | Variable | Unit | Source | Refresh | Direction |
|---|---|---|---|---|---|
| EXT_01 | frontier capability gap, U.S. vs China/other | normalized benchmark gap | benchmark suites/research | per release | shrinking gap ↑ pressure |
| EXT_02 | price/performance frontier | capability per dollar | official prices + benchmarks | per release | cheaper rival ↑ |
| EXT_03 | open-weight capability gap | normalized gap | benchmarks/model releases | per release | shrinking gap ↑ |
| EXT_04 | frontier release cadence | days | model release records | monthly | faster ↑ |
| EXT_05 | compute efficiency improvement | capability/compute | technical reports | per release | rapid improvement can impair old assets |
| EXT_06 | enterprise switching/adoption evidence | count/% | company/customer disclosures | monthly | rival gains ↑ |
| EXT_07 | chip/export-control constraint | event/index | U.S./foreign government sources | event-driven | contextual |
| EXT_08 | foreign state support/subsidy | USD/policy | government sources | event-driven | ↑ competitive pressure |
| EXT_09 | architecture obsolescence shock | event + estimated affected capital | technical + financial mapping | event-driven | ↑ |
| EXT_10 | model commoditization rate | price/capability trend | derived | monthly | ↑ pressure on margins |

ASRO should not treat nationality as risk. It measures **competitive and technological pressure**, regardless of country of origin.

### D9 — Counter-Evidence / Resilience
**Question:** What evidence would falsify or weaken the hypothesis?

| ID | Variable | Unit | Source | Refresh | Effect |
|---|---|---|---|---|---|
| CNT_01 | sustained AI free-cash-flow growth | USD/% | filings | quarterly | cool |
| CNT_02 | leverage reduction | ratio/USD | filings | quarterly | cool |
| CNT_03 | external cash growing faster than claims | ratio | derived | quarterly | cool |
| CNT_04 | diversification of customers/suppliers | concentration | derived | quarterly | cool |
| CNT_05 | falling guarantees/vendor financing | USD | filings | quarterly | cool |
| CNT_06 | low retirement transmission after public listing | % | holdings/look-through | quarterly | cool |
| CNT_07 | successful refinancing on improving terms | bps/USD | filings/market | event | cool |
| CNT_08 | productivity/wage gains offset displacement | % | BLS/economic data | monthly/quarterly | cool |
| CNT_09 | efficiency gains improve margins rather than strand assets | % | filings/derived | quarterly | cool |
| CNT_10 | stress event absorbed without propagation | event | graph + market evidence | event | cool |

Counter-evidence is first-class data. It cannot be manually suppressed because it conflicts with the thesis.

## Cross-dimensional derived indicators

These are the variables the homepage eventually compresses into the nebula.

1. **Claims-to-External-Cash Divergence (CECD)** — growth in AI-linked financial claims minus growth in genuine external AI cash generation.
2. **Circular Capital Dependence (CCD)** — financial weight of reciprocal/cyclic paths relative to total monitored capital flows.
3. **Network Concentration & Single-Point Dependency (NCSD)** — graph centralization plus simulated loss of top nodes.
4. **Refinancing Fragility (RF)** — near-term maturities × funding cost × weak coverage × private-credit dependence.
5. **Household Transmission Index (HTI)** — look-through exposure through mutual funds, ETFs, target-date funds, pensions and insurers, with confidence bands.
6. **Technological Obsolescence Pressure (TOP)** — speed of price/performance improvement × capital exposed to older compute/model economics.
7. **External Competitive Pressure (ECP)** — capability convergence × price disadvantage × adoption shift × release cadence.
8. **Stress Propagation Index (SPI)** — simultaneous stress events weighted by network centrality and affected capital.
9. **Resilience / Counter-Thesis Index (RCI)** — cash generation, deleveraging, diversification, successful refinancing and shock absorption.

## Interaction triggers

No single indicator may put ASRO into the highest convergence state. High-convergence requires independent evidence across at least three dimensions, including one from **fragility/stress/transmission** and one from **monetization/capital/circularity**.

Examples of interaction patterns worth escalating:

- capital ↑ + external cash ratio ↓ + circularity ↑
- leverage ↑ + refinancing wall approaching + spreads ↑
- public index weight ↑ + retirement look-through ↑ + valuation pressure ↑
- foreign/open-weight price-performance ↑ + model pricing ↓ + committed capex ↑
- compute efficiency shock ↑ + asset-life mismatch ↑ + debt collateralized by affected infrastructure ↑
- layoffs/cannibalization ↑ + household income weakness ↑ + retirement exposure ↑

## Scoring principles

A new observation's contribution should approximate:

`impact = magnitude × systemic_relevance × novelty × confidence × network_centrality × acceleration`

Then adjust for:

`duplication`, `uncertainty`, `staleness`, and `counter_evidence`.

The final convergence state is **not** the sum of article counts and is **not** a probability of economic collapse.

## Collection priority

### P0 — Build now
- SEC 10-K, 10-Q, 8-K, S-1 and XBRL facts
- debt/guarantee/lease/capex extraction
- company/customer/supplier relationships
- IPO and index events
- fund holdings where freely available
- official model pricing and release records
- counter-evidence extraction

### P1 — Add next
- N-PORT fund holdings and look-through
- Form 5500/public pension disclosures
- BDC/private-credit filings
- ratings/refinancing events
- labor-market proxies
- benchmark + price/performance frontier tracking

### P2 — Research-intensive
- insurer look-through
- bank AI-specific credit exposure
- private-company economics
- GPU collateral values
- compute utilization
- enterprise switching
- causal cannibalization estimates

## Refresh classes

- **Realtime/event:** SEC filings, IPO/index notices, financing, rating actions, major model releases.
- **Daily:** market stress, official holdings when published, pricing pages.
- **Weekly:** infrastructure/project announcements, competitive frontier synthesis.
- **Monthly:** labor, price/performance trends, selected holdings.
- **Quarterly:** financial statements, leverage, cash flow, concentration, BDC/private credit.
- **Annual/quarterly:** pensions and some retirement look-through data.

“Real time” in ASRO means **near-source-time for event data** and **latest available observation for slow-reporting fundamentals**. The UI must expose the age of each variable.

## Data quality guardrails

1. Preserve raw evidence and provenance.
2. Deduplicate transactions across publishers.
3. Separate reported facts from derived estimates.
4. Store confidence and freshness independently.
5. Never infer zero from missing data.
6. Require independent corroboration for large private-company claims when possible.
7. Maintain entity aliases and parent/subsidiary relationships.
8. Version every derivation/scoring formula.
9. Recompute historical scores when methodology changes, without rewriting raw observations.
10. Publish methodology and counter-evidence alongside risk evidence.

## Minimum viable evidence set for a serious warning

A high-level warning should require:

- sufficient source health/freshness;
- multiple independent entities, not one company;
- at least three active dimensions;
- at least one transmission/fragility/stress dimension;
- no unresolved high-confidence counter-evidence that materially reverses the signal;
- transparent links to the observations that caused the state change.

This is the core contract between ASRO's backend and the living nebula.
