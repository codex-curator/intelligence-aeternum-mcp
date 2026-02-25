# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | Yes       |
| < 1.0   | No        |

## Reporting Vulnerabilities

**Email**: security@metavolve.com

Do not open public GitHub issues for security problems. Report via email.

**Response timeline**:
- Acknowledgment: 48 hours
- Assessment: 5 business days
- Fix (critical): 7 business days

## Security Architecture

- **x402 payment verification**: On-chain USDC signature validation on Base L2
- **Stripe webhook verification**: Signature-based webhook authentication
- **Secrets management**: GCP Secret Manager (never in code or env vars)
- **Data isolation**: Per-user Firestore documents with server-side security rules
- **Transport**: HTTPS-only (TLS 1.2+)
- **Infrastructure**: Google Cloud Run with IAM least-privilege service accounts
- **Rate limiting**: Free tools limited to 100 req/min per IP

## Responsible Disclosure

We follow coordinated disclosure and credit responsible reporters in release notes.