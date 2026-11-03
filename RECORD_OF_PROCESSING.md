# Record of processing, joint-controller arrangement and legitimate interests

The UK GDPR Article 30 record for math-stoa.org, with the Article 26
arrangement between its joint controllers and the legitimate interests
assessment the privacy notice (`server/templates/privacy.html`) relies on.
Review it whenever the processing changes and at least once a year.
Last reviewed: 3 November 2026.

## Controllers

Tommaso Rosati (United Kingdom) and Simon Gabriel (United States) run
math-stoa.org in a personal capacity as joint controllers. Contact:
`PUBLIC_CONTACT_EMAIL`, which forwards to both. No data protection officer is
required (no large-scale monitoring or special category data).

### Arrangement between the joint controllers (Article 26)

- Tommaso Rosati is the first point of contact for privacy requests,
  complaints, the supervisory authority and security incidents, and keeps the
  restricted correspondence record, erasure record and breach register.
- Either controller who receives a request forwards it to the contact mailbox
  the same day. Both act on it; data subjects may exercise their rights
  against either of them.
- Both hold staff and superuser roles with MFA (STAFF_SECURITY.md), review
  submissions, and follow OPERATIONS.md and PRIVACY_REQUESTS.md.
- Neither adds a processor, a new use of personal data or a new recipient
  without the other's written agreement, recorded here.
- Each tells the other without delay of a suspected breach (OPERATIONS.md,
  Breach handling).
- The essence of this arrangement is in the privacy notice, "Who is
  responsible for your information".

## Processing activities

| Activity | Data subjects | Personal data | Legal basis | Recipients | Retention |
|---|---|---|---|---|---|
| Accounts and sign-in | Account holders | Provider identifier, random username, creation and sign-in dates, encrypted MFA secret and recovery-code data, session data | Art. 6(1)(b) contract | Render (processor) | 365 days without sign-in if no responsibilities; otherwise until closure (`server/accounts/retention.py`) |
| Security and abuse prevention | Visitors, account holders | IP-derived keyed digests in rate-limit cache; request log without URLs or IPs; Render and Cloudflare platform logs | Art. 6(1)(f) security | Render, Cloudflare (processors) | Counters ≤ 1 hour; platform logs per provider (OPERATIONS.md retention table) |
| Drafts, editing access, invitations | Account holders, named contributors | Draft fields, contributor names, ORCID iDs and institutional emails (required for review, never public), editor memberships, hashed invitation tokens | Art. 6(1)(b) for the account holder; 6(1)(f) for contributors | Render | Unused drafts 90 days; invitations 30 days after end |
| Repository verification | Submitters | GitHub user id, login, permission level, commit SHA, connected ORCID, dates | Art. 6(1)(f) authorised submissions | Render; GitHub (independent controller, API queries) | With the submission's records |
| Review and publication | Submitters, reviewers, named contributors | Immutable revision snapshots, review notes, reviewer identity; public listing of names and ORCID iDs | Art. 6(1)(f) scholarly attribution and accountable review | Public (listing only); Render | While listed or under review; never-accepted projects until removal is requested |
| Moderation notices | Submitters (by project) | Project title and link in an email to moderators | Art. 6(1)(f) | SMTP provider when configured (processor) | Moderators' mailboxes |
| Contributor confirmations | Named contributors | Name, institutional email, project title and the other contributors' names, sent to each contributor separately when a revision is submitted; also gives the Art. 14 information by linking the notice | Art. 6(1)(f) | SMTP provider (processor) | Recipients' mailboxes; the provider's sending logs |
| Legacy GitHub issue submissions | Submitters, named contributors | Whatever the issue contains | Art. 6(1)(f) | Public on GitHub (independent controller) | GitHub's |
| Privacy requests and breach records | Requesters | Request file, reply address, correspondence, outcome | Art. 6(1)(c) legal obligation | Email forwarding (Porkbun), controllers' mailboxes | Request files and exports deleted at closure; date, action and outcome kept two years |

No special category or criminal offence data is sought. No automated
decision-making or profiling.

## Processors and international transfers

| Processor | Role | Location | Transfer safeguard | Agreement |
|---|---|---|---|---|
| Render | Hosting, database, backups | Data in Frankfurt; company and sub-processors (AWS, Google Cloud, Cloudflare and others listed on Render's trust page) in the US | EU–US Data Privacy Framework with UK Extension | Render DPA: **confirm it is in force and record the date and version here** |
| Cloudflare | Network edge in front of Render | Global; US company | Data Privacy Framework | Via Render |
| Porkbun | Email forwarding for contact@math-stoa.org | US | **to check** | Porkbun terms |
| SMTP provider (Brevo, planned) | Moderation notices | EU | Not a restricted transfer | Brevo DPA, to accept when configured |

Simon Gabriel's access from the United States is access by a joint
controller, not a processor. **Open:** decide and record the transfer basis
for it (for example a written agreement between the controllers using the
UK International Data Transfer Addendum, or reliance on Simon being directly
subject to the UK GDPR as a joint controller).

## Legitimate interests assessment

**Purpose.** Credit contributors accurately and permanently in a public
scholarly archive; publish only projects that their submitters are authorised
to list; keep an accountable record of review decisions; keep the service and
its accounts secure.

**Necessity.** Names and ORCID iDs are the minimum for attribution. A private institutional email per contributor lets each one be told they were listed and object, and lets the controllers reach them about the listing. Repository
evidence (GitHub id, login, role or commit) is the minimum to show
authorisation. No emails, addresses, profile payloads or OAuth tokens are
kept; rate-limit keys are keyed digests; the request log omits URLs and IPs.

**Balancing.** The data is professional, not sensitive, and of the kind
researchers expect to see attached to their work. Contributors named by
someone else did not supply it themselves; each is emailed when a project
listing them is submitted, and this is further mitigated by the submitter's confirmation that they may list them, the
notice's dedicated section, the right to object without an account, and the
`erase_submission --remove-contributor` command. Private records are visible
only to the owner, editors and the controllers. Outcome: the interests are
not overridden, provided objections are honoured promptly.

## Records kept elsewhere (restricted, not in this repository)

- Privacy correspondence log and erasure record (PRIVACY_REQUESTS.md).
- Breach register (OPERATIONS.md, Breach handling).
