# Security review before launch

This is the security pass that comes before the external penetration test in phase 3 of the [roadmap](roadmap.md). It records what was checked, what was found, what was fixed and what is still open. The standard is OWASP ASVS 4.0 level 2, which is the usual level for an application that holds money and identity documents. Update a row in the same pull request that changes it.

Status is **Met** (checked in the code or configuration named), **Fixed** (a gap found by this review and closed in the pull request named), or **Open** (still to do, with an owner).

## Findings

| # | Finding | Severity | Status |
| --- | --- | --- | --- |
| 1 | The API read a request body of any size into memory. Only the provider callback had a limit, so a large body could tie up memory before any check ran. | Medium | Fixed: `BodyLimitMiddleware` in `backend/app/api/hardening.py` refuses a body over 256 KiB with `payload_too_large`, by its declared length before reading and by counting a body sent without one. A route that takes a file names its path prefix and limit in `BODY_LIMITS`. |
| 2 | API responses carried no `Cache-Control`. A session or balance response could be kept by a browser cache or by a proxy that a future edge change put in front. | Medium | Fixed: every API response carries `Cache-Control: no-store` unless the route sets its own. |
| 3 | Security headers were set only at the edge (Caddy). Staging without the edge, local runs and any later edge change would serve the API without them. | Low | Fixed: `SecurityHeadersMiddleware` adds `nosniff`, a `default-src 'none'` content security policy, `frame-ancestors 'none'`, `X-Frame-Options`, `no-referrer` and `Cross-Origin-Resource-Policy` to every response. The edge keeps its own copy. |
| 4 | The API writes no line per request, so there is no record of status codes and timings to alert on or to give the penetration testers' findings a time line. | Medium | Fixed: `RequestLogMiddleware` writes one `request_served` line per request with its request ID, method, route template, status and duration, never the path as sent or the query string. See [observability.md](observability.md). |
| 5 | There is no edge rate limit. Sign-in, codes and passkeys have attempt limits in PostgreSQL, but any other anonymous route can be called as fast as the connection allows. | Medium | Fixed: one Cloudflare rate limiting rule for the zone (`infra/terraform/shared/main.tf`). An address that sends more than 400 requests in 10 seconds to `/client/*`, `/staff/*` or `/public/*` is blocked for 10 seconds. The Free plan matches on the path only and allows one rule, so it is a flood guard, set high because many people share one carrier address. Provider callbacks are not limited. Takes effect when `shared` is provisioned. |
| 6 | Caddy sets no request body limit, so an oversize body still reaches the API process before it is refused. | Low | Fixed: `request_body { max_size 1MB }` on the API host (`infra/deployment/Caddyfile`), answered with 413 at the edge. `BODY_LIMITS` is empty today; a path added there with a larger limit needs a matching raise in the Caddyfile. The rehearsal checks the 413. |
| 7 | `pnpm audit --prod` reports 1 low and 4 moderate advisories (`deepmerge`, `glob`, `minimatch`), all reached through Expo and Jest build and test tooling. None is in code sent to a browser or phone. | Low | Open: clear them when Expo and Jest release fixed versions. Check again before the test. |
| 8 | Secret scanning with push protection and Dependabot alerts are repository settings, not files. Their state could not be confirmed from the code. | Low | Open: Solo, in the repository's Settings, Code security. |
| 9 | New passwords are checked against a short built-in list, not a full breached-password list as ASVS 2.1.7 asks. | Low | Open: Solo picks the source, behind `too_common`. Recommended: the top one million passwords from a published breach corpus, kept as a hashed file in the image, so nothing about a password leaves the server. |

`make audit` (pip-audit on the locked backend dependencies) found no known vulnerabilities on 2026-11-13.

## Checklist (ASVS 4.0, level 2)

### V2 Authentication

| Requirement | Status | Where |
| --- | --- | --- |
| Passwords of at least 10 characters with no composition rules | Met | `backend/app/modules/identity/domain/passwords.py`, [rules](rules.md) |
| New passwords checked against breached passwords | Partly | A built-in list of the most common leaked passwords and patterns. A full breached-password list is finding 9. |
| Passwords hashed with a memory-hard function | Met | Argon2id, 64 MiB, 3 passes ([decisions](decisions.md)) |
| Attempt limits on password, code and passkey steps, counted before the work is done and shared by every process | Met | `backend/app/infrastructure/throttling.py` |
| The same answer and the same work for an unknown account and a wrong password | Met | `IdentityService.start_login` |
| A second step for every staff account, passkey or authenticator app, never SMS | Met | [sign-in rules](rules.md) |
| An SMS code alone never signs a client in, and a code sign-in limits withdrawals for 24 hours | Met | `restricted_until` in the identity service |
| Authenticator secrets encrypted at rest | Met for staff | `authenticator_key`; client authenticators follow the same `Sealer` (review follow-up) |

