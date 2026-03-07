# Security Policy

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

If you discover a security issue in Tessera, please report it responsibly:

1. Email: **23284131+agent47nh@users.noreply.github.com**
2. Include a description of the vulnerability, steps to reproduce, and potential impact
3. We will acknowledge receipt within 48 hours
4. We will provide a timeline for a fix within 5 business days

## Supported Versions

| Version | Supported |
|---------|-----------|
| main (dev) | ✅ |

## Scope

The following are in scope:

- Authentication bypass (admin API key, HMAC vote signing)
- Authorization flaws (read/write separation)
- IP binding bypass (`bind_ip` enforcement)
- Injection attacks (API endpoints, config parsing)
- Information disclosure (voter PSKs, API tokens, admin keys)
- Container escape or privilege escalation
- CSP bypass or XSS

The following are **out of scope**:

- Denial of service against the voter quorum (requires network access to all voters)
- Issues in Technitium DNS Server itself (report upstream)
- Social engineering
- Issues requiring physical access to the host

## Disclosure Policy

We follow coordinated disclosure. We ask that you:

- Give us reasonable time to fix the issue before public disclosure
- Do not exploit the vulnerability beyond what is necessary to demonstrate it
- Do not access or modify other users' data

We will credit reporters in the release notes (unless you prefer anonymity).
