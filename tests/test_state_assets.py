from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from asro.state_assets import package_state, restore_state, validate_state_binding


def _database(path: Path, value: str = "current") -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """PRAGMA foreign_keys=ON;
               CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT);
               CREATE TABLE state_value(value TEXT NOT NULL);
               INSERT INTO schema_migrations VALUES(16,'current');"""
        )
        connection.execute("INSERT INTO state_value VALUES(?)", (value,))


def _package(tmp_path: Path, value: str = "current") -> tuple[Path, dict[str, bytes], dict]:
    database = tmp_path / f"{value}.db"
    _database(database, value)
    output = tmp_path / f"package-{value}"
    result = package_state(
        database,
        output,
        repository="x0on/ASRO",
        source_commit="abc",
        workflow_run_id="run-1",
    )
    version = dict(result["version"])
    assets = {
        str(version["manifest_url"]): Path(str(result["manifest_path"])).read_bytes(),
        str(version["asset_url"]): Path(str(result["asset_path"])).read_bytes(),
    }
    return Path(str(result["pointer_path"])), assets, result


def test_missing_or_corrupt_asset_fails_closed(tmp_path: Path) -> None:
    pointer, _assets, _result = _package(tmp_path)
    with pytest.raises(ValueError, match="no valid state asset"):
        restore_state(pointer, tmp_path / "restored.db", downloader=lambda _url: b"missing")


def test_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    pointer, assets, result = _package(tmp_path)
    version = dict(result["version"])
    assets[str(version["asset_url"])] += b"tampered"
    with pytest.raises(ValueError, match="no valid state asset"):
        restore_state(pointer, tmp_path / "restored.db", downloader=assets.__getitem__)


def test_restore_is_idempotent_and_integrity_checked(tmp_path: Path) -> None:
    pointer, assets, _result = _package(tmp_path)
    destination = tmp_path / "restored.db"
    first = restore_state(pointer, destination, downloader=assets.__getitem__)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    second = restore_state(pointer, destination, downloader=assets.__getitem__)
    assert first == second
    assert digest == hashlib.sha256(destination.read_bytes()).hexdigest()


def test_restore_rolls_back_to_prior_valid_version(tmp_path: Path) -> None:
    old_pointer, old_assets, old_result = _package(tmp_path, "old")
    old_version = dict(old_result["version"])
    current_pointer, current_assets, current_result = _package(tmp_path, "new")
    pointer = json.loads(current_pointer.read_text(encoding="utf-8"))
    pointer["versions"].append(old_version)
    current_pointer.write_text(json.dumps(pointer), encoding="utf-8")
    assets = {**old_assets, **current_assets}
    assets[str(dict(current_result["version"])["asset_url"])] = b"broken"

    restored = restore_state(
        current_pointer, tmp_path / "restored.db", downloader=assets.__getitem__
    )
    assert restored["version_id"] == old_version["version_id"]
    assert restored["rollback_used"] is True


def test_packaging_does_not_update_live_pointer_and_retains_three_versions(
    tmp_path: Path,
) -> None:
    live_pointer, _assets, _result = _package(tmp_path, "one")
    original = live_pointer.read_bytes()
    prior = live_pointer
    for value in ("two", "three", "four"):
        database = tmp_path / f"{value}.db"
        _database(database, value)
        result = package_state(
            database,
            tmp_path / f"package-{value}",
            repository="x0on/ASRO",
            source_commit=value,
            workflow_run_id=value,
            prior_pointer=prior,
        )
        prior = Path(str(result["pointer_path"]))
    assert live_pointer.read_bytes() == original
    assert len(json.loads(prior.read_text(encoding="utf-8"))["versions"]) == 3


def test_snapshot_database_mismatch_is_rejected(tmp_path: Path) -> None:
    pointer, _assets, result = _package(tmp_path)
    database = tmp_path / "current.db"
    version = dict(result["version"])
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "database_state_version": version["version_id"],
                "database_sha256": "wrong",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="snapshot"):
        validate_state_binding(database, snapshot, pointer)


def test_database_hash_mismatch_is_rejected_even_for_valid_gzip(tmp_path: Path) -> None:
    pointer, assets, result = _package(tmp_path)
    version = dict(result["version"])
    raw = gzip.decompress(assets[str(version["asset_url"])]) + b"extra"
    assets[str(version["asset_url"])] = gzip.compress(raw, mtime=0)
    with pytest.raises(ValueError, match="no valid state asset"):
        restore_state(pointer, tmp_path / "restored.db", downloader=assets.__getitem__)
