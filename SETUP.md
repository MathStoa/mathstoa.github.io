# Running MathStoa accounts and submissions

The Django application serves the public site, sign-in, and project submissions
from the same origin. It registers links to public repositories; it does not
upload or mirror repository files. Code can remain in
[`MathStoa/mathstoa.github.io`](https://github.com/MathStoa/mathstoa.github.io).
GitHub Pages cannot run the account service or its database.

The application in this repository is not evidence of a live deployment. The
existing public site continues to use its current host until a production server,
provider credentials, email delivery, and DNS have been configured and tested.

## Local preview

Use Python 3.12 and a virtual environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r server/requirements.txt
export DJANGO_DEBUG=1
export DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
export PUBLIC_BASE_URL=http://localhost:8000
export EMAIL_LOGIN_ENABLED=true
python server/manage.py migrate
python server/manage.py createcachetable
python server/manage.py runserver
```

Open <http://localhost:8000/accounts/login/>. Development uses
`server/db.sqlite3` and prints sign-in email to the terminal. Read the one-time
code there. This demonstrates the workflow; it does not deliver email or verify
that you control the typed address. Never use console email for a public service.

To create an administrator account:

```sh
python server/manage.py createsuperuser
```

Use <http://localhost:8000/admin/> for administration and
<http://localhost:8000/submissions/> for your submissions. One-time codes expire
after three minutes and allow at most three attempts. Sessions expire after
fourteen days.

## Production configuration

Copy `.env.example` to `.env`. Docker Compose reads this file; for a manual
deployment, set the same variables in your process manager's environment.
Generate two different secrets and fill `DJANGO_SECRET_KEY` and
`POSTGRES_PASSWORD` with their respective values:

```sh
python -c 'import secrets; print(secrets.token_urlsafe(48))'
python -c 'import secrets; print(secrets.token_hex(32))'
chmod 600 .env
```

Do not commit `.env`, database dumps, provider secrets, or SMTP credentials. Keep
`DJANGO_DEBUG=0` in production. Set the public domain in `DJANGO_ALLOWED_HOSTS`,
the full HTTPS origin in `DJANGO_CSRF_TRUSTED_ORIGINS`, and the canonical HTTPS
URL in `PUBLIC_BASE_URL`. Comma-separate multiple hosts or origins if needed.

Production uses PostgreSQL. Compose creates a persistent database volume and
sets `DATABASE_URL` itself. Outside Compose, provide a PostgreSQL connection URL
through `DATABASE_URL`; URL-encode special characters in its credentials. A
random hex database password avoids this problem in the supplied Compose file.

For email sign-in, set `EMAIL_LOGIN_ENABLED=true` and configure a real SMTP
service with `EMAIL_HOST`, `EMAIL_PORT`,
`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and `DEFAULT_FROM_EMAIL`. Set the sender
address to one your mail service authorizes. Use either STARTTLS
(`EMAIL_USE_TLS=true`, usually port 587) or implicit TLS (`EMAIL_USE_SSL=true`,
usually port 465), according to that service. Do not enable both. Verify actual
delivery to an external mailbox before launch. Set `EMAIL_LOGIN_ENABLED=false`
only if a working alternative sign-in provider is configured. The production
template leaves email sign-in disabled until you configure it; leaving both
email and all providers disabled means no user sign-in is available.

Set `PUBLIC_CONTACT_EMAIL` to a monitored mailbox for privacy and account
questions. This address is public; submitters' private contact addresses are not
published as part of their listings.

## Register sign-in providers

Register a separate application with each provider you want to enable. The
credentials below must be issued to MathStoa by that provider; this repository
does not include them. Leave unconfigured provider credentials empty.

| Provider | Environment variables | Production callback URL |
| --- | --- | --- |
| ORCID | `ORCID_CLIENT_ID`, `ORCID_CLIENT_SECRET`, `ORCID_SANDBOX=false` | `https://math-stoa.org/accounts/orcid/login/callback/` |
| GitHub | `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | `https://math-stoa.org/accounts/github/login/callback/` |
| GitLab.com | `GITLAB_CLIENT_ID`, `GITLAB_CLIENT_SECRET` | `https://math-stoa.org/accounts/gitlab/login/callback/` |
| Institution or identity broker | `INSTITUTION_CLIENT_ID`, `INSTITUTION_CLIENT_SECRET`, `INSTITUTION_SERVER_URL`, `INSTITUTION_NAME` | `https://math-stoa.org/accounts/oidc/institution/login/callback/` |

Use the exact callback, including the final slash. Replace the origin when
testing on a different host, and register that testing callback with the
provider. Prefer separate development and production applications. ORCID's
sandbox has separate accounts and credentials: use `ORCID_SANDBOX=true` only
with sandbox credentials. The GitLab integration targets GitLab.com, not
arbitrary self-hosted GitLab instances.

Institutional sign-in requires an agreement and configuration with the
institution or its identity broker. `INSTITUTION_SERVER_URL` must be the OpenID
Connect issuer root without a trailing slash, or its full HTTPS discovery URL.
The full URL ending in `/.well-known/openid-configuration` is the clearest
choice; obtain the exact value from the identity administrator. The configured
provider ID is `institution`. This button does
not automatically admit accounts from every university. Access through eduGAIN
usually needs a suitable federation arrangement or broker that exposes OpenID
Connect to MathStoa; it is not enabled simply by adding an eduGAIN URL.

If the institution's authorization endpoint has a different origin from its
discovery URL, set `INSTITUTION_AUTHORIZATION_ORIGIN` to that exact HTTPS origin
(without a path or trailing slash). The browser's form policy permits redirects
only to the configured sign-in origins.

Provider setup references: [ORCID authentication](https://info.orcid.org/documentation/api-tutorials/api-tutorial-get-and-authenticated-orcid-id/),
[GitHub OAuth applications](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app),
[GitLab OAuth provider](https://docs.gitlab.com/integration/oauth_provider/),
and [django-allauth OpenID Connect](https://docs.allauth.org/en/latest/socialaccount/providers/openid_connect.html).

## Verify control of a submitted repository

Sign-in identifies a MathStoa account; it does not prove that the account can
manage the repository named in a submission. Before submission and approval,
the service requires repository-control verification. The initial verification
implementation supports public GitHub repositories, using a verification file
committed to the repository or a separate GitHub authorisation check. Any token
used for that check is transient, not retained as an account credential.

GitLab sign-in remains an account option, but it does not enable verified
publication of GitLab repositories. Repositories on other hosts cannot be
published until a corresponding verification method is implemented. Verification
also does not establish agreement from all collaborators or mathematical
correctness; the submitter's declaration and staff review still apply.

## Start the production service

Install Docker and the Docker Compose plugin on the application host, then run:

```sh
docker compose config --quiet
docker compose up --build -d
docker compose exec app python server/manage.py createsuperuser
docker compose exec app python server/manage.py check --deploy
docker compose exec app python server/manage.py check_login_setup --require-all
```

The application starts only after PostgreSQL is ready. Startup applies database
migrations, creates the shared database cache table, collects static assets into
`server/staticfiles`, and starts Gunicorn. The image runs the application as an
unprivileged user. The supplied startup command assumes one application
container; with multiple replicas, run migrations once in a separate release job.

The app listens on host address `127.0.0.1:8000`, and PostgreSQL is not exposed
on a host port. Put an HTTPS reverse proxy on this host in front of the app. Set
the correct `Host` header. Only if that proxy removes incoming
`X-Forwarded-Proto` values and replaces them with the protocol it actually served,
set `DJANGO_SECURE_PROXY_SSL_HEADER=true`. An untrusted client must not be able
to choose that header. A managed host must provide equivalent trusted proxy
behaviour. Resolve every relevant deployment-check warning before launch.
The supplied policy deliberately does not opt all subdomains into HSTS or
request browser preloading; Django reports W005 and W021 for these choices.
Only enable those policies after confirming the domain-wide HTTPS arrangements.

The public archive and the account routes must reach the same Django service.
After testing HTTPS, sign-in, email delivery, submission, administration, and
static pages on the target host, change the domain's DNS to that host. Keep the
existing site available until the replacement is ready. This setup does not
change DNS, buy hosting, or register provider applications. `docs/CNAME` may
remain in the repository while the static pilot is still hosted by GitHub Pages.

For a non-Docker process manager, install `server/requirements.txt`, set the
production environment, and run the equivalent release and server commands:

```sh
python server/manage.py migrate --noinput
python server/manage.py createcachetable
python server/manage.py collectstatic --noinput
gunicorn --chdir server config.wsgi:application --bind 127.0.0.1:8000 --workers 2
```

## Data handling and routine operation

MathStoa retains stable sign-in identifiers, account details, session data,
private contact email where supplied, and submission/review records. Public
collaborator names, affiliations, roles, and ORCID iDs remain personal data.
Provider profile payloads are stripped rather than retained in
`SocialAccount.extra_data`; OAuth access and refresh tokens are not stored.
MathStoa does not receive the provider's account password. An authenticated
provider account does not prove authorship, current employment, or permission to
publish another person's details.

Keep account/contact information private. Publish only the scholarly attribution
that the submitter is authorised to supply. A collaborator ORCID entered by a
submitter is not authenticated by that collaborator merely because it has a
valid format. Confirm the public privacy notice and actual retention practices
before launching.

Remove expired sessions and old invitations regularly, for example with daily
scheduled commands:

```sh
docker compose exec -T app python server/manage.py clearsessions
docker compose exec -T app python server/manage.py purge_expired_invitations
docker compose exec -T app python server/manage.py purge_repository_verification_attempts --days 30
```

Invitations are valid for seven days and grant editing access to the signed-in
account that accepts the bearer link. Share them privately. The invitation purge
removes expired, revoked, or consumed invitations older than thirty days by
default. It does not delete project drafts, submitted revisions, or scholarly
credit. Those records require the operator's documented retention and request
handling process.

Unused, expired repository-verification attempts can be removed after thirty
days by the third command. Completed verification evidence is kept with the
submission history. See `server/repository_verification/README.md` for the
verification model and its limits. `REPOSITORY_GITHUB_READ_TOKEN` is an optional
read-only server credential for public API quota; it never proves a submitter's
own access. `REPOSITORY_VERIFICATION_VALID_DAYS` sets the validity of evidence
to 1–30 days (default 30). Repository and owner identities are rechecked before
submission and approval; continuing individual collaborator permissions require
a new authorization check after the evidence expires.

Staff review fixed submitted revisions in `/admin/`, using the approval or
request-changes action. Approved revisions are merged with the existing static
archive for the public project data and announcement feed. Editing a draft or
requesting changes to an update leaves the previously approved revision public.
See [CONTRIBUTING.md](CONTRIBUTING.md) for the review workflow.

Back up PostgreSQL to access-controlled storage, test restoration, and define a
retention period for backups and operational logs. The supplied Gunicorn access
log omits request paths, query strings, and client IP addresses; check the reverse
proxy and hosting platform's logs too, because sign-in callbacks can contain
temporary codes and invitation URLs can contain access tokens. Database cache
expiry and session expiry are not a complete policy for
deleting accounts or historic submissions. Handle correction, account deletion,
and public attribution requests according to the published policy.

Use `docker compose logs --tail=100 app` for startup failures, avoiding disclosure
of secrets or private data when sharing logs. `docker compose down` stops the
service while retaining the database volume; adding `--volumes` deletes that
database and should not be part of a routine update.

## Checks

```sh
python3 scripts/validate.py
python3 scripts/feed.py
python3 -m unittest discover -s tests -v
python server/manage.py test accounts submissions repository_verification --settings=config.test_settings
```

Test each configured sign-in method against the actual provider and production
mail service before making it available. Local tests cannot establish that live
credentials, institutional agreements, DNS, certificates, or mail delivery work.
