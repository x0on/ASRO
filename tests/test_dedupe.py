from asro.dedupe import economic_fingerprint
from asro.entities import canonicalize
from asro.models import EventType, FinancialEvent


def test_entity_aliases():
    assert canonicalize("AWS") == "Amazon"
    assert canonicalize("Google") == "Alphabet"


def test_economic_fingerprint_ignores_document_id():
    kw = dict(
        event_type=EventType.ISSUES_DEBT,
        source_entity="Oracle",
        target_entity=None,
        amount=10_000_000_000,
        currency="USD",
        instrument="debt",
        effective_date="2026-08-20",
        confidence=0.8,
        evidence_text="x",
        extractor="test",
    )
    a = FinancialEvent(event_id="a", document_id="d1", **kw)
    b = FinancialEvent(event_id="b", document_id="d2", **kw)
    assert economic_fingerprint(a) == economic_fingerprint(b)


def test_economic_fingerprint_spans_days_but_not_months():
    kw = dict(
        event_type=EventType.GUARANTEES,
        source_entity="Nvidia",
        target_entity="OpenAI",
        amount=30_000_000_000,
        currency="USD",
        confidence=0.9,
        evidence_text="x",
        extractor="test",
    )
    day1 = FinancialEvent(event_id="a", document_id="d1", effective_date="2026-08-03", **kw)
    day9 = FinancialEvent(event_id="b", document_id="d2", effective_date="2026-08-11", **kw)
    later = FinancialEvent(event_id="c", document_id="d3", effective_date="2026-11-02", **kw)
    assert economic_fingerprint(day1) == economic_fingerprint(day9)
    assert economic_fingerprint(day1) != economic_fingerprint(later)
