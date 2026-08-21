from asro.extraction.amounts import extract_amount
from asro.extraction.deterministic import DeterministicEventExtractor
from asro.models import Category, EventType, ScoredItem


def make_document(title: str, summary: str = "") -> ScoredItem:
    return ScoredItem(
        title=title,
        summary=summary,
        url="https://example.com/story",
        source="Example",
        item_id="doc-1",
        score=10,
        category=Category.CREDIT,
        companies=["Nvidia", "OpenAI"],
    )


def test_extract_amount_billions() -> None:
    amount, currency = extract_amount("$30 billion investment")
    assert amount == 30_000_000_000
    assert currency == "USD"


def test_extract_guarantee_event() -> None:
    extractor = DeterministicEventExtractor(["Nvidia", "OpenAI"])
    document = make_document("Nvidia guarantees $30 billion financing for OpenAI.")

    events = extractor.extract(document)

    assert len(events) == 1
    assert events[0].event_type == EventType.GUARANTEES
    assert events[0].source_entity == "Nvidia"
    assert events[0].target_entity == "OpenAI"
    assert events[0].amount == 30_000_000_000
    assert events[0].currency == "USD"
    assert events[0].evidence_text


def test_no_event_without_rule_match() -> None:
    extractor = DeterministicEventExtractor(["Nvidia"])
    document = make_document("Nvidia announced a new product.")
    assert extractor.extract(document) == []


def test_google_news_metadata_is_not_treated_as_alphabet() -> None:
    extractor = DeterministicEventExtractor(["DeepSeek", "Google", "Alphabet"])
    document = make_document("DeepSeek cuts prices for its flagship model - Reuters Google News")

    events = extractor.extract(document)

    assert len(events) == 1
    assert events[0].event_type == EventType.PRICE_CUT
    assert events[0].source_entity == "DeepSeek"
    assert events[0].target_entity is None
