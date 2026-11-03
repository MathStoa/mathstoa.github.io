# Ongoing maintenance routine

Proposal for running the mathStoa account service on Render, written against
commit `5e1842a`; accept it or amend the cadences and thresholds. Where SETUP.md
gives Docker Compose commands, use the web service's **Shell** tab (working
directory `/app`) or `psql` with the database's external connection string.
Dashboard names as of November 2026. Owner of every row: the operator.

## Checklist

| Cadence | Check | Where | How | Problem if |
|---|---|---|---|---|
| Weekly | Service and deploys | Render → web service → **Events**, **Logs**; `https://math-stoa.org/health/` | Last deploy live, no health-check failures or restarts; `/health/` returns `{"status": "ok"}` (`server/accounts/views.py`). Set it as the Health Check Path under **Settings**. | Failed deploy, repeated restarts, non-200 health response. |
| Weekly | Cleanup runs | Render → **Logs**, filter `Transient cleanup` | `server/accounts/supervisor.py` logs one line per run: `completed or another instance holds the cleanup lock` on success; `failed (exit N)`, `timed out` or `could not start` on failure. The command's own output is discarded. | Any failure line, or no success line for several hours while the service is up. |
| Weekly | Backups | Render → database → **Recovery**, **Backups** | Point-in-time window present (observed: 3 days) and latest export recent; export retention is per the Render plan, recorded below. | Shorter window, missing export, database not "Available". |
| Weekly | Pending submissions | `/admin/submissions/submissionrevision/?status__exact=pending`; the **Moderation queue** count in the site header; the `MODERATION_NOTIFY_EMAIL` mailbox when set | Moderate each pending revision following CONTRIBUTING.md's maintainer workflow: approve, or request changes with a note. A moderator may approve their own project; the decision records who made it. | A revision pending for more than a week, or a notice with no matching pending revision. |
| Weekly | Privacy mailbox | `PUBLIC_CONTACT_EMAIL` mailbox | Log receipt date, action and deadline in the restricted correspondence record; verify files at `/admin/privacy-requests/verify/` (PRIVACY_REQUESTS.md). UK GDPR: one month from receipt, extendable by two months for complex requests if the requester is told. | A request unacknowledged after a week, or within a week of its deadline. |
| Weekly | CI and dependencies | GitHub → **Actions**, **Security** | Latest run on `main` green, including `pip-audit --strict` (`.github/workflows/test.yml`). It runs only on push and there is no Dependabot configuration; proposed: enable Dependabot alerts or add a weekly `schedule` trigger. | Red run on `main`, open alert, or a Django/allauth security release not deployed within a week. |
| Monthly | Cleanup preview | Render → **Shell** | `python server/manage.py purge_transient_data --dry-run` prints counts. "Skipped" means the hourly run holds the lock; retry. | Hundreds of `sessions` or `cache_entries` (hourly cleanup failing), or unexpected account or draft counts. |
| Monthly | Database storage | Render → database → **Metrics**; `psql` | Compare usage with the plan limit; `SELECT count(*) FILTER (WHERE expires >= now()), count(*) FROM mathstoa_cache;`. The security cache never evicts unexpired counters (`server/accounts/security_cache.py`), so a flood grows the table for up to an hour. | Usage above 70% of the plan, thousands of unexpired rows, or expired rows surviving an hour. |
| Quarterly | MFA and recovery codes | `/accounts/2fa/` (recent MFA required); Render, GitHub, ORCID, GitLab, Porkbun and mailbox account settings | Regenerate at three or fewer unused codes; store offline (STAFF_SECURITY.md); confirm the authenticator app is backed up; once a year sign in with one code, then regenerate. MFA on for every platform account, with recovery codes in the password manager; Render team and GitHub collaborators are only the operator. | Fewer than three codes, codes kept only on the authenticator device, a rejected code, an account without MFA, or an unknown member. |
| Quarterly | Certificate and domain | Render → web service → **Settings → Custom Domains**; Porkbun | Render renews certificates automatically once the domain is verified: check it shows verified and `openssl s_client -connect math-stoa.org:443 -servername math-stoa.org </dev/null` reports expiry over 30 days away. Porkbun: auto-renew and registrar lock on, DNS unchanged. | Browser warning, "pending" domain, expiry under 30 days, renewal under 60 days away, unexpected DNS record. |
| Quarterly; secrets yearly | Provider applications | ORCID developer tools; GitHub → Developer settings → OAuth Apps; GitLab → User Settings → Applications; Render → **Environment** | Callbacks match SETUP.md's production table; `python server/manage.py check_login_setup` prints them; sign in once with each provider. Yearly: new client secret (GitHub allows two active secrets), update Render, redeploy, test sign-in, delete the old one; same for `REPOSITORY_GITHUB_READ_TOKEN`. | Provider error, callback still on `onrender.com`, secret older than 12 months, or a rotation not rolled back within the hour. |
| Yearly, and whenever processing changes | Privacy records | RECORD_OF_PROCESSING.md; `server/templates/privacy.html`; breach register | Activities, processors, transfer safeguards and the legitimate interests assessment still match the code and providers; update the notice's "Last updated" date with any change; both controllers agree. | A new provider, field or use not in the record, or a notice older than the code it describes. |
| Six-monthly, and after a major upgrade, PostgreSQL version change, key rotation or plan/region change | Restoration re-test | Separate Render database and web service | Restore a Render backup or an off-platform dump into an isolated database and service with the same `DJANGO_SECRET_KEY`; `migrate --check`; sign in to `/admin/` with MFA (proves MFA data decrypts); delete the copy. | Restore fails, MFA cannot decrypt, copy left running. |

