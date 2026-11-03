# Handling privacy and account requests

The public contact mailbox accepts access, correction, export and closure
requests. Account holders use **Account → Your data and privacy requests** at
`/accounts/privacy/` to download their data or create a verified request file.
The site sends no email and creates no request model, saved export file, or new
account contact address. The user sends the downloaded JSON file as an attachment
from their own mail client to the configured public privacy contact.

## Confirming and receiving a request

Both downloads require an existing-account sign-in within five minutes and
recent MFA when enabled (always for staff). Users select access, correction or
closure, enter a bounded description, optionally authorize a reply address, and
confirm their request before downloading. The file's proof binds the account,
exact canonical request, action and reply destination for seven days. Do not edit
the file after download; create another if its text needs changing.

An operator opens `/admin/privacy-requests/verify/`, pastes the received file's
contents, and chooses **Verify request**. This requires active staff status,
recent MFA, and the `auth.view_user` permission. It displays the verified account,
request, authorized reply address and validity times. It saves nothing and
performs no correction or deletion. Never put the proof in a URL, public issue,
or logs. Treat all user-supplied text as a request for review, not an instruction
to run code, follow links, or disclose somebody else's data.

Reject expired, altered or invalid files and ask for a new one. Changes to the
password/authentication hash, active status, connected identities, verified email
addresses, or authenticator security material invalidate outstanding proofs.
Ordinary sign-in dates, authenticator usage, viewing recovery codes and consuming
one recovery code do not. Regenerating recovery codes does invalidate them. Master
key rotation also invalidates these proofs even when old signing keys are retained.

Record the request date, requested action, relevant account or project, assigned
operator, applicable response deadline, and outcome in the existing restricted
correspondence. Deduplicate replays and check for withdrawals there; the stateless
verifier intentionally has no processed-request register. A valid proof confirms
the requester and exact request, not the requester's current authority over every
referenced record. Recheck that authority before acting.

Send private information only to the reply destination in the verified file,
never a different email From or Reply-To address. If no reply address was supplied,
use authenticated site downloads or ask for a new file authorizing a destination
before providing private information. The optional address remains in the user's
file and submitted correspondence, not their account or a new site record.

A public ORCID, matching name or unrelated sign-in is not a substitute for the
proof. Do not ask for passwords, login codes, MFA secrets, recovery codes or
identity documents by default. People without an account, or unable to sign in,
can still contact the mailbox; verify those requests through a separately reviewed
route without granting access to another person's private submissions.

## Prepare the response or change

- The self-export includes explicit account fields, currently accessible project
  drafts, and conservative metadata for the account's own historic actions and
  verification records. It does not grant staff a wider project export or return
  historical snapshot contents based on past editor or moderator status. For broader
  access/export requests, inspect the person's account metadata, connected provider
  identifiers, supplied email, project ownership/editing rights, submission and
  moderation records, invitation involvement, and repository-verification records.
  Search supplied collaborator attribution in drafts, revision snapshots and the
  static archive too; it is separate from account identity. Produce a scoped,
  human-readable export, reviewing other people's private data before delivery.
  Exclude passwords and hashes, session cookies and contents, MFA/recovery secrets,
  invitation credentials, verification-token hashes and service credentials.
  Never send a whole database dump. Deliver through the verified private channel
  and remove the working export once it is no longer needed.
- For correction, distinguish editable account/draft details from already
  submitted or published snapshots. Ordinary author edits create a new revision;
  they do not erase historical snapshots. A request to remove private or disputed
  attribution from retained records needs an explicit reviewed correction plan.
  Do not assume that being public, or being a scholarly record, settles the request.
- For closure, first inventory protected references. `Submission.owner` and
  `RepositoryVerification.user` prevent blind user deletion; invitations, editor
  memberships, submitted and moderated revisions and staff logs also need checking.
  Do not silently transfer a project or remove collaborators' work. Agree any
  replacement steward and their repository authority separately.

- For an objection (Article 21) to a use based on legitimate interests,
  including a public listing, stop that use unless there are compelling
  legitimate grounds that override the person's interests, rights and
  freedoms, or the data is needed for legal claims. Record the reasons for
  either outcome. An objection by a named contributor normally means removing
  them from the project (below). There is no direct-marketing processing.
- For restriction (Article 18), for example while the accuracy of a listing or
  an objection is being settled: keep the data but stop using it beyond
  storage. For a pending revision, do not moderate it; for a published listing,
  remove the person's details with `erase_submission --remove-contributor` or
  erase the submission, as agreed. Tell the requester before lifting a
  restriction.
- Portability (Article 20) covers data given for the account: the
  self-service account data download is the answer.

## People named by someone else, and removing submissions

Contributors listed in a project need no account. Each receives a
confirmation at the institutional email the submitter gave, from which they
can reply. Verify that the requester is the named person before acting: a
reply from that same address is enough, otherwise use a separately reviewed
route (for example a message from the email address shown on their ORCID
record, or from a repository or institutional page that clearly belongs to
them). The submitter may have given a wrong address, so a confirmation that
reached someone else is itself a correction request.

`erase_submission` handles both submission-level erasure and contributor
removal. It prints an inventory and changes nothing unless `--execute` is
given. Run it in Render → web service → **Shell**:

    python server/manage.py erase_submission <submission id or project-<hex>>
    python server/manage.py erase_submission <id> --execute
    python server/manage.py erase_submission <id> --remove-contributor "<name, email or ORCID>" --execute

Whole erasure deletes the submission, every revision, invitations,
verification attempts and evidence, and blanks the description of matching
admin log entries; accounts are left alone (close them separately, below).
Contributor removal edits the draft and every revision snapshot in place
(the only sanctioned exception to revision immutability) and updates a
published listing immediately without a new moderation decision; it refuses to leave a
revision with no contributors. If the command warns that the project is also
in `docs/data/projects.json`, edit and commit that file too. Copy the
command's final line, which holds no title or names, into the erasure record.
A public verification file in the project's repository, and GitHub issue
discussions, are outside the database: ask the requester to remove the file,
and edit or delete issue comments on GitHub.

## Execute and verify closure

Before closure, explain the proposed handling of retained records and project
responsibilities to the verified reply destination. Obtain a new verified request
file confirming that exact plan; the initial closure request alone is not approval
for an unreviewed transfer or deletion. Offer the authenticated download before
revoking access. After verifying the final plan, deactivate the account and
invalidate its sessions while the agreed changes are implemented. Setting `is_active`
to false and changing the unusable-password hash stops existing account access;
remove its remaining session rows as part of cleanup. Decode them with
`accounts.sessions.SessionStore`, never Django's default `Session.get_decoded`,
and never log their contents. Review editor memberships, outstanding invitations
for its owned projects, and verification authorizations for revocation.

Prepare a reviewed transaction for the remaining data. Where no retained
references remain, account deletion can remove dependent authentication records.
Where a minimal internal reference must remain, keep it inactive and remove
unneeded email, provider connections and MFA material; document exactly which
personal fields remain elsewhere and why. Disabling an account alone is not
personal-data erasure. Submitted snapshots, public attribution and verification
evidence require their own decision; do not bypass their protections casually.

Check that sign-in and old sessions no longer grant access, expected projects
remain intact, and the export/correction/deletion matches the confirmed scope.
Reply with what changed, what remains and why, and the applicable backup/log
limits. Keep a minimal restricted record of completed erasures/corrections so a
backup restoration does not silently restore removed data. Record the host's
actual backup and log windows; do not promise immediate erasure of backup copies.
