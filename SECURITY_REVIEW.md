# Security review summary

Date: 2026-11-03. Commit: `5e1842a` on `main` ("Add private account exports and
verified privacy requests"). Prepared for the operator from the repository at
that commit.

**[code]** marks statements from reading the code, tests and CI configuration.
**[live]** marks checks that must be made against the running service; unless
listed as done, they have not been made. This records what was checked and what
was not. It is not a statement that the service is secure.

## Scope and method

- Code review of the Django service in `server/`, the deployment files
  (`Dockerfile`, `compose.yaml`, `.github/workflows/test.yml`) and the operator
  documents (SETUP.md, STAFF_SECURITY.md, PRIVACY_REQUESTS.md,
  `server/repository_verification/README.md`). [code]
- Automated tests: CI runs
  `python server/manage.py test accounts submissions repository_verification --settings=config.test_settings`
  on every push against PostgreSQL 17 (Python 3.12) and SQLite (Python 3.13),
  plus `makemigrations --check`, `collectstatic` and the archive validator. The
  run for this commit (GitHub Actions run 41672552786) passed. The default test
  cache is local memory (`server/config/test_settings.py`);
  `server/accounts/test_security_cache.py` exercises the database backend
  explicitly. Providers are mocked. [code]
- Dependency scan: `pip-audit==2.10.1 --strict` over `server/requirements.txt`
  (Django 5.2.18, django-allauth 65.21.1, cryptography 50.0.2, gunicorn 23.0.0,
  psycopg 3.3.7, whitenoise 6.12.0, dj-database-url 3.1.2) passed in the same
  run. It runs on push only; no scheduled scan or Dependabot configuration
  exists. [code]
- Live checks done: restoration of the database and master key into an isolated
  service. Everything in the open-items list remains. [live]
- Not reviewed: Django and allauth internals, Render's platform, edge network and proxy, TLS
  configuration, browser behaviour of the CSP, email sign-in and institutional
  OIDC (both disabled), the static archive's JavaScript, behaviour under floods,
  and the legacy GitHub issue-template route. No penetration test was performed.

## Controls in place [code]

| Control | Implementation | Tests |
|---|---|---|
| Owner access | Drafts visible only to their owner (`accessible()`: `server/submissions/views.py`); review needs an active staff user with `submissions.change_submissionrevision` (`SubmissionRevision.review`). | `server/submissions/tests.py` |
| Fixed submitted revisions | `SubmissionRevision.save` rejects changes to a snapshot; admin is read-only with no add or delete (`server/submissions/admin.py`); approval validates the frozen snapshot with `scripts/validate.py` under row locks. | `server/submissions/tests.py` |
| Repository-control verification | `require_verified` runs inside the locked submit and approve transactions and re-fetches public metadata (`server/repository_verification/services.py`, `github.py`). Limits: public github.com only; attests authority at `verified_at` for at most 30 days; cannot see a later role removal; GitLab repositories not verifiable. | `server/repository_verification/test_services.py`, `test_views.py`, `test_github.py` |
| CSRF and OAuth state | `CsrfViewMiddleware`; allauth state, with PKCE for GitHub, GitLab and OIDC (`server/config/settings.py`); client-supplied `scope` and `auth_params` rejected on login routes (`server/accounts/middleware.py`); ORCID iD taken only from the server-side token exchange (`server/accounts/orcid.py`). | `server/accounts/test_oauth_parameters.py` |
| Mandatory staff MFA, 15-minute rule | Every `/admin/` request needs an active staff user, an enrolled TOTP and a TOTP or recovery-code challenge in this session within `STAFF_MFA_MAX_AGE` (900 s); a privileged POST is refused, not replayed, after a challenge; staff cannot delete their last TOTP; no password path exists (`server/accounts/staff_mfa.py`, `mfa_adapter.py`, `backends.py`). | `server/accounts/test_staff_security.py`, `tests.py` |
| Fresh sign-in for account changes | Sign-in method and email changes, MFA enrolment and privacy downloads need a first-factor sign-in within 300 s, rechecked at the OAuth callback (`staff_mfa.py`, `adapters.py`, `privacy_views.py`). | `test_author_mfa_security.py`, `test_privacy_tools.py` |
| Encrypted session and MFA contents | Fernet keys derived by HKDF-SHA256 from `DJANGO_SECRET_KEY` for the purposes `sessions` and `mfa`, fallbacks via MultiFernet, plaintext and legacy signed payloads rejected (`server/accounts/security_crypto.py`, `sessions.py`, `mfa_adapter.py`); production refuses keys under 50 characters. | `test_staff_security.py`, `test_settings.py` |
| Minimal scopes, stripped profiles | ORCID `/authenticate`, GitHub none, GitLab `read_user`, OIDC `openid`; tokens not stored; `extra_data`, emails and phone cleared on every sign-in; random usernames (`settings.py`, `adapters.py`, `orcid.py`). | `tests.py`, `test_orcid_privacy.py` |
| Rate limits and security cache | allauth limits for admin login (socket address and account), submissions, verification and privacy actions (`ACCOUNT_RATE_LIMITS`; `middleware.py`); database cache that never evicts live counters and fails closed on a failed write; HMAC-hashed keys (`security_cache.py`, `cache.py`). | `test_limits.py`, `test_security_cache.py`, `server/submissions/test_limits.py` |
| Retention cleanup | Hourly supervised `purge_transient_data` under a PostgreSQL advisory lock with the 7/90/365-day rules (`retention.py`, `maintenance.py`, `supervisor.py`, `run_web.py`). | `test_retention.py`, `test_supervisor.py` |
| Privacy exports and verified requests | Self-export and signed request files bound to account, request digest and security state for seven days; stateless verification needs staff, recent MFA and `auth.view_user` (`privacy_services.py`, `privacy_views.py`). | `test_privacy_tools.py` |
| Log minimisation | Gunicorn access log carries time, method, status and size only; cleanup output discarded; privacy and verification code marked sensitive for error reports (`run_web.py`, `supervisor.py`). Render's proxy logs are outside this control [live]. | `test_supervisor.py` |
| Security headers | HTTPS redirect, secure cookies, HSTS one year without subdomains or preload (deliberate; Django W005 and W021 expected), nosniff, `Referrer-Policy: same-origin`, `X-Frame-Options: DENY`, CSP with a `form-action` allow-list and `frame-ancestors 'none'`, no-store on account routes (`settings.py`, `middleware.py`). HSTS and the redirect work only if `DJANGO_SECURE_PROXY_SSL_HEADER` matches Render's proxy [live]. | CSP in `tests.py`; HSTS by settings review only |
| Registration closed by default | Sign-up needs `PUBLIC_REGISTRATION_ENABLED=true` and a `PUBLIC_CONTACT_EMAIL` in production (`policy.py`, `adapters.py`, `.env.example`). | `test_privacy_controls.py`, `test_settings.py` |

## Personal data retained [code]

| Data | Where |
|---|---|
| Login identifiers: random username, provider and UID, optional email, creation and last sign-in dates | `auth_user`, `socialaccount_socialaccount`, `account_emailaddress` |
| Session rows: ID, expiry and Fernet-encrypted payload (sign-in records, pending codes, OAuth state) | `django_session` |
| MFA: encrypted TOTP secret and recovery seed with used-code mask, type, creation and last-use times | `mfa_authenticator` |
| Private drafts; collaborator attribution typed by submitters (third parties' names, ORCID iDs, affiliations) | `submissions_submission` |
| Submitted snapshots, submitter, reviewer, review notes and dates; no automatic deletion | `submissions_submissionrevision` |
| Verification evidence: GitHub user ID, login and role or commit SHA, repository and owner IDs, ORCID; attempt token hashes | `repository_verification_*` |
| Rate-limit counters under HMAC keys, at most an hour | `mathstoa_cache` |
| Request logs at Render and its edge network (may include IP addresses and full URLs), point-in-time recovery (3 days) and exports (per plan) [live] | Render |
| Request files (account ID, request text, reply address) and correspondence | Operator mailbox, restricted record |
| Published attribution, public by design | `docs/data/projects.json`, approved revisions |

## Remaining risks

| Risk | Likelihood / impact | Mitigation or accepted |
|---|---|---|
| Phishable TOTP and recovery codes | Medium / high for staff | 15-minute re-challenge, fresh sign-in, no password path. Accepted until WebAuthn after the domain is final. |
| Hosting or application account compromise (Render, GitHub) | Low / critical: database plus master key exposes everything above | Platform MFA, single member, incident checklist in OPERATIONS.md. Accepted; no external key manager. |
| Bearer invitation links | Retired | Editor invitations were withdrawn on 2026-11-03 and their tables dropped the same day; no invitation or editor record remains. |
| Point-in-time repository proof | Medium / low to medium | Rechecks catch transfers and privacy changes, not role removal within 30 days; staff review. Accepted. |
| Session-ID replay until expiry | Low / medium | HttpOnly, Secure, SameSite=Lax, 14-day limit, revocable by row deletion. Accepted. |
| Security-cache growth under floods | Low / medium, availability | Hourly expired-row cleanup, monthly storage check. Accepted by design. |
| Single operator | Medium over time / high | Nobody else can reset MFA, rotate keys or answer requests; password manager and written procedures. Accepted for the pilot; a second trusted operator recommended. |
| DNS or registrar compromise (Porkbun) | Low / high | Registrar MFA and lock, quarterly DNS check [live]. Accepted. |
| Dependency supply chain | Low to medium / high | Pinned versions; pip-audit on push only; enable Dependabot alerts or a scheduled scan. Partly mitigated. |
| GitLab repositories not verifiable | Certain / low | Documented; GitLab users sign in but cannot publish GitLab repositories. Accepted. |
| Credentials bound to `onrender.com` | Certain at cutover / low | Affects WebAuthn (none yet), cookies and provider callbacks, not TOTP; re-test sign-in at math-stoa.org [live]. |
| No WebAuthn | Certain / medium | Planned after cutover; needs two keys or a recovery process first. |
| Platform and edge logs may hold callback codes | Unknown / medium [live] | Check Render's log fields and retention; SETUP.md warns of this. |

## Open items before public registration [live]

1. DNS cutover at Porkbun and HTTPS check at `https://math-stoa.org/`:
   certificate, HSTS header, no redirect loop, `/health/` ok.
2. Live sign-in re-test with ORCID, GitHub and GitLab at the final domain and
   callbacks.
3. Live author test: draft, both verification methods, submission, approval,
   public listing and feed.
4. Privacy mailbox test: a request file from `/accounts/privacy/` sent to
   `PUBLIC_CONTACT_EMAIL` and verified at `/admin/privacy-requests/verify/`.
5. Recovery-code sign-in test.
6. MFA on Render, GitHub, ORCID, GitLab, Porkbun and the mailbox.
7. Confirmation that `DJANGO_SECRET_KEY` is in the password manager and decrypts
   the restored copy.
8. Maintenance routine (OPERATIONS.md) agreed, with backup and log retention
   recorded from the Render dashboard.
9. Privacy wording in `server/templates/privacy.html` confirmed against
   practice, including backup retention.

Only then set `PUBLIC_REGISTRATION_ENABLED=true`.
