# Security Policy

## Supported Versions

probeflow is pre-1.0 software. Security fixes are applied to the **latest
released version only**; there are no long-term support branches yet.

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| < 0.2   | :x:                |

If you are running an older release, please upgrade to the latest version before
reporting an issue, in case it has already been addressed.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
pull requests, or discussions.**

Instead, report them privately through GitHub's private vulnerability reporting:

➡️ **[Open a private security advisory](https://github.com/Kunal241207/probeflow/security/advisories/new)**
(the **Security** tab → **Report a vulnerability**).

This channel is private to the maintainers until a fix is coordinated, and it
lets us collaborate with you on the report and a fix in one place. If you cannot
use GitHub's reporting form, open a regular issue that contains **no sensitive
details** and asks a maintainer to establish a private channel.

### What to include

To help us triage quickly, please include as much of the following as you can:

- The probeflow version (`probeflow version`) and Python version.
- A description of the vulnerability and its impact.
- Step-by-step reproduction instructions, ideally with a minimal `.http` file
  or command line that triggers the issue.
- Any proof-of-concept code, logs, or stack traces.
- Whether the issue is already public or known to third parties.

### What to expect

- **Acknowledgement:** we aim to confirm receipt within **5 business days**.
- **Assessment:** we will investigate and let you know whether the report is
  accepted, needs more information, or is out of scope.
- **Fix & disclosure:** for accepted reports we will work on a fix, keep you
  updated on progress, and coordinate a disclosure timeline with you. We are
  happy to credit you in the release notes and advisory unless you prefer to
  remain anonymous.

## Scope

This project is a **local CLI**: it parses `.http` files and sends the HTTP
requests they describe. Reports that are especially relevant include:

- Parsing or resolution behavior that leaks secrets (for example, environment
  values or OAuth2 credentials) into logs, error messages, or reports.
- Path-traversal or arbitrary-file-read issues in multipart `@file` handling.
- Any code path that executes untrusted content from a `.http` file. Note that
  hook execution (`@before` / `@after`) is intentionally **not implemented** —
  the runner refuses to execute hook-bearing files — so reports about hooks
  should focus on that refusal being bypassed.

Denial-of-service reports that require an already-hostile local environment
(for example, deliberately crafting a malicious file on your own machine and
running it) are generally considered lower priority, but we still want to hear
about them.

Thank you for helping keep probeflow and its users safe.
