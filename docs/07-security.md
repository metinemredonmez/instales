# Security posture (audited 2026-09-16 against OWASP ASVS 5.0 · NIST SP 800-63B-4)

Reference: Emre's internal guides (`~/Desktop/siber/*.pdf`, 18-area checklist). Findings below use the
same status vocabulary: **Giderildi** (fixed + tested), **Güvenceye alındı** (fail-closed guard), **Temiz**, **Doğrulanacak**.

| # | Alan | Bulgu | Durum | Nerede |
|---|------|-------|-------|--------|
| S1 | 7.12 / B1 | Rate limiter trusted `X-Forwarded-For` from anyone (CWE-290) | Giderildi | `hardening.client_ip` — XFF only from `trusted_proxies`, last hop |
| S2 | 7.17 / A5 | Login limited per IP only | Giderildi | per-account lockout (8 / 15 min) + per-IP + global cap; 429 with Retry-After |
| S3 | 7.8 | Session token in URL for SSE/audio → access logs | Giderildi | 5-min `scope=ticket` tokens (`POST /auth/ticket`), only accepted by those two routes; session tokens refused in query |
| S4 | 7.8 / 7.9 | No revocation: password/role change did not invalidate tokens | Giderildi | `users.token_version` in JWT `ver`; bumped on password change, role/active change, logout-all |
| S5 | 7.10 | JWT lacked `iss/aud/jti`, no `require` set | Giderildi | claims + `issuer/audience` verification, HS256 only |
| S6 | 7.11 | Production accepted any non-default secret | Güvenceye alındı | boot refuses secrets shorter than 32 chars |
| S7 | 7.7 / NIST | Only length ≥ 8 was checked | Giderildi | max 128, blocks e-mail/product words, repetition; HIBP k-anonymity breach check (fail-open) |
| S8 | 7.7 | No password change / logout-everywhere | Giderildi | `POST /auth/password`, `POST /auth/logout-all`, UI in Alarmlar → Hesap güvenliği |
| S9 | 7.9 | Admin could demote/deactivate self or the last admin | Giderildi | guarded; access changes revoke the target's tokens immediately |
| S10 | 7.15 | No audit trail | Giderildi | `audit_events` (login ok/fail/locked, password, role/plan) + Admin → Veri & pipeline |
| S11 | 7.17 | Paid AI endpoints unbounded per user | Giderildi | `ai_requests_per_hour` per user; forced refresh admin-only |
| S12 | 7.17 | `limit` / `poll_seconds` unbounded | Giderildi | bounded Query params |
| S13 | 7.2 | Search text acted as LIKE wildcards | Giderildi | escaped `% _ \` |
| S14 | 7.13 | `/docs`, `/openapi.json` public in production | Giderildi | disabled when `INSTILENS_ENVIRONMENT=production` |
| S15 | 7.4 / 7.12 | SPA had no CSP; API headers minimal | Giderildi | `infra/pm2/harden-nginx.sh` (CSP, HSTS, Permissions-Policy, no-query access log); API adds COOP/CORP/Permissions-Policy |
| S16 | 7.13 | No dependency/secret scanning | Giderildi | `.github/workflows/ci.yml`: ruff, pytest, tsc, build, pip-audit, npm audit, gitleaks |
| S17 | 7.14 | Session token in `localStorage` | Kabul edildi (kalan risk) | needed for Tauri/mobile bearer flow; mitigated by CSP, 7-day TTL, revocation; move to httpOnly cookie for web-only later |
| S18 | 7.16 | SSRF | Temiz | no user-controlled URLs are fetched; feeds/APIs are fixed hosts |
| S19 | 7.5 | BOLA | Temiz | every user-data query is owner-scoped; admin router gated |
| S20 | 7.7 | MFA | Giderildi | TOTP (pyotp) for any account, enforced on login AND on password reset; setup/enable/disable in Ayarlar → Hesap güvenliği; rate-limited /mfa/verify |
| S21 | 7.7 | Password reset / e-mail verification | Giderildi | single-use hashed tokens (30 min), always-200 /forgot, verification link at registration |
| S22 | 7.17 | Limiter state per process | Giderildi | rate_hits table shared across uvicorn workers, fail-closed lockout on DB error; pipeline run lock row |

Re-test: `uv run pytest -q` (auth tests cover S2–S5, S7–S10). Operational: run `harden-nginx.sh` once after deploy.
