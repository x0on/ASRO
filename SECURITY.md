# Security Policy

## Reporting a vulnerability

Please do not publicly disclose exploitable vulnerabilities before maintainers have had a reasonable opportunity to review them.

Never commit API keys or place them in dashboard JavaScript. Local credentials belong in the
ignored `.env` file; deployed credentials belong in GitHub Actions encrypted secrets. If a key
is exposed, revoke it immediately and report the exposure privately.

Until a dedicated security contact is configured, open a GitHub Security Advisory in the repository.

## Secrets

Never commit:

- API keys
- authentication tokens
- cookies
- private credentials
- paywalled content
- personally identifying datasets
