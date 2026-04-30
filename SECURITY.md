# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

1. **DO NOT** open a public GitHub issue for security vulnerabilities.
2. Report via [GitHub Security Advisory](https://github.com/Caliquende/OKX_Spot_Trading_AI-Based_Bot/security/advisories/new).
3. Include a detailed description, steps to reproduce, and any potential impact.
4. We will acknowledge your report within 48 hours.

## Security Measures

- **Dependabot:** Monitors pip and GitHub Actions dependencies for known vulnerabilities.
- **CodeQL:** Static analysis scans Python code for security patterns on every push/PR.
- **Bandit:** SAST tool scans for common Python security issues in CI.
- **pip-audit:** Checks installed packages against CVE databases.
- **Pre-commit Hooks:** detect-secrets, detect-private-key, and Bandit run before every commit.

## Critical Security Notes

- **API Keys:** OKX API credentials (API Key, Secret, Passphrase) must NEVER be committed to the repository. Use `.env` files which are gitignored.
- **Telegram Bot Token:** If using Telegram reporting, store the bot token in `.env` only.
- **Database:** Local SQLite databases containing trade history are gitignored.
- **CI/CD:** Dummy credentials are used in CI; never use real credentials in automated pipelines.
