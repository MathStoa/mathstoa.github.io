# Handling privacy and account requests

The public contact mailbox accepts access, correction, export and closure
requests. The service currently has no self-service export or closure endpoint.
Use a restricted operator case record; automatic retention cleanup is not a
substitute for handling an individual request.

## Intake and identity

Record the request date, requested action, relevant account or project, assigned
operator, applicable response deadline, and outcome. Keep only the correspondence
needed to resolve the request. Do not put it in a public GitHub issue.

Verify control through an existing sign-in method and a confirmation tied to the
specific request before disclosing private data or closing access. A public ORCID,
a matching name, an email From header, or an unrelated recent sign-in is not that
confirmation. Do not request passwords, login codes, MFA secrets, recovery codes,
or identity documents by default. Provider-only users need not add an email.
The repository does not yet provide an authenticated request-confirmation route;
choose and test a reviewed manual confirmation channel before opening public
registration. Collaborator-attribution requests may come from people without an
account and must not grant access to somebody else's private submissions.

## Prepare the response or change

- For access/export, inspect the person's account metadata, connected provider
  identifiers, supplied email, project ownership/editing rights, submission and
  review records, invitation involvement, and repository-verification records.
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
  memberships, submitted/reviewed revisions and staff logs also need review.
  Do not silently transfer a project or remove collaborators' work. Agree any
  replacement steward and their repository authority separately.

## Execute and verify closure

After confirming the requested scope, deactivate the account and invalidate its
sessions while the record-handling decision is implemented. Setting `is_active`
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
