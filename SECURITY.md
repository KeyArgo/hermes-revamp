# Security policy

REVamp serves only `127.0.0.1`. Every `/api/*` route except `/api/health` requires the per-install token in `X-Revamp-Token` (or the page’s token query parameter). The token is stored in the REVamp data directory with private file permissions. The browser page has no permissive CORS header.

Please report vulnerabilities privately to the repository maintainers rather than opening a public issue. Include the affected version, a minimal reproduction, impact, and any suggested mitigation. Do not include tokens, private library paths, or personal media metadata.
