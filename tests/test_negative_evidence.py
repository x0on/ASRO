import hashlib
import json
from pathlib import Path

from asro.backfill.negative_evidence import enumerate_negative_evidence_universe


def test_enumeration_never_creates_zero_from_submission_silence(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "id": "sec-submissions-meta",
                        "entity": "Meta",
                        "cik": "0001326801",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    acquired = tmp_path / "acquired"
    acquired.mkdir()
    content = json.dumps(
        {
            "filings": {
                "recent": {
                    "accessionNumber": ["one", "two"],
                    "filingDate": ["2025-10-30", "2026-01-29"],
                    "form": ["424B2", "10-K"],
                    "primaryDocument": ["notes.htm", "annual.htm"],
                }
            }
        }
    ).encode()
    digest = hashlib.sha256(content).hexdigest()
    (acquired / f"{digest}.bin").write_bytes(content)
    receipts = acquired / "acquisition-receipts.json"
    receipts.write_text(
        json.dumps(
            {
                "receipts": [
                    {
                        "id": "sec-submissions-meta",
                        "content_file": f"{digest}.bin",
                        "content_sha256": digest,
                        "fetched_at": "2026-08-27T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = enumerate_negative_evidence_universe(inventory, receipts, acquired)

    assert result["cell_count"] == 6
    assert result["zero_cells_created"] == 0
    october = next(cell for cell in result["cells"] if cell["month"] == "2025-10")  # type: ignore[union-attr]
    assert october["decision"] == "missing_pending_full_filing_review"
    assert october["in_month_accessions"][0]["accession"] == "one"
