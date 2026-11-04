# Repository authorization

The submission owner chooses either a verification file or a one-time GitHub
connection. Both establish repository authorization. Neither establishes
authorship or intellectual-property ownership. An authorized maintainer can
merge the file for the submitter. ORCID sign-in remains independent of this check.

The verification overview offers both methods to personal and approved organisation
accounts, including when a file check is already in progress. Each method opens its
own instructions page, with a link back to change methods. Visiting these pages
preserves an active file challenge; generating a new file or starting the GitHub
check replaces any unfinished attempt. File-check errors stay on the file page,
and GitHub-check errors stay on the GitHub page.

The initial release accepts **public github.com repositories only**. A fork may
be submitted as its own repository; its proof cannot verify the upstream project.
GitLab and other account providers can still be used to sign in, but repositories
on other hosts need a future provider implementation.

## Methods

- File: a random 256-bit challenge expires after 24 hours. The owner copies the
  complete JSON document to `.mathstoa-verification` at the repository root on
  the default branch. The check resolves that branch to a commit, inspects the
  Git tree, rejects symlinks and submodules, then reads the pinned blob. Another
  fork, branch, pull request, account, submission, or old challenge cannot satisfy
  the proof. Reissuing either method revokes previous unfinished attempts.
- GitHub: an authenticated owner starts a CSRF-protected POST. Existing allauth
  OAuth state and PKCE carry a short-lived account/submission-bound challenge.
  The callback uses the user's transient token to obtain the immutable GitHub
  user ID and repository permissions. Personal repositories require their owner;
  organization repositories require explicit admin or maintain permission.
  Missing permissions fail closed and offer the file method. No OAuth scopes
  are requested: GitHub permits public profile and repository reads without a
  scope. Private profile fields and repository-write access are not requested. This one-time verification records the
  GitHub identity as evidence; it does not add a GitHub sign-in method or save an
  OAuth credential. Ordinary account connections remain independent.

## Integration and policy

`verification:overview` accepts a submission UUID. Add the application and URL
include in the Django configuration. `has_current_verification` is a display
hint. **Every submission/moderation transition must call `require_verified` inside
the same database transaction that locks and transitions the submission.** The
gate locks proof evidence too, serializing concurrent identity revocation.
Callers continue to enforce owner/moderator permissions themselves.
The Django admin shows the latest proof on each submitted revision and lists
completed proofs read-only; attempts and token digests are never shown.

Proof is bound to the local submission owner, authenticated ORCID (if connected),
exact repository URL, immutable repository ID and immutable owner ID. It stores
the method and verification time, plus the file commit or GitHub identity/role.
Repository URL/owner changes and ORCID changes revoke local proof; restoring an
old URL does not restore it. Submit and approve fetch public metadata again and
fail safely on transfer, replacement, private/disabled repository, or provider
outage. Approval must also compare the current URL with the immutable revision.

Evidence is valid for 30 days by default (`REPOSITORY_VERIFICATION_VALID_DAYS`).
It attests authority at `verified_at`, not continuous authority: without keeping
the submitter's token, subsequent permission removal cannot be detected by
public metadata checks. After expiry the owner must complete a new check before
submission or publication. Existing published revisions are historical records.

## Deployment

Use the existing GitHub OAuth client configuration and callback, with
`OAUTH_PKCE_ENABLED=True` and `SOCIALACCOUNT_STORE_TOKENS=False`. Provider
credentials are required to enable the GitHub button; file verification works
without a submitting-user GitHub connection. PostgreSQL is required in production
for transactional submission and proof locking; local SQLite tests do not
establish PostgreSQL concurrency behavior.

Configure `REPOSITORY_GITHUB_READ_TOKEN` on the server for production-scale
public metadata/content reads. Use a separately managed credential with only
the needed read access. It never substitutes for the submitting user's token
when checking user permissions. Without it, all users share GitHub's anonymous
IP quota, and a file check uses several requests. Rate-limit errors are recoverable
and never mark a submission verified. The UI limits verification work across
methods using allauth's shared per-user and per-submission counters:
`ACCOUNT_RATE_LIMITS['repository_verification'] = '5/m/user,10/h/user,5/m/key,10/h/key'`.

Keep sessions and the allauth rate-limit cache in shared server-side storage.
File challenges must not be stored in browser-readable session cookies. Private
verification responses use no-store; OAuth tokens are excluded from persistence
and production exception reports. Never log tokens, generated file contents,
or callback request parameters.

Cleanup is part of `purge_transient_data` (see `accounts/maintenance.py`), which
the standard deployment runs at startup and hourly. It removes unused attempts
that expired over 30 days ago, along with expired sessions and other transient
data, and preserves proof evidence. Preview the counts with
`python manage.py purge_transient_data --dry-run` from `server/`.

Run tests from `server/` with
`python manage.py test repository_verification --settings=config.test_settings`.
Tests use mocked provider responses and do not authorize a real GitHub account.
Before public launch, exercise both methods with the registered provider client
and the production PostgreSQL database.