## Additional encrypted off-platform backup

**What Render covers.** Point-in-time recovery for the last 3 days (observed) and
periodic exports with plan-dependent retention, both inside the same Render
account. They cover a bad migration or deletion noticed within days, not a
Render account compromise or suspension, a provider-side loss, or a mistake
noticed later. Every backup also needs `DJANGO_SECRET_KEY`
(`server/accounts/security_crypto.py`), which stays in the password manager.

**Minimal approach.** Monthly, from the operator's machine:
`pg_dump --format=custom "$DATABASE_URL" | age -r <backup public key> > mathstoa-YYYY-MM-DD.dump.age`
(or GPG), using a dedicated read-only database role over TLS. Keep
the `age` private key in the password manager, never beside the dumps. Store them on an
encrypted external drive and in a bucket at a different provider, never only on
the laptop. A GitHub Actions cron with `DATABASE_URL` as a repository secret is
the hands-off variant but still needs its own bucket and credential: the
repository is public, hosted runners have no fixed IP, and artifacts are
unsuitable.

**What it adds.** A second copy of personal data on its own retention clock (the
erasure record in PRIVACY_REQUESTS.md must cover it), a standing database
credential outside Render, and another restoration path to test.

**Recommendation.** Adopt the local monthly dump with a read-only role, keep the
last three, and restore one in each six-monthly re-test. Leave the GitHub
Actions variant until a second operator needs automation.

## Incident checklist

**Suspected key or hosting compromise (Render, GitHub or `DJANGO_SECRET_KEY`).**

1. Set a new 50+ character `DJANGO_SECRET_KEY`. If the old key may be exposed,
   give no fallback; `DJANGO_SECRET_KEY_FALLBACKS` is for planned rotations only
   (STAFF_SECURITY.md). Redeploy. Without a fallback
   every session is unreadable (everyone is signed out), MFA records cannot be
   decrypted, rate-limit counters restart and privacy-request proofs are void.
2. Revoke sessions explicitly anyway: in the Shell,
   `python server/manage.py shell -c "from django.contrib.sessions.models import Session; Session.objects.all().delete()"`.
