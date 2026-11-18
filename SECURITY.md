# Security Policy

## Supported versions

Only the latest release of this integration receives fixes.

## Reporting a vulnerability

Please report security issues privately via
[GitHub security advisories](https://github.com/Videobarista/brightsign-ha/security/advisories/new)
instead of opening a public issue.

## Design notes

- Local network only: no cloud services are contacted.
- Only documented local interfaces are used (DWS REST API, presentation Local Web Server XML,
  UDP commands, passive sync multicast listening). No web interface pages are scraped.
- The local DWS uses a self-signed certificate, so TLS certificate verification is disabled
  for requests to the configured player only.
- Sync multicast traffic is only received, never sent.
