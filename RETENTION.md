# Retention review for never-accepted submissions

Policy approved by the operator on 4 November 2026: review after six months
without activity; give the owner at least 30 days' notice before removing a
submission that is no longer needed. This is a human review process, not an
automatic deletion job. Tommaso Rosati and Simon Gabriel share responsibility
for the monthly review and restricted decision record; record which of them
completes each review. It complements the existing 90-day unused-draft cleanup.

## Monthly review

Run in the application environment:

```sh
python server/manage.py review_submission_retention
```

`--json` produces a minimal report for the restricted operational record.
The command is read-only: it neither sends messages nor deletes anything.
It prints IDs and dates, not titles, names, abstracts or contact details.
Keep even these IDs restricted; they can be linked back to personal records.

Candidates have submitted revisions, have never had an approved revision,
are not currently published or present in the static archive, and have had no
saved edit, submission, moderation or repository-verification activity for
six calendar months. Never-submitted drafts remain under the separate
90-day rule. Previously published work needs its own scholarly-record and
rights assessment and is not covered by this removal policy.

For each candidate, record the review date, project ID, reviewer, necessity
decision, reason, notice status and next action date in the restricted
retention record. Do not copy abstracts or contributor lists into this log.

- If moderation is simply overdue, resolve it; do not treat the operator's
  delay as the owner's abandonment.
- If the project is still being progressed, or a specific complaint, rights
  request, dispute or legal obligation needs the records, record the exact
  necessity and a next review date no more than three months away. Keep only
  what is needed, with restricted access. A generic "might be useful" is not
  sufficient.
- Otherwise propose removal and notify the owner as below. Review the
  candidate again after the notice period before any deletion.

## Thirty-day notice

Use a verified private contact route belonging to the owner, not an arbitrary
collaborator address and never a public issue. Account sign-in does not
necessarily provide an email address. The application does not yet provide
an on-site retention-notice facility. Existing private correspondence may
establish a verified owner contact route; otherwise record notification as
pending, assign a manual follow-up action and revisit it at the next monthly
review. Do not start the notice period or delete under this policy until
delivery to the owner has been established. Do not claim an email was sent
without evidence. An undeliverable notice needs a documented follow-up plan
and a fresh necessity review, not indefinite silent retention or automatic
deletion. The candidate-report command does not resolve this contact gap.

The notice must identify the owner's project, explain the inactivity and
proposed removal, give a date at least 30 days after delivery, and explain
how to ask for continued retention, retrieve their information or object.
Do not reveal other contributors' private addresses. Retain only the notice
date, verified route, deadline and outcome in the review record.

Suggested notice (complete privately before sending):

> We are reviewing your unpublished MathStoa submission [project title/ID]
> because it has had no activity for six months. We propose to remove its
> draft, submitted versions and associated verification records on or after
> [date at least 30 days after this notice]. Your account and other projects
> are unaffected. If you wish to continue the project, need a copy, or
> disagree with removal, please contact contact@math-stoa.org before that
> date or resume work on your submission. We will review your response
> before taking action. Our privacy notice explains your rights:
> https://math-stoa.org/privacy/.

An owner response or renewed activity pauses the proposed removal while it
is reviewed. There is no automatic approval or publication of a retained
submission.

## Removal and verification

After the deadline, recheck that there is no renewed activity, published
record, unresolved objection or documented reason to retain the data. Run
the existing `erase_submission` preview and review its inventory. Execute
only the specifically reviewed erasure; follow PRIVACY_REQUESTS.md and
record its minimal completion line. Account closure is a separate decision.
Re-apply completed erasures if restoring a backup. Do not run bulk deletion
from the candidate report.

## Backups, logs and operational evidence

Provider-published limits are not proof of this account's actual settings.
The restricted provider/transfer record holds current provider evidence and remaining checks.
Record the actual database plan, recovery/export windows, application and
edge-log windows, mail delivery logs and any separate backups in the
restricted operational record, including source, verification date and
owner. Retire unused copies and use a dated review where a fixed period
cannot be established. Keep the public notice accurate without publishing
account IDs, personal email destinations or security configuration.

## Basis

Six months is an operational choice, not a statutory universal deadline.
It leaves time for mathematical revisions while requiring a regular
necessity decision for records with no accepted scholarly publication.
The 30-day notice allows the owner to retrieve the work or explain why it
is still needed. [ICO storage-limitation guidance](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-protection-principles/a-guide-to-the-data-protection-principles/storage-limitation/)
requires retention to be justified and unnecessary personal data erased or
anonymised. Reviewed 4 November 2026.
