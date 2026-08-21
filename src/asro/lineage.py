from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import HttpUrl

from asro.dedupe import economic_fingerprint
from asro.measurement import event_to_observation
from asro.models import Category, EventType, FinancialEvent, ScoredItem
from asro.storage import SqliteRepository


@dataclass(frozen=True)
class VerifiedFact:
    title: str
    url: str
    source: str
    date: str
    event_type: EventType
    source_entity: str
    target_entity: str | None
    evidence: str
    amount: float | None = None
    currency: str | None = None


SPACEX_PROSPECTUS = (
    "https://content.spacex.com/cms-assets/FINAL_Documents%20and%20Updates/"
    "SpaceX%20-%20EU%20Prospectus%20%28Approved%20by%20Bafin%29%20-%20June%205%2C%202026.pdf"
)

VERIFIED_LINEAGE: tuple[VerifiedFact, ...] = (
    VerifiedFact(
        "X Holdings completes $44 billion acquisition of Twitter",
        "https://www.sec.gov/Archives/edgar/data/1418091/000119312522272772/d411753d8k.htm",
        "Twitter SEC filing",
        "2022-10-27",
        EventType.ACQUIRES,
        "X Holdings",
        "X (Twitter)",
        "Twitter reported that the merger was completed on October 27, 2022 and Twitter "
        "became a wholly owned subsidiary of X Holdings. The transaction valued Twitter at "
        "approximately $44 billion.",
        44_000_000_000,
        "USD",
    ),
    VerifiedFact(
        "Twitter acquisition financed with $13 billion of company debt",
        "https://www.sec.gov/Archives/edgar/data/1418091/000119312522195126/d283119dprer14a.htm",
        "Twitter SEC proxy",
        "2022-10-27",
        EventType.ASSUMES_DEBT,
        "X (Twitter)",
        "X Holdings",
        "Twitter's proxy disclosed $13 billion in secured and unsecured debt financing "
        "commitments for the acquisition.",
        13_000_000_000,
        "USD",
    ),
    VerifiedFact(
        "xAI acquires X Holdings in a share exchange",
        SPACEX_PROSPECTUS,
        "SpaceX 2026 prospectus",
        "2025-03-28",
        EventType.ACQUIRES,
        "xAI",
        "X (Twitter)",
        "SpaceX's prospectus states that xAI acquired X Holdings effective March 28, 2025 "
        "and X became a wholly owned subsidiary of xAI.",
    ),
    VerifiedFact(
        "SpaceX acquires xAI, including X",
        SPACEX_PROSPECTUS,
        "SpaceX 2026 prospectus",
        "2026-02-02",
        EventType.ACQUIRES,
        "SpaceX",
        "xAI",
        "SpaceX's prospectus states that SpaceX acquired xAI, including X, effective "
        "February 2, 2026 and recast its financial statements to include their historical results.",
    ),
    VerifiedFact(
        "SpaceX assumes debt through the xAI acquisition",
        SPACEX_PROSPECTUS,
        "SpaceX 2026 prospectus",
        "2026-02-02",
        EventType.ASSUMES_DEBT,
        "SpaceX",
        "xAI",
        "SpaceX's prospectus states that the company had a technical default when it "
        "acquired xAI because of the amount of debt assumed at the subsidiary level.",
    ),
    VerifiedFact(
        "SpaceX uses $20 billion bridge loan to refinance X and xAI debt",
        SPACEX_PROSPECTUS,
        "SpaceX 2026 prospectus",
        "2026-03-02",
        EventType.REFINANCES,
        "SpaceX",
        "xAI",
        "SpaceX entered a $20 billion bridge loan whose proceeds repaid X term loans and "
        "xAI loans and secured notes.",
        20_000_000_000,
        "USD",
    ),
    VerifiedFact(
        "SpaceX completes initial public offering and begins trading as SPCX",
        "https://content.spacex.com/cms-assets/FINAL_Documents%20and%20Updates/SpaceX_PricingAnnouncement.pdf?embed=true",
        "SpaceX pricing announcement",
        "2026-06-12",
        EventType.COMPLETES_IPO,
        "SpaceX",
        "Nasdaq",
        "SpaceX announced that 555,555,555 Class A shares were priced at $135 per "
        "share and would begin trading on Nasdaq on June 12, 2026 under SPCX.",
        75_000_000_000,
        "USD",
    ),
    VerifiedFact(
        "SpaceX joins the Nasdaq-100 Index",
        "https://ir.nasdaq.com/news-releases/news-release-details/space-exploration-technologies-corporation-join-nasdaq-100",
        "Nasdaq index announcement",
        "2026-07-07",
        EventType.ENTERS_INDEX,
        "SpaceX",
        "Nasdaq-100",
        "Nasdaq announced that SpaceX (SPCX) would become a Nasdaq-100 component "
        "before market open on July 7, 2026. Nasdaq states that more than 200 "
        "investment products with over $800 billion in assets track the index.",
    ),
    VerifiedFact(
        "OpenAI cuts GPT-5.6 Luna and Terra API prices",
        "https://openai.com/index/advancing-the-price-performance-frontier-with-gpt-5-6/",
        "OpenAI pricing announcement",
        "2026-07-30",
        EventType.PRICE_CUT,
        "OpenAI",
        None,
        "OpenAI announced that GPT-5.6 Luna would cost 80 percent less and GPT-5.6 "
        "Terra would cost 20 percent less starting July 30, 2026.",
    ),
)


