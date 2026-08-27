from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

_ALLOWED_HOSTS = {"www.sec.gov", "fred.stlouisfed.org"}


def acquire_inventory(
    inventory_path: Path,
    output_directory: Path,
    *,
    user_agent: str,
    include_controls: bool = True,
    session: requests.Session | None = None,
) -> dict[str, object]:
    """Fetch declared authoritative evidence without accepting or promoting any claim."""
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict) or inventory.get("status") != "candidate_acquisition_only":
        raise ValueError("inventory must be explicitly classified as candidate acquisition only")
    entries = _entries(inventory, include_controls=include_controls)
    client = session or requests.Session()
    output_directory.mkdir(parents=True, exist_ok=True)
    receipts: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for entry in entries:
        entry_id = str(entry["id"])
        if entry_id in seen_ids:
            raise ValueError(f"duplicate acquisition id: {entry_id}")
        seen_ids.add(entry_id)
        requested_url = str(entry["url"])
        _require_authoritative_url(requested_url)
        response = client.get(
            requested_url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,text/plain,text/csv,application/json,*/*;q=0.1",
            },
            timeout=60,
        )
        response.raise_for_status()
        final_url = str(response.url)
        _require_authoritative_url(final_url)
        content = response.content
        if not content:
            raise ValueError(f"empty authoritative response: {entry_id}")
        digest = hashlib.sha256(content).hexdigest()
        content_path = output_directory / f"{digest}.bin"
        if content_path.exists() and content_path.read_bytes() != content:
            raise ValueError(f"content-address collision: {digest}")
        content_path.write_bytes(content)
        receipts.append(
            {
                "id": entry_id,
                "classification": "candidate_unreviewed",
                "requested_url": requested_url,
                "final_url": final_url,
                "redirect_chain": [str(item.url) for item in response.history],
                "public_availability_at": entry.get("public_availability_at"),
                "fetched_at": datetime.now(UTC).isoformat(timespec="microseconds"),
                "content_sha256": digest,
                "content_length": len(content),
                "content_type": response.headers.get("content-type", ""),
                "content_file": content_path.name,
                "candidate_event_ids": entry.get("candidate_event_ids", []),
            }
        )
    report: dict[str, object] = {
        "inventory_version": inventory.get("inventory_version"),
        "classification": "candidate_unreviewed",
        "entry_count": len(receipts),
        "receipts": receipts,
    }
    receipt_path = output_directory / "acquisition-receipts.json"
    receipt_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _entries(inventory: Mapping[str, Any], *, include_controls: bool) -> list[dict[str, Any]]:
    documents = inventory.get("documents")
    controls = inventory.get("controls", []) if include_controls else []
    if not isinstance(documents, list) or not documents:
        raise ValueError("inventory must declare documents")
    if not isinstance(controls, list):
        raise ValueError("inventory controls must be a list")
    entries: list[dict[str, Any]] = []
    for raw in [*documents, *controls]:
        if not isinstance(raw, dict) or "url" not in raw:
            raise ValueError("every acquisition entry requires a URL")
        entry = dict(raw)
        if "id" not in entry:
            series_id = entry.get("series_id")
            if not isinstance(series_id, str):
                raise ValueError("every acquisition entry requires an id")
            entry["id"] = f"control-{series_id}"
        entries.append(entry)
    return entries


def _require_authoritative_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise ValueError(f"acquisition URL is not an allowed authoritative host: {url}")
