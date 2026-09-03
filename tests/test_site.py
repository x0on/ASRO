import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from asro.site import _build_network, build_static_site


def test_entire_dashboard_script_parses(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    page = Path("src/asro/templates/index.html").read_text()
    script = tmp_path / "dashboard.js"
    script.write_text("\n".join(re.findall(r"<script[^>]*>(.*?)</script>", page, re.S)))
    subprocess.run([node, "--check", str(script)], check=True, capture_output=True)  # noqa: S603


def test_news_cards_group_same_source_and_headline_is_not_alert_count(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    page = Path("src/asro/templates/index.html").read_text()
    source = next(
        line for line in page.splitlines() if line.startswith("function renderNewsAlerts(")
    )
    script = tmp_path / "news.mjs"
    script.write_text(
        "const links=[];const make=()=>({append(){},appendChild(){},replaceChildren(){},"
        "set href(value){links.push(value)}});"
        "const document={getElementById:make,createElement:make};"
        "const row={kind:'pressure',url:'https://example.com/filing',date:'2026-09-01',"
        "title:'Filing',rationale:'Debt',causal_role:'VULNERABILITY'};"
        "const data={news_alerts:{items:[row,{...row,rationale:'Guarantees'}]}};"
        + source
        + ";renderNewsAlerts();console.log(JSON.stringify(links));"
    )
    result = subprocess.run([node, str(script)], check=True, capture_output=True, text=True)  # noqa: S603
    assert json.loads(result.stdout) == ["https://example.com/filing"]
    hero = next(
        line for line in page.splitlines() if line.startswith("function renderOverviewPhrase(")
    )
    assert "sig.score" in hero
    assert "data.news_alerts" not in hero


def test_network_weights_distinct_facts_and_carries_mentions() -> None:
    events = [
        {
            "source_entity": "Nvidia",
            "target_entity": "OpenAI",
            "event_type": "INVESTS_IN",
            "mention_count": 7,
        },
        {
            "source_entity": "Nvidia",
            "target_entity": "OpenAI",
            "event_type": "LENDS_TO",
            "mention_count": 1,
        },
    ]
    network = _build_network(events)
    assert {n["id"] for n in network["nodes"]} == {"Nvidia", "OpenAI"}
    by_type = {e["type"]: e for e in network["edges"]}
    assert by_type["INVESTS_IN"]["weight"] == 1
    assert by_type["INVESTS_IN"]["mentions"] == 7
    assert by_type["LENDS_TO"]["weight"] == 1


def test_network_includes_company_news_as_evidence_nodes() -> None:
    events = [{"source_entity": "Nvidia", "event_type": "REVENUE_REPORT"}]
    items = [
        {
            "item_id": "abc",
            "title": "Nvidia reports earnings",
            "companies": '["Nvidia"]',
            "category": "General AI capital",
            "source": "Example",
            "url": "https://example.com/news",
            "published_at": "2026-08-21",
        },
        {
            "item_id": "unresolved",
            "title": "Economic evidence awaiting entity resolution",
            "companies": "[]",
            "category": "General AI capital",
        },
    ]

    network = _build_network(events, items)

    evidence = [node for node in network["nodes"] if node["kind"] == "evidence"]
    assert {node["label"] for node in evidence} == {
        "Nvidia reports earnings",
        "Economic evidence awaiting entity resolution",
    }
    assert any(edge["type"] == "EVIDENCE" for edge in network["edges"])
    assert {node["id"] for node in evidence} == {"evidence:abc", "evidence:unresolved"}


def test_network_uses_storage_item_id_for_unique_evidence_nodes() -> None:
    network = _build_network(
        [],
        [
            {"id": "first", "title": "First source", "companies": "[]"},
            {"id": "second", "title": "Second source", "companies": "[]"},
        ],
    )

    assert {node["id"] for node in network["nodes"]} == {
        "evidence:first",
        "evidence:second",
    }


def test_build_static_site_on_empty_db_reports_insufficient_evidence(tmp_path: Path) -> None:
    out = build_static_site(output_dir=tmp_path / "site", database_path=tmp_path / "empty.db")

    assert (out / "index.html").exists()
    assert (out / ".nojekyll").exists()
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="companyLabelsToggle"' in html
    assert "drawCompanyLabels" in html
    payload = json.loads((out / "data" / "snapshot.json").read_text())
    assert payload["event_count"] == 0
    assert payload["review_counts"] == {
        "provisional": 0,
        "confirmed": 0,
        "flagged": 0,
        "flagged_retry_pending": 0,
    }
    assert payload["signal"]["score"] is None
    assert payload["signal"]["label"] == "INSUFFICIENT EVIDENCE"
    assert payload["signal"]["direction"] == "unknown"
    assert payload["network"] == {"nodes": [], "edges": []}
    assert len(payload["measurements"]) == 13
    assert "Anthropic" in payload["tracked_entities"]
    assert "DeepSeek" in payload["tracked_entities"]
    assert "Alibaba" in payload["tracked_entities"]
    assert payload["dimension_evidence"] == {}
    assert payload["dimension_evidence_items"] == {}
    assert all(value is None for value in payload["dimensions"].values())


def test_measured_trend_reads_published_history(tmp_path: Path) -> None:
    """The trend text must come from `data.history`, not from a hardcoded string.

    171 snapshots ship in every `snapshot.json` and nothing read them, so the dashboard
    said "Not established" whatever the score did. The logic lives in the template, so
    the check runs it there: extract the function and evaluate it against fixed series.
    """
    template = Path("src/asro/templates/index.html").read_text(encoding="utf-8")
    assert "Not established" not in template
    assert "measuredTrend(data.comparable_history,data.signal.indicator_version)" in template
    assert "Indicator change · not a risk direction" in template
    assert "not a measurement of whether systemic risk rose or fell" in template
    assert "not enough history yet" not in template

    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI without node still gets the checks above
        pytest.skip("node is not available")
    start = template.index("function measuredTrend(")
    source = template[start : template.index("function plainLevel(")]

    def day(number: int, score: float | None) -> dict[str, object]:
        return {"captured_at": f"2026-08-{number:02d}T20:00:00+00:00", "score": score}

    cases = [
        ([], "Trend pending"),
        ([day(22, 50)], "Trend pending"),
        ([day(24, 50), day(23, 45), day(22, 40)], "Indicator up 10.0"),
        ([day(24, 40), day(23, 45), day(22, 50)], "Indicator down 10.0"),
        ([day(24, 44.6), day(23, 44.9), day(22, 44.8)], "Indicator steady"),
        ([day(24, 50), day(23, None), day(22, 45), day(21, 40)], "Indicator up 10.0"),
        # several readings land on one day; the last one is that day's value
        (
            [
                {"captured_at": "2026-08-24T01:00:00+00:00", "score": 99},
                {"captured_at": "2026-08-24T23:00:00+00:00", "score": 50},
                day(23, 45),
                day(22, 40),
            ],
            "Indicator up 10.0",
        ),
    ]
    script = tmp_path / "trend.mjs"
    script.write_text(
        source
        + "const cases="
        + json.dumps([history for history, _ in cases])
        + ";console.log(JSON.stringify(cases.map(h=>measuredTrend(h).text)));",
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603
        [node, str(script)], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == [text for _, text in cases]


def test_overview_disclaimer_spans_grid_and_unvalidated_score_stays_amber(tmp_path: Path) -> None:
    template = Path("src/asro/templates/index.html").read_text()
    assert ".overview-phrase>.calibration-banner{grid-column:1 / -1;" in template
    assert ".overview-phrase>.overview-band{grid-column:1 / -1;" in template
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    source = next(
        line for line in template.splitlines() if line.startswith("function renderCalibration(")
    )
    script = tmp_path / "calibration.mjs"
    script.write_text(
        "const banner={dataset:{}};const document={querySelectorAll(){return [banner]}};"
        + source
        + "renderCalibration({calibration_label:'HISTORICALLY CALIBRATED',"
        "indicator_validation:'Revised indicator: not historically validated'});"
        "console.log(JSON.stringify(banner));"
    )
    result = subprocess.run([node, str(script)], capture_output=True, text=True, check=True)  # noqa: S603
    banner = json.loads(result.stdout)
    assert banner["dataset"]["calibrated"] == "false"
    assert "not historically validated" in banner["textContent"]


def test_public_view_excludes_unsourced_and_missing_cards(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    template = Path("src/asro/templates/index.html").read_text()
    source = next(
        line for line in template.splitlines() if line.startswith("function sourceLinkedView(")
    )
    assert "data=sourceLinkedView(await res.json())" in template
    assert "data=sourceLinkedView(next)" in template
    script = tmp_path / "sources.mjs"
    script.write_text(
        source + "\nconst rows=[{value_numeric:0,url:'https://example.com/filing'},"
        "{value_numeric:null,url:'https://example.com/filing'},"
        "{value_numeric:5},{value_numeric:5,url:'javascript:alert(1)'}];"
        "const original={feature_family:rows,fundamentals:rows,"
        "timeline:rows,public_market_alerts:rows};"
        "console.log(JSON.stringify({view:sourceLinkedView(original),original}));"
    )
    result = subprocess.run([node, str(script)], capture_output=True, text=True, check=True)  # noqa: S603
    payload = json.loads(result.stdout)
    for key in ("feature_family", "fundamentals"):
        assert payload["view"][key] == [{"value_numeric": 0, "url": "https://example.com/filing"}]
        assert len(payload["original"][key]) == 4
    assert len(payload["view"]["timeline"]) == 2
    assert len(payload["view"]["public_market_alerts"]) == 2


def test_carried_measurements_group_without_merging_distinct_filings(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    template = Path("src/asro/templates/index.html").read_text()
    source = next(
        line for line in template.splitlines() if line.startswith("function groupedMeasurements(")
    )
    script = tmp_path / "grouped.mjs"
    script.write_text(
        source + "\nconst row={entity_id:'Meta',value_numeric:77.8,url:'https://example.com/a'};"
        "console.log(JSON.stringify(groupedMeasurements(["
        "{...row,period_start:'2025-01-01'}, {...row,period_start:'2025-02-01'},"
        "{...row,period_start:'2025-03-01',url:'https://example.com/b'}])));"
    )
    result = subprocess.run([node, str(script)], capture_output=True, text=True, check=True)  # noqa: S603
    groups = json.loads(result.stdout)
    assert len(groups) == 2
    assert groups[0]["display_months"] == "2025-01, 2025-02"
    assert groups[1]["display_months"] == "2025-03"
    assert "Methodology definitions, not measured evidence." in template
    assert "src/asro/dictionary/registry.py#L" in template
    assert "Definition ↗" in template


def test_public_market_alert_renderer_is_independent_and_uses_text_nodes(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    template = Path("src/asro/templates/index.html").read_text()
    source = template[
        template.index("function renderPublicMarketAlerts()") : template.index(
            "function renderDashboard()"
        )
    ]
    script = tmp_path / "alerts.mjs"
    script.write_text(
        "const nodes=[];const panel={replaceChildren(){},appendChild(n){nodes.push(n)}};"
        "const document={getElementById(){return panel},createElement(){return {appendChild(){}}},"
        "createTextNode(t){return t}};"
        "const data={signal:{score:null},public_market_alerts:[{company:'OpenAI',"
        "stage:'IPO filing: potential public-investor exposure',date:'2026-06-08',"
        "title:'Filing report',url:'https://example.com/filing'}]};"
        + source
        + "renderPublicMarketAlerts();console.log(JSON.stringify(nodes.map(n=>n.textContent)));"
    )
    result = subprocess.run([node, str(script)], capture_output=True, text=True, check=True)  # noqa: S603
    assert "OpenAI" in result.stdout
    assert "potential public-investor exposure" in result.stdout
    assert "innerHTML" not in source
