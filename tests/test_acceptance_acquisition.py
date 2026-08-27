import json
from pathlib import Path

import pytest
import requests

from asro.backfill.acquisition import acquire_inventory


class _Response:
    def __init__(self, url: str, content: bytes = b"authoritative full document") -> None:
        self.url = url
        self.content = content
        self.headers = {"content-type": "text/html"}
        self.history: list[_Response] = []

    def raise_for_status(self) -> None:
        return None


class _Session(requests.Session):
    def __init__(self, response: _Response) -> None:
        super().__init__()
        self.response = response

    def get(self, url: str, **kwargs: object) -> _Response:  # type: ignore[override]
        return self.response


def _inventory(path: Path, url: str) -> None:
    path.write_text(
        json.dumps(
            {
                "inventory_version": "1.0.0",
                "status": "candidate_acquisition_only",
                "documents": [
                    {
                        "id": "filing",
                        "url": url,
                        "public_availability_at": "2025-10-30T00:00:00Z",
                        "candidate_event_ids": ["candidate"],
                    }
                ],
                "controls": [],
            }
        ),
        encoding="utf-8",
    )


def test_acquisition_freezes_full_bytes_and_candidate_receipt(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.json"
    url = "https://www.sec.gov/Archives/example.htm"
    _inventory(inventory, url)
    output = tmp_path / "evidence"

    result = acquire_inventory(
        inventory,
        output,
        user_agent="ASRO test contact@example.com",
        session=_Session(_Response(url)),
    )

    receipt = result["receipts"][0]  # type: ignore[index]
    assert receipt["classification"] == "candidate_unreviewed"
    assert receipt["content_length"] == len(b"authoritative full document")
    assert (output / str(receipt["content_file"])).read_bytes() == b"authoritative full document"


@pytest.mark.parametrize(
    "url",
    ["http://www.sec.gov/example", "https://example.com/filing", "file:///secret"],
)
def test_acquisition_rejects_non_authoritative_urls(tmp_path: Path, url: str) -> None:
    inventory = tmp_path / "inventory.json"
    _inventory(inventory, url)

    with pytest.raises(ValueError, match="allowed authoritative host"):
        acquire_inventory(
            inventory,
            tmp_path / "evidence",
            user_agent="ASRO test contact@example.com",
            session=_Session(_Response(url)),
        )


def test_acquisition_rejects_redirect_off_authoritative_host(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.json"
    _inventory(inventory, "https://www.sec.gov/Archives/example.htm")

    with pytest.raises(ValueError, match="allowed authoritative host"):
        acquire_inventory(
            inventory,
            tmp_path / "evidence",
            user_agent="ASRO test contact@example.com",
            session=_Session(_Response("https://example.com/copied-filing")),
        )
