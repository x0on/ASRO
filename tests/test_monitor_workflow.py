from __future__ import annotations

from pathlib import Path


def test_monitor_has_one_daily_schedule_and_preserves_manual_backfill() -> None:
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "monitor.yml").read_text(
        encoding="utf-8"
    )

    assert workflow.startswith("name: Daily monitor\n")
    assert workflow.count('cron: "17 10 * * *"') == 1
    assert 'cron: "17 * * * *"' not in workflow
    assert "workflow_dispatch:" in workflow
    assert "backfill:" in workflow
    assert "github.event.schedule == '17 10 * * *'" in workflow
    assert "data: daily observatory update" in workflow
    assert "group: daily-monitor" in workflow
    assert "asro state-restore" in workflow
    assert "asro state-package" in workflow
    assert "gh release upload asro-state" in workflow
    assert "--state-pointer data/state/current.json" in workflow
    assert "git rm --cached --ignore-unmatch data/monitor.db" in workflow


def test_ci_restores_verified_release_state_before_artifact_tests() -> None:
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    restore = "asro state-restore --pointer data/state/current.json --database data/monitor.db"
    assert restore in workflow
    assert workflow.index(restore) < workflow.index("run: pytest")
