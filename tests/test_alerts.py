from datetime import UTC, datetime

import pytest

from asro.alerts import news_alerts

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def event(kind="ISSUES_DEBT", **changes):
    return dict(
        event_id="one",
        fingerprint="fact-one",
        event_type=kind,
        review_status="confirmed",
        reviewed_at="2026-08-31T00:00:00Z",
        effective_date="2026-08-20",
        published_at="2026-08-21",
        url="https://example.com/filing",
        title="Reported transaction",
        **changes,
    )


def test_duplicate_mentions_cannot_raise_alert_count():
    row = event()
    duplicate = {**row, "event_id": "another-article", "url": "https://example.com/other"}
    result = news_alerts([row, duplicate], NOW)
    assert result["counts"]["pressure"] == 1
    assert result == news_alerts([duplicate, row], NOW)


def test_added_risk_cannot_reduce_pressure_and_context_does_not_cancel_it():
    rows = [event()]
    first = news_alerts(rows, NOW)["counts"]
    rows.append({**event("DOWNGRADE"), "fingerprint": "second"})
    rows.append({**event("REVENUE_REPORT"), "fingerprint": "third"})
    result = news_alerts(rows, NOW)
    assert result["counts"]["pressure"] == first["pressure"] + 1
    assert result["counts"]["context"] == 1
    assert all(row["rationale"] and row["rule_version"] for row in result["items"])


@pytest.mark.parametrize(
    "change",
    [
        {"review_status": "provisional"},
        {"review_status": "flagged"},
        {"url": None},
        {"url": "javascript:alert(1)"},
        {"effective_date": "2025-01-01"},
        {"effective_date": "2027-01-01"},
        {"reviewed_at": None},
        {"reviewed_at": "2027-01-01"},
        {"published_at": "2027-01-01"},
    ],
)
def test_unreviewed_unsourced_stale_and_future_evidence_is_excluded(change):
    assert not news_alerts([{**event(), **change}], NOW)["items"]


def test_ipo_is_public_exposure_not_completed_trading():
    result = news_alerts([event("FILES_FOR_IPO")], NOW)
    assert result["counts"]["public_exposure"] == 1
    assert "trading has not been established" in result["items"][0]["rationale"]


def test_ipo_backer_headlines_group_under_explicit_issuer():
    rows = [
        {**event("FILES_FOR_IPO"), "fingerprint": "a", "title": "SB Energy Files for IPO"},
        {
            **event("FILES_FOR_IPO"),
            "fingerprint": "b",
            "title": "Softbank's SB Energy files for IPO, with Nvidia backing",
            "url": "https://example.com/second",
        },
    ]
    result = news_alerts(rows, NOW)
    assert result["counts"]["public_exposure"] == 1
    assert len(result["items"][0]["sources"]) == 2
    assert result["items"][0]["underlying_fact_ids"] == ["a", "b"]
