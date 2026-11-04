# Record of processing, joint-controller arrangement and legitimate interests

The UK GDPR Article 30 record for math-stoa.org, with the Article 26
arrangement between its joint controllers and the legitimate interests
assessment the privacy notice (`server/templates/privacy.html`) relies on.
Review it whenever the processing changes and at least once a year.
Last reviewed: 4 November 2026. Provider evidence and outstanding checks are kept in the restricted compliance folder.

## Controllers

Tommaso Rosati (United Kingdom) and Simon Gabriel (United States) run
math-stoa.org in a personal capacity as joint controllers. Contact:
`PUBLIC_CONTACT_EMAIL`, which forwards to both. No data protection officer is
required (no large-scale monitoring or special category data).

### Arrangement between the joint controllers (Article 26)

- Tommaso Rosati and Simon Gabriel have equal responsibility for privacy
  requests, complaints, supervisory-authority correspondence and security
  incidents, and jointly maintain the restricted correspondence record,
  erasure record and breach register. Either may handle an individual case;
  record who is handling it, with the other available for review or absence cover.
- Either controller who receives a request forwards it to the contact mailbox
  the same day. Both act on it; data subjects may exercise their rights
  against either of them.
- Both moderate submissions and receive correspondence at the shared contact
  address. Simon’s intended access is website moderation and that correspondence,
  not hosting, database or backups. Verify actual permissions against this scope;
  do not assume a superuser account is limited to moderation. Require MFA and
  follow STAFF_SECURITY.md, OPERATIONS.md, COMPLAINTS.md and PRIVACY_REQUESTS.md.
- Neither adds a processor, a new use of personal data or a new recipient
  without the other's written agreement, recorded here.
- Each tells the other without delay of a suspected breach (OPERATIONS.md,
  Breach handling).
- The essence of this arrangement is in the privacy notice’s Summary section.
  The transfer agreement and its private contact details are kept outside this
  public repository. Both controllers must confirm and adopt the arrangement.

## Processing activities

| Activity | Data subjects | Personal data | Legal basis | Recipients | Retention |
|---|---|---|---|---|---|
| Accounts and sign-in | Account holders | Provider identifier, random username, creation and sign-in dates, encrypted MFA secret and recovery-code data, session data | Art. 6(1)(b) contract | Render (processor) | 365 days without sign-in if no responsibilities; otherwise until closure (`server/accounts/retention.py`) |
| Security and abuse prevention | Visitors, account holders | IP-derived keyed digests in rate-limit cache; request log without URLs or IPs; Render and Cloudflare platform logs | Art. 6(1)(f) security | Render, Cloudflare (processors) | Counters ≤ 1 hour; platform logs per provider (OPERATIONS.md retention table) |
| Drafts | Account holders, named contributors | Draft fields, contributor names, ORCID iDs and institutional emails (required for moderation, never public) | Art. 6(1)(b) for the account holder; 6(1)(f) for contributors | Render | Unused drafts 90 days |
| Repository verification | Submitters | GitHub user id, login, permission level, commit SHA, connected ORCID, dates | Art. 6(1)(f) authorised submissions | Render; GitHub (independent controller, API queries) | With the submission's records |
| Moderation and publication | Submitters, moderators, named contributors | Immutable revision snapshots, moderation notes, moderator identity; public listing of names and ORCID iDs | Art. 6(1)(f) scholarly attribution and accountable moderation | Public (listing only); Render | While needed for publication/moderation; never-accepted records reviewed monthly after six calendar months without activity, with 30 days’ notice before removal; justified exceptions reviewed within three months (RETENTION.md) |
| Moderation queue | Submitters, moderators | Pending revision and status; no submission-alert emails to moderators | Art. 6(1)(f) | Render | With submission records |
| Change-request notices | Named contributors | Each contributor's submitted email, frozen project title and revision number; separate messages to the same contacts as submission confirmations, without moderation feedback, account identifiers or other contributors' emails | Art. 6(1)(f), accountable moderation | SMTP provider when configured (processor) | Recipients' mailboxes; the provider's sending logs |
| Contributor confirmations | Named contributors | Name, institutional email, project title and the other contributors' names, separately queued when a revision is submitted, subject to delivery and abuse limits; links the notice, with required failed/suppressed notices needing operator follow-up | Art. 6(1)(f) | SMTP provider (processor) | Recipients' mailboxes; the provider's sending logs |
| Contributor email delivery controls | Contributors, submitters | Keyed recipient digests, account/revision reference and delivery status; limited repeat-prevention digests on revisions | Art. 6(1)(f), security and delivery accountability | Render; outgoing mail provider for actual delivery | Queued notices expire in 24 hours; delivery rows seven days; repeat-prevention digests with revision |
| Legacy GitHub issue submissions | Submitters, named contributors | Whatever the issue contains | Art. 6(1)(f) | Public on GitHub (independent controller) | GitHub's |
| Privacy requests and breach records | Requesters | Request file, reply address, correspondence, outcome | Art. 6(1)(c) legal obligation | Email forwarding (Porkbun), controllers' mailboxes | Request files and exports deleted at closure; date, action and outcome kept two years |

