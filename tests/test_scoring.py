from asro.models import Category, SourceItem
from asro.scoring import classify, score


def test_classify_credit() -> None:
    assert classify("AI infrastructure refinancing loan") == Category.CREDIT


def test_classify_distribution() -> None:
    assert classify("pension target-date 401(k)") == Category.DISTRIBUTION


def test_scoring_detects_company_and_signal() -> None:
    item = SourceItem(
        title="Nvidia guarantee supports AI infrastructure refinancing",
        url="https://example.com/story",
        source="Example",
    )

    scored = score(item, ["Nvidia", "OpenAI"])

    assert scored.score >= 6
    assert scored.category == Category.CREDIT
    assert scored.companies == ["Nvidia"]
