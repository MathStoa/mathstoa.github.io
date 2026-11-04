# mathΣτοά

A small, curated archive of living mathematical research: open questions, expository projects, formalizations, and research notes that live in public Git repositories.

[Visit the pilot](https://math-stoa.org/) · [Contribution policy](CONTRIBUTING.md) · [Account service setup](SETUP.md)

An open mathematical research project is a public Git repository containing mathematical work that can continue to develop, with, optionally, a DOI for an archived release and a public website explaining its aims. mathStoa is intended for research carried out with AI, including articles written by AI that can be studied, corrected, extended, and used in further research.

## Pilot scope

New registrations require a project steward's permission and human moderation. A listing does not certify a mathematical claim.

The public archive provides search and subject filters, readable project pages and repository links. This repository also includes a Django account and submission service: email-code sign-in, optional ORCID/GitHub/GitLab/institutional sign-in, direct submission and staff moderation of versioned submissions. Creating a submission or submitting an edit emails its listed contributors, subject to delivery limits. It registers public repository links rather than copying repository contents. Submitted revisions await repository-control verification when needed, then proceed automatically to moderation; approval rechecks that control. There is no separate draft-save step. The initial implementation supports public GitHub repositories. GitLab sign-in is available independently, but verified publication from other repository hosts is not yet supported.

The account service runs on a production host with PostgreSQL and configured sign-in providers or SMTP; publishing this code to GitHub Pages does not activate it. Since 3 November 2026 the service has served <https://math-stoa.org/>, and signed-in submission at `/submissions/new/` is the operational route. The [GitHub submission form](https://github.com/MathStoa/mathstoa.github.io/issues/new?template=submit-project.yml) remains available as the legacy public route. See [SETUP.md](SETUP.md) for the exact setup and limitations.

Submission owners can open a project under **My submissions** and choose
**Delete**. A confirmation page explains the permanent removal of
the submission and its history, including any published listing. A separately
maintained archive copy requires staff assistance to remove both together.

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
python3 -m unittest discover -s tests -v
```

## Content and deployment

- `docs/` holds the public pages and assets. The Django service serves them together with the account routes; the GitHub Pages copy only redirects to the canonical domain.
- `docs/data/projects.json` holds the existing manually moderated archive.
- `server/` contains the Django account and submission service. Approved submission revisions are stored in its database and merged into the public archive when served by Django.
- `scripts/validate.py` checks the archive contract.
- `tests/` checks the archive validation.
- `.github/ISSUE_TEMPLATE/submit-project.yml` provides the legacy public submission route.
- `Dockerfile`, `compose.yaml`, and `.env.example` provide the application and PostgreSQL deployment configuration. [SETUP.md](SETUP.md) covers provider callbacks, email, HTTPS, backups, and operations.

The Django service serves both the public site and the account routes at <https://math-stoa.org/>, currently from Render with a PostgreSQL database; see [OPERATIONS.md](OPERATIONS.md). GitHub Pages, built from **branch `main`, folder `/docs`**, now only redirects to that domain through `docs/CNAME`. Project repositories and their contents remain external, and GitHub continues to host the source repository.

For a host move, preserve the canonical domain where possible. If the public URL changes, update `PUBLIC_BASE_URL`, the allowed hosts and trusted origins, provider callbacks, public links and the recovery base URL in `docs/404.html`. Back up and migrate the submission database as well as the static archive. Preserve submission issues and moderation decisions if the GitHub repository also moves. Do not delete the database volume during a routine application update.

## Accounts and attribution

Users can use the sign-in methods enabled by the operator; an institutional account or ORCID is not required when email sign-in is enabled. Institutions can be connected through a configured OpenID Connect provider or broker; this does not automatically provide access for every university.

During the beta, new personal accounts require moderator approval after verifying their email or provider identity and before signing in or submitting. Existing members keep access. Moderators review **Account applications** in the account menu or administration interface. `ACCOUNT_APPROVAL_REQUIRED=false` ends moderation for future registrations; existing pending applications still need approval and rejected accounts stay blocked. Account approval is separate from repository verification and project moderation.

Organisations can apply for a separate account at `/accounts/organisation/register/` when organisation sign-in and registration are enabled. A moderator reviews the organisation and the applicant's authority to represent it before activating the account. Approved organisations sign in at `/accounts/organisation/login/` using a code sent to their account email; completing this sign-in verifies control of the mailbox. Organisation approval does not replace repository verification or moderation of individual project submissions. Application details and decision records remain private.

Authorised moderators can delete an organisation through its admin detail page after confirming its name. This removes the organisation and its sign-in account together. Owned projects and retained repository-verification evidence must be resolved first; account deletion does not remove projects or their public attribution. See [organisation account administration](SETUP.md#organisation-accounts).

Account contact details, sign-in identifiers, and session records are private. Public collaborator attribution is supplied separately and does not grant editing access. Provider profile payloads and OAuth tokens are not retained. An authenticated ORCID links an account to that iD; it does not establish authorship, institutional endorsement, or mathematical correctness. Read the service's `/privacy/` notice and the [data-handling instructions](SETUP.md#data-handling-and-routine-operation) before operating it publicly.

## Credit and reuse

Project credit belongs to the people and groups responsible for the work. An archive listing does not make mathStoa a contributor to the research. Each project sets its own contribution and credit policy; see [our contribution policy](CONTRIBUTING.md) for the requirements for listings.

The site code is available under the [MIT License](LICENSE). New submissions include an explicit [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/) grant for the abstract, retained with each submitted revision. Where this grant is recorded, the listing and data export identify the abstract licence and supplied contributor attribution. Credit those contributors, link to the source record and licence, and indicate modifications. Historical records are not retrospectively marked as accepting the new declaration; check each listing’s licence information before reuse. Linked repositories retain their own terms.

For the legacy GitHub route, moderators must retain the issue permalink, the submitting account, the submitted abstract and contributor attribution, and the exact accepted declaration and its version before adding `summaryLicense` and `summaryAttribution` to an archive record. Record the acceptance time from the submitted issue or a later explicit confirmation, rather than inventing it from a migration date. Treat ORCID iDs supplied in public issues as unauthenticated. An old issue with the earlier permission statement is not evidence that the new declaration was accepted: obtain a new confirmation or leave the abstract licence metadata unset.

External repositories, linked documents, quoted material, and other third-party content retain their own rights and licenses. Listing them does not change those terms. mathStoa is an independent pilot and is not affiliated with arXiv.

## Legal and operational procedures

[Retention reviews](RETENTION.md), [reports and complaints](COMPLAINTS.md), and
[Online Safety Act scope and risk assessment](ONLINE_SAFETY.md) record the
procedures and the remaining operational evidence. A draft assessment or
unsigned agreement is not a completed compliance check. Keep private
agreements, addresses, correspondence and supporting account evidence out
of this public repository.
