# mathΣτοά

A small, curated archive of living mathematical research: open questions, expository projects, formalizations, and research notes that live in public Git repositories.

[Visit the pilot](https://math-stoa.org/) · [Contribution policy](CONTRIBUTING.md) · [Account service setup](SETUP.md)

An open mathematical research project is a public Git repository containing mathematical work that can continue to develop, with, optionally, a DOI for an archived release and a public website explaining its aims. mathStoa is intended for research carried out with AI, including articles written by AI that can be studied, corrected, extended, and used in further research.

## Pilot scope

New registrations require a project steward's permission and a human review. A listing or an announcement does not certify a mathematical claim.

The public archive provides search and subject filters, readable project pages, repository links, and an Atom announcement feed. This repository also includes a Django account and submission service: email-code sign-in, optional ORCID/GitHub/GitLab/institutional sign-in, private drafts, and staff review of versioned submissions. It registers public repository links rather than copying repository contents. Submission and approval require verification of repository control; the initial implementation supports public GitHub repositories. GitLab sign-in is available independently, but verified publication from other repository hosts is not yet supported.

The account service runs on a production host with PostgreSQL and configured sign-in providers or SMTP; publishing this code to GitHub Pages does not activate it. Since 3 November 2026 the service has served <https://math-stoa.org/>, and signed-in submission at `/submissions/new/` is the operational route. The [GitHub submission form](https://github.com/MathStoa/mathstoa.github.io/issues/new?template=submit-project.yml) remains available as the legacy public route. See [SETUP.md](SETUP.md) for the exact setup and limitations.

## Run locally

For the complete service, use Python 3.12 and follow the [local account-service instructions](SETUP.md#local-preview). Its entry points are `/accounts/login/`, `/submissions/`, and `/admin/`. The service serves the archive and accounts from one origin.

To preview only the original public archive, without accounts or submissions, Python 3.10 or later is enough:

```sh
git clone https://github.com/MathStoa/mathstoa.github.io.git
cd mathstoa.github.io
python3 -m http.server 4173 --directory docs
```

Open <http://localhost:4173>. Use the local server so the browser can load the archive JSON. This static preview cannot run the sign-in routes or show database-backed submissions.

Before publishing an archive change:

```sh
python3 scripts/validate.py
python3 scripts/feed.py
python3 -m unittest discover -s tests -v
```

Commit the updated feed with the archive when the generator changes it.

## Content and deployment

- `docs/` holds the public pages and assets. The Django service serves them together with the account routes; the GitHub Pages copy only redirects to the canonical domain.
- `docs/data/projects.json` holds the existing manually reviewed archive.
- `server/` contains the Django account and submission service. Approved submission revisions are stored in its database and merged into the public archive and feed when served by Django.
- `scripts/validate.py` checks the archive contract.
- `scripts/feed.py` generates the static feed from accepted, non-example entries in the JSON archive.
- `tests/` checks the validation and feed behavior.
- `.github/ISSUE_TEMPLATE/submit-project.yml` provides the legacy public submission route.
- `Dockerfile`, `compose.yaml`, and `.env.example` provide the application and PostgreSQL deployment configuration. [SETUP.md](SETUP.md) covers provider callbacks, email, HTTPS, backups, and operations.

The Django service serves both the public site and the account routes at <https://math-stoa.org/>, currently from Render with a PostgreSQL database; see [OPERATIONS.md](OPERATIONS.md). GitHub Pages, built from **branch `main`, folder `/docs`**, now only redirects to that domain through `docs/CNAME`. Project repositories and their contents remain external, and GitHub continues to host the source repository.

For a host move, preserve the canonical domain where possible. If the public URL changes, update `PUBLIC_BASE_URL`, the allowed hosts and trusted origins, provider callbacks, public links, the recovery base URL in `docs/404.html`, and `SITE_URL` in `scripts/feed.py`, then regenerate the static feed. Keep `FEED_ID` unchanged: it is the permanent Atom feed identity, not a navigation link. Back up and migrate the submission database as well as the static archive. Preserve submission issues and review decisions if the GitHub repository also moves. Do not delete the database volume during a routine application update.

## Accounts and attribution

Users can use the sign-in methods enabled by the operator; an institutional account or ORCID is not required when email sign-in is enabled. Institutions can be connected through a configured OpenID Connect provider or broker; this does not automatically provide access for every university.

Account contact details, sign-in identifiers, and session records are private. Public collaborator attribution is supplied separately and does not grant editing access. Provider profile payloads and OAuth tokens are not retained. An authenticated ORCID links an account to that iD; it does not establish authorship, institutional endorsement, or mathematical correctness. Review the service's `/privacy/` notice and the [data-handling instructions](SETUP.md#data-handling-and-routine-operation) before operating it publicly.

## Credit and reuse

Project credit belongs to the people and groups responsible for the work. An archive listing does not make mathStoa a contributor to the research. Each project sets its own contribution and credit policy; see [our contribution policy](CONTRIBUTING.md) for the requirements for listings.

The site code is available under the [MIT License](LICENSE). Original archive summaries contributed to mathStoa are available under [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/): credit mathStoa, retain any supplied contributor attribution, and link to the source record and license. Mention changes when adapting a summary.

External repositories, linked documents, quoted material, and other third-party content retain their own rights and licenses. Listing them does not change those terms. mathStoa is an independent pilot and is not affiliated with arXiv.