No special category or criminal offence data is sought. No automated
decision-making or profiling.

## Processors and international transfers

Published provider evidence was checked on 4 November 2026. Incorporated terms
can supply a binding DPA without a separate signature, but retain the actual
account/customer/version evidence privately. The restricted provider/transfer record holds sources,
contract scope, transfer-assessment steps and remaining verification.

| Provider | Role and location | Published safeguard/terms | Account-specific evidence |
|---|---|---|---|
| Render | Hosting, database, backups; Frankfurt service region stated in existing notice, US provider and subprocessors | Terms dated 10 July 2026 incorporate DPA dated 19 December 2024; UK DPF route with UK SCC/Addendum fallback | Official DPF register entry 9348 checked 4 November 2026: active EU and UK participation for non-HR data. Confirm contracting customer/terms and dashboard regions; review coverage on changes; assess fallback if needed |
| Cloudflare | Render network subprocessor; global | Covered through Render’s subprocessor arrangements, unless separately contracted | Confirm actual chain; do not invent a separate MathStoa DPA |
| Porkbun | Domain contact-email forwarding; US | Published DPA revised 13 October 2026 covers forwarding; Product Terms incorporate it; Schedule 3 includes UK IDTA | Confirm effective account version, scope for all UK GDPR-covered data and transfer assessment; message/backup retention |
| Contact mailbox providers | Receive forwarded correspondence for the controllers | Provider/product and contractual roles not yet confirmed | Complete private provider/transfer/retention inventory; do not publish destination addresses |
| Outgoing email provider | Contributor confirmations and change-request notices only | Brevo was previously described as planned; verify actual deployment and terms | Confirm provider, DPA, regions/onward access and logs; EU headquarters alone does not prove absence of transfers |

Simon’s US access covers website moderation and shared contact correspondence.
Prepare and execute the controller-to-controller UK IDTA and complete the
transfer risk assessment/data protection test for this scope. Service addresses,
state, direct emails and signatures are private completion fields, not public
notice fields. Do not mark this completed until the facts, assessment and
agreement have been adopted. Prior access must be assessed separately; no
backdating. Being subject to UK GDPR is not itself a transfer exemption.

The public notice gives controller identities, the shared contact, purposes,
recipient categories and US access. Provide information about safeguards and
copies as legally required, with appropriate redaction of personal contact
details and confidential security information. Keep the private agreement and
evidence outside the repository.

## Legitimate interests assessment

**Purpose.** Credit contributors accurately and permanently in a public
scholarly archive; publish only projects that their submitters are authorised
to list; keep an accountable record of moderation decisions; keep the service and
its accounts secure.

**Necessity.** Names and ORCID iDs are the minimum for attribution. A private institutional email per contributor lets each one be told they were listed and object, and lets the controllers reach them about the listing. Repository
evidence (GitHub id, login, role or commit) is the minimum to show
authorisation. Contributor contact emails and necessary correspondence are
kept as described above; sign-in provider profile payloads and OAuth tokens
are discarded. Rate-limit keys are keyed digests; the request log omits URLs
and IPs. Recorded licence declarations and authenticated ORCID provenance
are private evidence attached to the corresponding submission revision.

**Balancing.** The data is professional, not sensitive, and of the kind
researchers expect to see attached to their work. Contributors named by
someone else did not supply it themselves; a separate notice is queued when a project
listing them is submitted, subject to delivery and abuse limits. Required
notices that fail, expire or are suppressed need operator follow-up, and
draft collection has its own Article 14 deadline. This is further mitigated
by the submitter's confirmation that they may list them, the
notice's dedicated section, the right to object without an account, and the
`erase_submission --remove-contributor` command. Private records are visible
only to the owner and the controllers. Outcome: the interests are
not overridden, provided objections are honoured promptly.

## Records kept elsewhere (restricted, not in this repository)

- Privacy correspondence log and erasure record (PRIVACY_REQUESTS.md).
- Breach register (OPERATIONS.md, Breach handling).
- Retention necessity/notice decisions (RETENTION.md) and complaints cases (COMPLAINTS.md).
- Executed IDTA, transfer assessment, provider account terms and actual log/backup windows (restricted provider/transfer record).
