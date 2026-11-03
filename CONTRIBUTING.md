# Contributing to mathStoa

mathStoa lists living mathematical projects hosted in public Git repositories. Exposition, partial arguments, experiments, and formalizations are welcome; a completed article is not required.

## Submit a project

Sign in at <https://math-stoa.org/accounts/login/> and use `/submissions/new/`. The available sign-in methods depend on the operator's configuration. Create a private draft, add the project details and public collaborator attribution, verify repository control, check the declaration, and submit it for moderation. A submission is a proposal; it does not immediately change the public archive.

If you cannot use the account service, the [GitHub project submission form](https://github.com/MathStoa/mathstoa.github.io/issues/new?template=submit-project.yml) remains available as a legacy route. GitHub issue submissions and their moderation discussion are public. Do not put private contact details in that form or its discussion.

A registered project should have:

- A public Git repository with meaningful mathematical content.
- Optionally, a DOI for an archived release of the repository. GitHub explains [how to get a DOI for a repository with Zenodo](https://docs.github.com/en/repositories/archiving-a-github-repository/referencing-and-citing-content#issuing-a-persistent-identifier-for-your-repository-with-zenodo).
- Optionally, a publicly accessible project website explaining its aims and introducing the material in the repository.
- A named steward who agrees to the listing and can keep its status current.
- A short original abstract and relevant subject categories.
- Clear reuse terms and a visible contribution and credit policy. Public visibility alone does not grant permission to reuse material.
- An accurate statement of whether contributions are welcome and, where possible, specific useful tasks.

Only submit a registration if you maintain the project or have a steward's permission. You may suggest a public project for consideration without that permission, but clearly mark the suggestion as unaffiliated in a general issue; it cannot become a registered project on that basis. The pilot's public examples are expressly labelled and excluded from announcements.

The account service initially verifies public GitHub repositories through a verification file committed to the repository or a separate GitHub authorisation check. Follow the verification instructions shown for your draft. Logging in with GitHub or listing a repository URL alone does not verify repository control. Other hosts cannot be published through this service until their verification methods are supported, even when they are offered as sign-in providers. Verification is required again during moderation; it does not replace the submitter's permission declaration or establish agreement from every collaborator.

List every contributor, including yourself, by name and surname and ORCID iD. Anyone can [register for an ORCID iD](https://orcid.org/register) free of charge. Confirm that you are authorised to publish the attribution you supply, and keep personal email addresses and private correspondence out of these public fields. Entering someone else's ORCID iD does not authenticate that person or confirm their agreement.

Only the account that created a submission can edit it. Being credited as a collaborator does not grant access to the submission.

Choose one main subject and, if the project spans several areas, up to three further subjects. Use the closest subject codes where available, such as `math.AG` or `math.CO`, and describe an unmatched subject in the submission. These are discovery categories, not an assertion of arXiv endorsement or registration.

The DOI identifies the archived release that a listing announces; a mutable repository URL is not an archival snapshot.

## Moderation and corrections

mathStoa maintainers check scope, readable content, public links and any DOI, permission to register, attribution, and clarity of the requested collaboration. They may ask for revisions or decline an empty, misleading, duplicate, or out-of-scope submission. This is archive moderation, not mathematical peer review.

In the account service, submitting a draft creates a fixed revision for moderation. Staff can approve it or request changes. A previously approved revision remains public while an update is being moderated; editing a draft does not silently replace the published listing. Moderation decisions are retained with the submitted revision. Legacy submissions retain their decisions in the GitHub issue or pull request.

To correct a listing, report a credit problem, or appeal a decision, open an issue with the project ID and relevant public evidence, or use the private contact route in the service's privacy notice when the request includes private information. Avoid posting private contact details or confidential correspondence. A maintainer should record the resolution and explain material changes. A steward can request removal or other changes through the same route.

## Contribution credit

Keep these roles separate:

- **Contributor:** credit the accepted contribution and describe what it did, including useful ideas or review supplied outside Git commits.
- **Section contributor:** attribute substantive writing and mathematical work at the section level when useful.
- **Maintainer:** someone responsible for review and project decisions; this role does not automatically determine authorship.
- **Paper author:** agree authorship for the particular output, using the project's published policy and the named authors' consent.

A typo correction deserves acknowledgment without automatically implying authorship of a resulting paper. A short decisive observation can merit substantial credit. Do not use commit counts or lines changed as an authorship threshold. Git history supports a contribution record but cannot decide intellectual credit on its own.

The project steward should state these rules before inviting contributions. mathStoa requires transparent policies and handles the accuracy of its own listings; project participants remain responsible for their research's authorship decisions. Contributions to this archive do not imply contributions to the listed research.

## Maintainer workflow

### Account-service submissions

1. Open the submitted revision in the Django administration interface at `/admin/`. Confirm repository-control verification and the steward's permission, the public repository link, that any DOI resolves to an archived release of that repository, the website link if one is given, the original summary, contributor attribution, subject categories, and the reuse terms and contribution policy stated in the repository.
2. Record the moderation note and use the revision's approval or request-changes action. Judge the fixed submitted snapshot, not a later working draft. Approval publishes that revision to the database-backed archive and feed; requesting changes leaves any previously approved version public.
3. Inspect the resulting public listing and announcement. Illustrative examples remain excluded from announcements. Handle corrections through a new moderated revision, and retain a clear record of removal or attribution decisions.
4. Keep backups and run the retention commands described in [SETUP.md](SETUP.md). Drafts and submitted revisions are not automatically deleted when a session or invitation expires. Handle account and attribution requests according to the published privacy policy.

Moderation rules:

- Decide within a week of submission. The site header shows moderators a **Moderation queue** count, and a moderation mailbox can receive a notice for every submitted revision (see [SETUP.md](SETUP.md)).
- One moderator is enough to publish, and a moderator may approve a project they own or submitted themselves; every decision records who made it.
- Every decision carries a moderation note. The note is the only message the submitter receives: accounts created through ORCID, GitHub or GitLab hold no email address, so submitters read the outcome on their draft page.
- "Request changes" is also how to decline. State the reason, including out-of-scope or duplicate listings, so the submitter can respond or stop.
- Never approve a test or demonstration draft; use "Request changes" to clear it from the queue.

### Legacy JSON archive

1. Read the issue and confirm the steward's permission. Keep source links and the moderation decision in the issue.
2. Add or update the entry in `docs/data/projects.json`, following the existing structure. Use a stable, unique project ID. Record the actual acceptance date in `added`; keep `example: true` for illustrative entries and use `false` only for accepted registrations.
3. Confirm that the title, summary, DOI, category labels, and project credits accurately reflect the source and submission. Record any DOI as an `https://doi.org/` link in `doi`; `doi` and `readingUrl` are optional. Do not copy research content into the archive.
4. Run `python3 scripts/validate.py`, `python3 scripts/feed.py`, and `python3 -m unittest discover -s tests -v`.
5. Preview with `python3 -m http.server 4173 --directory docs`, inspect the listing and links, and include any changed generated feed in the pull request.
6. Merge the change into `main` and link the published listing from the submission issue.

Ordinary repository commits do not create archive announcements. The initial feed announces accepted registrations, excluding examples; a richer release-announcement policy can be added when the pilot needs it.

## License for contributions

By contributing code, you agree to its inclusion under this repository's MIT License. By contributing an original archive summary, you agree to its inclusion under CC BY 4.0 as described in the README. Supply the attribution you want retained. Do not submit text or other material that you lack permission to contribute under those terms. Linked projects keep their own licenses and attribution.