### V3 Sessions

| Requirement | Status | Where |
| --- | --- | --- |
| Session cookie `Secure`, `HttpOnly`, `SameSite=Strict` | Met | `backend/app/modules/identity/api.py` |
| Idle and absolute timeouts per audience | Met | `Settings`: client web 15 minutes and 12 hours, staff 30 minutes and 12 hours |
| Sign out ends the session on the server | Met | `/sign-out` |
| A new session on every sign in | Met | `session_opened` |

### V4 Access control

| Requirement | Status | Where |
| --- | --- | --- |
| Deny by default, permissions checked on the server for every staff route | Met | `identity/domain/permissions.py` |
| Two people for money decisions (large payouts, corrections, sign-offs, dealing) | Met | Each module's two-person steps |
| Two admins for every change to staff access: invite, role, deactivation, new set-up link | Met | `identity_staff_changes` and the guard on `identity_staff` (migration 0150), [console-admin.md](console-admin.md#the-team) |
| A client can reach only their own records | To test | Included in the penetration test scope below |

### V5 Validation and V7 Errors and logging

| Requirement | Status | Where |
| --- | --- | --- |
| Every request body is a strict contract that refuses unknown fields | Met | `app/shared/contracts.py` |
| Errors never echo what the caller sent | Met | `app/api/errors.py` |
| Logs carry request IDs and error codes, never personal data or amounts | Met | `app/infrastructure/logging.py`, [rules](rules.md) |
| A request body has a size limit | Fixed | Finding 1 |
| One log line per request with status and duration | Fixed | Finding 4 |

### V8 Data protection and V9 Communications

| Requirement | Status | Where |
| --- | --- | --- |
| Identity details encrypted at field level | Met | `app/infrastructure/sealing.py`, used by KYC and authenticators |
| No caching of responses that hold personal data | Fixed | Finding 2 |
| TLS from the visitor to Cloudflare and from Cloudflare to the origin (Full strict, Origin CA) | Met | `infra/deployment/Caddyfile` |
| Only Cloudflare may reach ports 80 and 443 | Met | DigitalOcean firewall in `infra/terraform/` |
| Verified TLS to the database | Met | `FINVEST_DATABASE_SSL_MODE=verify-full` |

### V10 Code and V14 Configuration

| Requirement | Status | Where |
| --- | --- | --- |
| Locked dependencies, audited on every pull request | Met | `uv.lock`, `make audit` in `backend.yml`, `pnpm-lock.yaml` |
| No secrets in the repository | Met | GitHub Actions secrets; Terraform state only in its private bucket |
| API route catalogue off in deployments | Met | `FINVEST_API_DOCS` defaults to off |
| Security headers on every response | Fixed | Finding 3 |
| Least-privilege database roles (app, worker, migrator) | Met | `backend/db/init-roles.sql` |

### V13 API

| Requirement | Status | Where |
| --- | --- | --- |
| CORS for the exact allowed origins only, with credentials | Met | `create_app` |
| An unsafe cookie request must come from an allowed origin | Met | `check_origin` |
| Provider callbacks are signed, size-limited, and only a hint | Met | `app/modules/payments/api.py` |
| An edge rate limit for anonymous routes | Fixed | Finding 5 |

## Penetration test

### Scope

- `api.finvestug.com`, `app.finvestug.com`, `admin.finvestug.com` and `finvestug.com`, tested on the staging hosts (`api-staging.` and the others) with staging data only.
- The Android and iPhone apps from a staging build.
- Grey box: the testers get the API contract (`/openapi.json` turned on in staging only), one client account per state (new, verified, invested, restricted after a code sign-in) and one staff account per role.
- Priorities: one client reaching another's records, a staff role doing another role's work, getting round a two-person step, replaying or racing a payment, payout or idempotency key, and the sign-in and recovery flows.
- Out of scope: denial of service, Cloudflare and DigitalOcean themselves, the payment provider's own systems, social engineering.

### Before it starts

- Every **Open** finding above is closed or accepted in writing.
- Staging runs the same release as production will.
- Alerts are on, so the test's traffic is visible and the team learns what an attack looks like in the logs.

### What only Solo can provide

- The testing firm, dates and the signed authorisation letter.
- Whether the regulator wants the report, and in what form.
- Who receives the testers' urgent findings during the test.
- Turning on secret scanning, push protection and Dependabot alerts in the repository settings.

Every finding from the test is fixed in its own pull request, retested by the firm, and listed here with the report's reference.
