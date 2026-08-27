# Negative-evidence protocol for `ai_related_debt@1.0.0`

## Purpose

A numeric zero means that a bounded, authoritative search found no qualifying AI-related debt flow
for one entity and month. Silence in one filing, a missing candidate lead, or absence from news is
not evidence of zero. Until every requirement below is satisfied, the feature cell remains explicitly
missing.

## Qualifying feature event

The current feature accepts a debt issuance or assumption by the named entity only when a primary
source directly connects that financing to AI infrastructure, AI capacity, or an identified AI
investment. General-corporate-purpose notes, period-end debt balances, capex guidance, operating
leases, compute-service purchase commitments, and another party's project debt do not qualify.

Contingent guarantees and supplier financing are economically relevant but require separate,
versioned features; they must not be folded into `ai_related_debt@1.0.0`.

## Required search universe for a zero

For the entity-month, the reviewer must freeze and hash:

1. The issuer's complete SEC submissions index available by the episode cutoff.
2. Every filing accepted during the month with form `8-K`, `10-Q`, `10-K`, `424B2`, `424B3`,
   `424B5`, `FWP`, `S-3`, or `S-3ASR`, including all debt-related exhibits.
3. The first subsequent `10-Q` or `10-K` available by the cutoff that reconciles debt outstanding,
   proceeds, maturities, and material commitments for the target month.
4. Any issuer investor-relations debt offering, closing release, or financing announcement identified
   by those filings. The filing remains authoritative when an IR copy differs.
5. Amendments and late filings available by the cutoff. Later evidence cannot be used in an earlier
   historical build unless its public availability is within that build cutoff.

Each acquired item keeps the requested and final URL, redirect chain, complete bytes, SHA-256,
content type, public filing/acceptance time, and local fetch time.

## Query and completeness proof

The review record must list every accession in the bounded universe and record deterministic searches
for: `note`, `debt`, `borrow`, `credit`, `loan`, `financing`, `proceeds`, `guarantee`, `AI`,
`artificial intelligence`, `data center`, `datacenter`, `compute`, and named AI counterparties found
in the entity's positive evidence. Search hits and the surrounding filing sections are retained.

A cell may receive zero only when:

- all required filings and exhibits were fetched successfully and hash-verified;
- the submissions index has no unexplained accession or amendment gap;
- the subsequent debt reconciliation is consistent with no qualifying monthly flow;
- two explicit reviewer decisions agree that no qualifying event is present and explain any general-
  purpose financing, lease, guarantee, or supplier-financing near misses;
- the negative-evidence audit is finalized append-only and linked to the zero observation and its
  canonical negative fact.

Any acquisition error, ambiguous use of proceeds, incomplete exhibit set, conflicting reconciliation,
or missing reviewer decision leaves the cell missing. A later correction supersedes the decision
append-only.

## Current 4×6 disposition

The SEC submission histories are an enumeration starting point, not a completed negative review.
No zero cell is accepted from the initial enumeration. The current feature is intrinsically sparse at
monthly grain; if complete filing review confirms that most months have no issuances, the next schema
change should introduce versioned, separately named flow, stock, commitment, and contingent-support
features rather than broaden the meaning of this feature.
