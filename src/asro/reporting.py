from __future__ import annotations

import csv
import html
from datetime import datetime
from pathlib import Path
from sqlite3 import Row


def write_csv(rows: list[Row], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    path = report_dir / f"ai_risk_monitor_{stamp}.csv"

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(rows[0].keys() if rows else [])
        for row in rows:
            writer.writerow(tuple(row))

    return path


def write_html(
    rows: list[Row],
    report_dir: Path,
    high_signal_threshold: int,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "latest.html"

    high_signal = [row for row in rows if int(row["score"]) >= high_signal_threshold]

    cards: list[str] = []
    for row in high_signal:
        cards.append(
            f"""
            <article class="item">
              <div class="meta">
                <span class="score">Score {row["score"]}</span>
                <span>{html.escape(row["category"])}</span>
                <span>{html.escape(row["source"])}</span>
              </div>
              <h2>
                <a href="{html.escape(row["url"])}" target="_blank" rel="noopener">
                  {html.escape(row["title"])}
                </a>
              </h2>
              <p>{html.escape(row["summary"])}</p>
              <div class="small">
                {html.escape(row["published_at"] or "")}
              </div>
            </article>
            """
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Systemic Risk Observatory</title>
<style>
body {{
  font-family: system-ui, -apple-system, sans-serif;
  max-width: 980px;
  margin: 40px auto;
  padding: 0 18px;
  background: #fafafa;
  color: #151515;
}}
h1 {{ margin-bottom: 4px; }}
.sub {{ color: #666; margin-bottom: 28px; }}
.item {{
  background: white;
  border: 1px solid #ddd;
  border-radius: 12px;
  padding: 18px;
  margin: 14px 0;
}}
.item h2 {{ font-size: 18px; margin: 10px 0; }}
a {{ color: inherit; }}
.meta {{
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #555;
}}
.score {{ font-weight: 700; color: #111; }}
.small {{ font-size: 12px; color: #777; }}
.legend {{
  background: white;
  border-left: 4px solid #333;
  padding: 12px 16px;
  margin: 20px 0;
}}
</style>
</head>
<body>
<h1>AI Systemic Risk Observatory</h1>
<div class="sub">
Generated {datetime.now().strftime("%Y-%m-%d %H:%M")}.
Showing items scoring at least {high_signal_threshold}.
</div>

<div class="legend">
<strong>Look for clusters:</strong>
refinancing stress + guarantees + widening credit spreads +
cancelled leases + model price cuts + rapid IPO/index inclusion +
pension/private-market exposure.
</div>

{"".join(cards) if cards else "<p>No high-signal items yet.</p>"}
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")
    return path
