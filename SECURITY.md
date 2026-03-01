# Security Policy

## Supported Versions

Security fixes are applied to the latest `main` branch.

If you deploy Sentinel from a pinned commit, upgrade to the latest `main` before reporting an issue unless the vulnerability prevents upgrading.

## Reporting a Vulnerability

Please do **not** open public GitHub issues for security vulnerabilities.

Report privately by email:

- `security@arais.us`

Include:

- affected component(s) (`gateway`, `sentinel-backend`, `sentinel-frontend`, `araios-backend`, `araios-frontend`)
- reproduction steps or proof-of-concept
- impact assessment (what an attacker can do)
- your suggested fix (if available)

## Response Targets

- Initial acknowledgement: within 72 hours
- Status update: within 7 days
- Fix timeline: depends on severity and exploitability

## Scope

In scope:

- authentication and authorization bypass
- secrets exposure
- privilege escalation
- remote code execution
- SSRF / internal network access bypass
- vulnerabilities in default Docker/local deployment

Out of scope:

- social engineering
- denial of service from local machine misuse
- vulnerabilities only present in modified forks

## Disclosure

Please allow time for patching before public disclosure.
Coordinated disclosure is preferred.