def seed_verified_lineage(repository: SqliteRepository) -> int:
    """Insert source-backed ownership and liability lineage exactly once."""
    inserted = 0
    now = datetime.now(UTC).isoformat()
    with repository.connect() as connection:
        for fact in VERIFIED_LINEAGE:
            item_id = hashlib.sha256(f"{fact.url}|{fact.title}".encode()).hexdigest()
            item = ScoredItem(
                item_id=item_id,
                title=fact.title,
                url=HttpUrl(fact.url),
                source=fact.source,
                summary=fact.evidence,
                published_at=fact.date,
                score=20,
                category=(
                    Category.IPO
                    if fact.event_type in {EventType.COMPLETES_IPO, EventType.ENTERS_INDEX}
                    else Category.CANNIBALIZATION
                    if fact.event_type is EventType.PRICE_CUT
                    else Category.CREDIT
                    if fact.event_type
                    in {EventType.ASSUMES_DEBT, EventType.ISSUES_DEBT, EventType.REFINANCES}
                    else Category.GENERAL
                ),
                companies=[
                    company
                    for company in (fact.source_entity, fact.target_entity)
                    if company is not None
                ],
            )
            if not repository.insert(connection, item):
                continue
            repository.upsert_document(
                connection, item_id, now, "text/plain", "verified", fact.evidence
            )
            event_id = hashlib.sha256(
                f"verified|{fact.event_type.value}|{fact.source_entity}|{fact.target_entity}|{fact.date}|{fact.amount}".encode()
            ).hexdigest()
            event = FinancialEvent(
                event_id=event_id,
                document_id=item_id,
                event_type=fact.event_type,
                source_entity=fact.source_entity,
                target_entity=fact.target_entity,
                amount=fact.amount,
                currency=fact.currency,
                effective_date=fact.date,
                confidence=0.99,
                evidence_text=fact.evidence,
                extractor="curated-primary-source-v1",
            )
            fingerprint = economic_fingerprint(event)
            repository.register_economic_event(connection, fingerprint, event_id, now)
            repository.insert_event(connection, event)
            if observation := event_to_observation(event):
                repository.insert_observation(connection, observation)
            repository.apply_review(
                connection,
                fingerprint,
                "confirm",
                None,
                0.99,
                "Verified against the cited primary filing or prospectus.",
                "curated-primary-source-v1",
                now,
            )
            inserted += 1
        connection.commit()
    return inserted
