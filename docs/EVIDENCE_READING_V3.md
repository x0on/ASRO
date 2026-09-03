# Reviewed-evidence reading v3

This replaces the mixed raw-measurement/directional headline and the temporary
article-count headline. It is an explicit deterministic heuristic, not a fitted
or historically validated risk scale. Historical benchmark readiness is unchanged.

For each variable/entity/polarity, retain the strongest reviewed, source-linked
support in the last 90 days. Risk and counter-evidence remain separate. Support is
confidence times registered variable weight, multiplied by severity/5 for score-unit
events. Dollar values remain visible as evidence; no fabricated dollar value is
created for qualitative news. Their magnitude is not used as a severity scale.
Entity aliases are normalized. If extraction omitted the entity, a single company
recorded on the source document can resolve it; ambiguous attribution is excluded.
Multiple excerpts from one company's filing therefore cannot manufacture extra
independent entities. The original observations are not rewritten.
Credit-facility and letter-of-credit excerpts are excluded from the debt reading
until facility capacity, drawn borrowing, and contingent exposure are separately
extracted. Their first dollar amount must not be presented as outstanding debt;
the original events and provenance remain available for review.

With P = total pressure support and C = counter-support, both the headline and
each category use `50 + 50*(P-C)/(5+P+C)`. Five is an explicit neutral prior, not
an empirically calibrated parameter. The full 0–100 range is asymptotic; 50 means
balanced support, not a statement about the economy being safe. This expands the
earlier directional estimator to one consistent full-scale rule. There is no
fifth-point estimator switch. Confidence affects strength of evidence, rather
than multiplying a raw risk score toward zero.

No evidence returns unknown, never zero. The headline still requires three risk
categories. Unknown category cards are omitted; they do not imply safety. Adding
positive support cannot lower the score, including when a new category appears.
Weak duplicate reporting cannot replace stronger support. Counter-evidence can
lower it. Negative cash flow is pressure even if an old derived row marked it
as safety. Qualitative safety reports without measurements are not reassuring votes.

The exact selected source records, polarity and support are exposed through each
category's source panel. The JSON also exposes aggregate support totals. These
are audit inputs, not proof of economic validity. Coverage, correlated evidence,
the fixed prior, and event classification remain model limitations. Maximum
support is retained only within the window; expiry or a reviewed correction can
change a reading. Upstream canonical review determines which facts remain eligible.

Collection and publication call the same function. `asro capture-reading` runs
after review and vintage acquisition, before immutable packaging. Site generation
uses that recorded cutoff. Existing snapshots are preserved under their old
method IDs; no cross-method trend is shown. A pending comparison is not displayed
as a large empty dashboard tile. Benchmark datasets and V2 evidence are unchanged.

Tests cover duplicate invariance, stronger severity, fifth-point continuity,
new-category monotonicity, genuine counter-evidence, negative cash flow, unknowns,
date/source restrictions, post-review capture, and publication cutoff equality.
