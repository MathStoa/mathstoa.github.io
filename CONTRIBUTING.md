# Contributing to MathStoa

MathStoa lists living mathematical projects hosted in public Git repositories. Exposition, partial arguments, experiments, and formalizations are welcome; a completed article is not required.

## Submit a project

Use the [project submission form](https://github.com/MathStoa/mathstoa.github.io/issues/new?template=submit-project.yml). Submissions and review discussion are public. A submission is a proposal; it does not immediately change the site.

A registered project should have:

- A public repository with meaningful mathematical content, a readable introduction, and a clear purpose or question.
- A named steward who agrees to the listing and can keep its status current.
- A short original abstract, relevant subject categories, and a link to the best reading entry point.
- Clear reuse terms and a visible contribution and credit policy. Public visibility alone does not grant permission to reuse material.
- An accurate statement of whether contributions are welcome and, where possible, specific useful tasks.

Only submit a registration if you maintain the project or have a steward's permission. You may suggest a public project for consideration without that permission, but clearly mark the suggestion as unaffiliated in a general issue; it cannot become a registered project on that basis. The pilot's public examples are expressly labelled and excluded from announcements.

Use the closest subject codes where available, such as `math.AG` or `math.CO`, and describe an unmatched subject in the submission. These are discovery categories, not an assertion of arXiv endorsement or registration.

The archive uses these independent fields:

| Field | Values |
| --- | --- |
| Kind | Research; Exposition; Formalization; Mixed |
| Stage | Exploration; Partial results; Claimed solution; Ongoing; Reference; Archived |
| Collaboration | Open to contributions; Specific help wanted; Currently closed; See repository |

Stages and invitations describe the steward's stated position. “Claimed solution” is not a verification badge. Use “See repository” when the archive cannot accurately summarize the project's contribution preferences. An optional release, commit, or archive link helps identify the version being announced; a mutable repository URL is not an archival snapshot.

## Review and corrections

MathStoa maintainers check scope, readable content, public links, permission to register, attribution, and clarity of the requested collaboration. They may ask for revisions or decline an empty, misleading, duplicate, or out-of-scope submission. This is an archive review, not mathematical peer review.

Review decisions are recorded in the issue or pull request. To correct a listing, report a credit problem, or appeal a decision, open an issue with the project ID and relevant public evidence. Avoid posting private contact details or confidential correspondence. A maintainer should record the resolution and explain material changes. A steward can request removal or a change in collaboration status through the same route.

## Contribution credit

Keep these roles separate:

- **Contributor:** credit the accepted contribution and describe what it did, including useful ideas or review supplied outside Git commits.
- **Section author or contributor:** attribute substantive writing and mathematical work at the section level when useful.
- **Maintainer:** someone responsible for review and project decisions; this role does not automatically determine authorship.
- **Release or paper author:** agree authorship for the particular output, using the project's published policy and the named authors' consent.

A typo correction deserves acknowledgment without automatically implying authorship of the whole project. A short decisive observation can merit substantial credit. Do not use commit counts or lines changed as an authorship threshold. Git history supports a contribution record but cannot decide intellectual credit on its own.

The project steward should state these rules before inviting contributions. MathStoa requires transparent policies and handles the accuracy of its own listings; project participants remain responsible for their research's authorship decisions. Contributions to this archive do not imply contributions to the listed research.

## Maintainer workflow

1. Review the issue and confirm the steward's permission. Keep source links and the review decision in the issue.
2. Add or update the entry in `docs/data/projects.json`, following the existing structure. Use a stable, unique project ID. Record the actual acceptance date in `added`; keep `example: true` for illustrative entries and use `false` only for accepted registrations.
3. Confirm that the title, summary, category labels, project credits, contribution note, and license note accurately reflect the source and submission. Do not copy research content into the archive. Place an optional snapshot link in the description or highlights if no dedicated field is available.
4. Run `python3 scripts/validate.py`, `python3 scripts/feed.py`, and `python3 -m unittest discover -s tests -v`.
5. Preview with `python3 -m http.server 4173 --directory docs`, inspect the listing and links, and include any changed generated feed in the pull request.
6. Merge the reviewed change into `main` and link the published listing from the submission issue.

Ordinary repository commits do not create archive announcements. The initial feed announces accepted registrations, excluding examples; a richer release-announcement policy can be added when the pilot needs it.

## License for contributions

By contributing code, you agree to its inclusion under this repository's MIT License. By contributing an original archive summary, you agree to its inclusion under CC BY 4.0 as described in the README. Supply the attribution you want retained. Do not submit text or other material that you lack permission to contribute under those terms. Linked projects keep their own licenses and authorship.
