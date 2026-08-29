from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

STATE_FORMAT_VERSION = 1
RELEASE_TAG = "asro-state"
MIN_ROLLBACK_VERSIONS = 2
POINTER_HISTORY_LIMIT = 3


def package_state(
    database: Path,
    output_directory: Path,
    *,
    repository: str,
    source_commit: str,
    workflow_run_id: str,
    prior_pointer: Path | None = None,
) -> dict[str, object]:
    _validate_database(database)
    raw = database.read_bytes()
    database_sha = hashlib.sha256(raw).hexdigest()
    version_id = f"v1-{database_sha[:16]}"
    output_directory.mkdir(parents=True, exist_ok=True)
    asset_name = f"monitor-{version_id}.db.gz"
    asset_path = output_directory / asset_name
    with (
        asset_path.open("wb") as target,
        gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as compressed,
    ):
        compressed.write(raw)
    compressed_sha = _sha256_file(asset_path)
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        migration_version = int(
            connection.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[
                0
            ]
        )
    finally:
        connection.close()
    base_url = f"https://github.com/{repository}/releases/download/{RELEASE_TAG}"
    manifest: dict[str, object] = {
        "format_version": STATE_FORMAT_VERSION,
        "version_id": version_id,
        "database_sha256": database_sha,
        "database_bytes": len(raw),
        "compressed_sha256": compressed_sha,
        "compressed_bytes": asset_path.stat().st_size,
        "schema_migration_version": migration_version,
        "source_commit": source_commit,
        "workflow_run_id": workflow_run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "asset_name": asset_name,
        "asset_url": f"{base_url}/{asset_name}",
    }
    manifest_text = _canonical_json(manifest)
    manifest_name = f"monitor-{version_id}.manifest.json"
    manifest_path = output_directory / manifest_name
    manifest_path.write_bytes(manifest_text.encode())
    version = {
        **manifest,
        "manifest_name": manifest_name,
        "manifest_url": f"{base_url}/{manifest_name}",
        "manifest_sha256": hashlib.sha256(manifest_text.encode()).hexdigest(),
    }
    prior_versions: list[dict[str, object]] = []
    if prior_pointer is not None and prior_pointer.exists():
        prior = json.loads(prior_pointer.read_text(encoding="utf-8"))
        prior_versions = [dict(item) for item in prior.get("versions", [])]
    versions = [version, *(item for item in prior_versions if item.get("version_id") != version_id)]
    pointer = {
        "format_version": STATE_FORMAT_VERSION,
        "release_tag": RELEASE_TAG,
        "current_version_id": version_id,
        "versions": versions[:POINTER_HISTORY_LIMIT],
    }
    pointer_path = output_directory / "current.json"
    pointer_path.write_bytes(_canonical_json(pointer).encode())
    return {
        "version": version,
        "pointer": pointer,
        "asset_path": str(asset_path),
        "manifest_path": str(manifest_path),
        "pointer_path": str(pointer_path),
    }


def restore_state(
    pointer_path: Path,
    database: Path,
    *,
    downloader: Callable[[str], bytes] | None = None,
) -> dict[str, object]:
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    versions = pointer.get("versions")
    if not isinstance(versions, list) or not versions:
        raise ValueError("state pointer has no recovery versions")
    fetch = downloader or _download
    failures: list[str] = []
    for raw_version in versions:
        version = dict(raw_version)
        try:
            restored = _restore_version(version, database, fetch)
            restored["rollback_used"] = version.get("version_id") != pointer.get(
                "current_version_id"
            )
            return restored
        except (OSError, ValueError, requests.RequestException) as exc:
            failures.append(f"{version.get('version_id')}: {exc}")
    raise ValueError("no valid state asset could be restored: " + "; ".join(failures))


def validate_state_binding(database: Path, snapshot: Path, pointer: Path) -> dict[str, str]:
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    state = json.loads(pointer.read_text(encoding="utf-8"))
    current = next(
        (
            dict(item)
            for item in state.get("versions", [])
            if item.get("version_id") == state.get("current_version_id")
        ),
        None,
    )
    if current is None:
        raise ValueError("current state version is absent from pointer history")
    database_sha = _sha256_file(database)
    if database_sha != current.get("database_sha256"):
        raise ValueError("database does not match current state pointer")
    if (
        payload.get("database_state_version") != current.get("version_id")
        or payload.get("database_sha256") != database_sha
    ):
        raise ValueError("public snapshot does not match current database state")
    return {"version_id": str(current["version_id"]), "database_sha256": database_sha}


def _restore_version(
    version: dict[str, Any], database: Path, downloader: Callable[[str], bytes]
) -> dict[str, object]:
    manifest_bytes = downloader(str(version["manifest_url"]))
    if hashlib.sha256(manifest_bytes).hexdigest() != version["manifest_sha256"]:
        raise ValueError("manifest hash mismatch")
    manifest = json.loads(manifest_bytes)
    for key in (
        "version_id",
        "database_sha256",
        "database_bytes",
        "compressed_sha256",
        "compressed_bytes",
        "schema_migration_version",
        "asset_name",
        "asset_url",
    ):
        if manifest.get(key) != version.get(key):
            raise ValueError(f"pointer/manifest mismatch: {key}")
    compressed = downloader(str(manifest["asset_url"]))
    if len(compressed) != int(manifest["compressed_bytes"]):
        raise ValueError("compressed state byte-size mismatch")
    if hashlib.sha256(compressed).hexdigest() != manifest["compressed_sha256"]:
        raise ValueError("compressed state hash mismatch")
    try:
        raw = gzip.decompress(compressed)
    except gzip.BadGzipFile as exc:
        raise ValueError("state asset is not valid gzip") from exc
    if len(raw) != int(manifest["database_bytes"]):
        raise ValueError("database byte-size mismatch")
    if hashlib.sha256(raw).hexdigest() != manifest["database_sha256"]:
        raise ValueError("database hash mismatch")
    database.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix="asro-state-", suffix=".db", dir=database.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(raw)
        _validate_database(temporary)
        os.replace(temporary, database)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "version_id": manifest["version_id"],
        "database_sha256": manifest["database_sha256"],
        "schema_migration_version": manifest["schema_migration_version"],
    }


def _validate_database(path: Path) -> None:
    if not path.exists():
        raise ValueError("state database is missing")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise ValueError("SQLite integrity_check failed")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise ValueError("SQLite foreign_key_check failed")
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            is None
        ):
            raise ValueError("state database has no migration ledger")
    finally:
        connection.close()


def _download(url: str) -> bytes:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.content


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
