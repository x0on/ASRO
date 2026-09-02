# News-first monitoring

The existing daily workflow collects, extracts, reviews, and rebuilds the site.
`site.build_static_site` now computes `news_alerts` from reviewed canonical events.
No new collector, model, database migration, or paid provider is required.

The headline counts pressure and public-exposure alerts within 90 days. These
are evidence counts, not severity scores. Revenue, cash flow and refinancing
reports remain separate context: their event types alone cannot establish a
positive trend or cancel pressure evidence.

Every alert includes a direct source URL, event time, review time, underlying
fact identity, causal category, deterministic rule version and explanation.
Missing URLs, unreviewed events and future/stale dates are excluded. Canonical
fact duplicates count once. Explicit same-issuer IPO filing headlines are also
grouped across the window, preserving all source URLs and underlying fact IDs.
Other cross-fact duplicates still depend on upstream canonicalization; this is
not a claim of perfect semantic deduplication. No stored evidence is deleted.

Changes in counts can reflect review, discovery or window expiry, not a change
in severity. The experimental numeric model and historical evidence are not
recalibrated by this feature. An IPO filing remains potential exposure, not
completed trading or demonstrated pension exposure.

Verification covers duplicate stability, IPO headline grouping, additive pressure
counts, separate context, source/review/date exclusions, and full browser-script
syntax. Preview against the preserved production database verifies the actual
alert path without changing the original evidence.
