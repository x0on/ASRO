from __future__ import annotations

ALIASES = {
    "NVIDIA Corporation": "Nvidia",
    "NVIDIA": "Nvidia",
    "NVDA": "Nvidia",
    "Microsoft Corporation": "Microsoft",
    "MSFT": "Microsoft",
    "Amazon Web Services": "Amazon",
    "AWS": "Amazon",
    "Amazon.com": "Amazon",
    "Alphabet Inc.": "Alphabet",
    "Google LLC": "Alphabet",
    "Google": "Alphabet",
    "Meta Platforms": "Meta",
    "Oracle Corporation": "Oracle",
    "CoreWeave, Inc.": "CoreWeave",
    "BlackRock, Inc.": "BlackRock",
    "OpenAI Global": "OpenAI",
    "OpenAI Group": "OpenAI",
}


def canonicalize(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = " ".join(name.strip().split())
    return ALIASES.get(cleaned, cleaned)


def canonicalize_many(names: list[str]) -> list[str]:
    out = []
    for n in names:
        c = canonicalize(n)
        if c and c not in out:
            out.append(c)
    return out