3. Re-enrol MFA. A staff account whose TOTP record used the replaced key is
   locked out of `/admin/` until that record is removed: in the Django shell
   delete the user's `allauth.mfa.models.Authenticator` rows (select by
   `user__socialaccount__provider` and `uid`), sign in, enrol within five
   minutes and regenerate recovery codes. There is deliberately no
   admin screen for this.
4. Rotate in Render → Environment: the three provider client secrets,
   `REPOSITORY_GITHUB_READ_TOKEN`, and the database credential (check Render's
   current options; otherwise restrict the database's access-control list),
   plus Render API keys, deploy hooks and GitHub tokens. Then check Render team
   members, GitHub collaborators, OAuth app authorizations and Porkbun DNS.
5. Record what was reachable and when. If personal data was likely exposed,
   assess UK GDPR notification: the ICO within 72 hours of becoming aware where
   individuals are at risk; the individuals without undue delay where the
   risk is high.

**Breach handling.** A personal data breach is any accidental or unlawful
loss, alteration, disclosure of or access to personal data, including an
availability loss such as a failed restore. Whoever notices one tells the
other controller at once (RECORD_OF_PROCESSING.md). Tommaso Rosati decides,
within 72 hours of either controller becoming aware, whether it is likely to
risk people's rights and freedoms; if so, report it to the ICO
(ico.org.uk/for-organisations/report-a-breach, or 0303 123 1113), and if the
risk is high, tell the people affected without undue delay. Record **every**
breach, reported or not, in the breach register: a restricted spreadsheet
kept with the privacy correspondence record, never in this public
repository. Columns: date and time noticed; date and time it happened, if
known; what happened; data and approximate number of people affected; likely
consequences; containment and remedial steps; ICO decision, with reasons and
reference; whether the people affected were told, when and how; who handled
it; date closed. Review the register at each quarterly check, even if empty.

**Provider outage (ORCID, GitHub or GitLab).** Sign-in with that provider fails;
accounts with another connected method still work, and submission and approval
fail closed while the GitHub API is unavailable
(`server/repository_verification/services.py`). Keep the credentials in place
(removing them hides the button and changes disconnection rules) and retry later. If the outage is a provider security
incident, rotate that client secret.

**Lost operator MFA.** With a recovery code: sign in, use it at the challenge,
regenerate the set, re-enrol TOTP if the device is gone. Without one: remove the
operator's `Authenticator` rows and sessions as in step 3, sign in fresh, and
`/admin/` redirects to enrolment. This needs the Render account, so Render's own
recovery codes are the root of trust and belong in the password manager. Nobody else can do this today.

## Retention summary

| Data | Rule | Mechanism |
|---|---|---|
| Sessions | 14 days from sign-in; expired rows removed hourly | `SESSION_COOKIE_AGE`; `purge_transient_data` |
| Rate-limit counters | Expire with their window, at most an hour; expired rows removed hourly | `server/accounts/security_cache.py`; cleanup |
| Verification attempts | Challenge valid 24 h (file) or 10 min (GitHub); unused expired attempts removed after 30 days | `server/repository_verification/management/commands/purge_repository_verification_attempts.py` |
| Verification evidence | Valid 30 days; kept with submission history; removed only with an unused draft | `server/accounts/retention.py` |
| Unverified sign-ups | 7 days with no sign-in, verified email or provider identity | `retention.py` |
| Never-submitted drafts | 90 days without saved or verification activity | `retention.py` |
| Accounts without responsibilities | 365 days after creation or last sign-in, whichever is later | `retention.py` |
| Submitted revisions, moderation decisions, published records | No automatic deletion; by request only | PRIVACY_REQUESTS.md |
| Render backups and logs, and Cloudflare edge logs | Point-in-time recovery 3 days (observed); exports and platform logs per the Render plan; edge-network logs (IP addresses, full URLs) under the provider's own retention. Record both here: ____ | Render |
| Off-platform dumps (proposed) | Last three monthly dumps, about 90 days | Operator |
| Privacy correspondence (proposed) | Delete request files and exports at closure; keep date, action and outcome two years | Mailbox; restricted record |
