# Contributing

Thank you for contributing.

This project sits at the intersection of finance, software, data engineering, and public-interest research. Contributions do not need to be code.

## Before opening a PR

Please:

1. Open or reference an issue for substantial changes.
2. Keep changes focused.
3. Add or update tests.
4. Preserve source provenance.
5. Avoid embedding thesis conclusions in collectors.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run:

```bash
ruff check .
ruff format --check .
mypy src
pytest
```

## Code style

- Small modules
- Explicit types
- No hidden global state
- No collector-specific database writes
- No scraping logic in scoring
- No model-generated text stored as source fact
- Prefer pure functions where reasonable
- Add tests for parsing and scoring behavior

## Pull requests

A useful PR description answers:

- What problem does this solve?
- Why is this the right layer?
- What source or behavior does it affect?
- How was it tested?
- Does it change stored data?

## Source integrity

Every financial claim should be traceable.

Future extracted events must retain:

- source URL
- publication / filing date
- document identifier where available
- extraction confidence

## Security

Do not commit:

- API keys
- cookies
- credentials
- private datasets
- personal information

See [SECURITY.md](SECURITY.md).

## Research disagreements

Disagreement with the project's thesis is welcome.

The software should make it easier to test and falsify hypotheses, not merely confirm them.
