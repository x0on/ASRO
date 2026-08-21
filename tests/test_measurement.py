from asro.measurement import event_to_observation
from asro.models import EventType, FinancialEvent


def test_event_maps_to_observation():
    event = FinancialEvent(
        event_id="e1",
        document_id="d1",
        event_type=EventType.GUARANTEES,
        source_entity="Nvidia",
        target_entity="OpenAI",
        amount=30_000_000_000,
        currency="USD",
        confidence=0.9,
        evidence_text="Nvidia guarantees $30 billion.",
        extractor="test",
    )
    obs = event_to_observation(event)
    assert obs is not None
    assert obs.event_id == "e1"
    assert obs.variable_key == "vendor_financing"
    assert obs.value == 30_000_000_000


def test_unquantified_money_event_is_directional_but_not_fake_dollars() -> None:
    event = FinancialEvent(
        event_id="e2",
        document_id="d2",
        event_type=EventType.REVENUE_REPORT,
        source_entity="OpenAI",
        confidence=0.72,
        evidence_text="OpenAI reported AI revenue growth without disclosing an amount.",
        extractor="test",
    )

    observation = event_to_observation(event)
    assert observation is not None
    assert observation.value == 1.0
    assert observation.unit == "signal"


def test_score_event_uses_severity_not_transaction_amount() -> None:
    event = FinancialEvent(
        event_id="e3",
        document_id="d3",
        event_type=EventType.REFINANCES,
        source_entity="SpaceX",
        amount=20_000_000_000,
        currency="USD",
        confidence=0.82,
        evidence_text="SpaceX refinanced $20 billion.",
        extractor="test",
    )

    observation = event_to_observation(event)
    assert observation is not None
    assert observation.value == 1.0
    assert observation.unit == "score"


def test_confirmed_index_entry_advances_but_does_not_complete_transmission() -> None:
    event = FinancialEvent(
        event_id="e4",
        document_id="d4",
        event_type=EventType.ENTERS_INDEX,
        source_entity="SpaceX",
        target_entity="Nasdaq-100",
        confidence=0.99,
        evidence_text="Nasdaq confirms SpaceX joined the Nasdaq-100.",
        extractor="curated-primary-source-v1",
    )

    observation = event_to_observation(event)
    assert observation is not None
    assert observation.variable_key == "public_market_transmission_stage"
    assert observation.value == 2.0
    assert observation.unit == "score"
