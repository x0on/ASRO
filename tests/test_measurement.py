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
    assert obs.variable_key == "vendor_financing"
    assert obs.value == 30_000_000_000
