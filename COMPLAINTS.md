# Reports, complaints and appeals

Prepared: 4 November 2026. This procedure covers mathStoa listings, moderation decisions, contributor attribution, copyright, privacy and accessibility. It establishes the workflow to use; it does not assert that mailbox cover or production response times have already been verified.

## Contact and information to provide

Email [contact@math-stoa.org](mailto:contact@math-stoa.org). A mathStoa or GitHub account is not required. Give the listing URL or project ID, explain the concern and the outcome requested, and include relevant links or evidence you can lawfully share. A particular subject line or form is not required. Explain any communication or accessibility adjustment needed.

Do not post private information in GitHub issues or send passwords, access tokens, unnecessary identity documents, or copies of suspected illegal sexual material. A URL and brief description are sufficient to start an investigation. Public GitHub issues remain an optional route for ordinary public feedback; move sensitive matters to the private channel.

## Receipt, ownership and deadlines

Tommaso Rosati and Simon Gabriel have equal responsibility for ensuring reports are handled. Either may handle a case; record who is doing so, with the other available for review or absence cover. This is a case assignment, not a standing hierarchy. A complaint about one operator should normally be reviewed by the other. Before relying on this procedure, record mailbox cover, check delivery, and arrange coverage during absence. The mailbox is not an emergency service.

On receipt, record a case reference, receipt date, contact details where provided, affected record, type of issue, urgency, case owner and next action in a restricted record. Do not place case details in the public source repository. Assess urgent risks immediately on becoming aware; do not wait for an acknowledgement deadline before investigating or removing access to known illegal material.

| Type of matter | Required handling |
| --- | --- |
| Data-protection complaint | Acknowledge without undue delay and within 30 days of receipt; investigate from receipt, provide appropriate progress information and communicate the outcome without undue delay. |
| Other report or moderation appeal | Acknowledge promptly, with a service maximum of 30 days; triage and investigate according to urgency. This maximum is a service commitment, not an Online Safety Act permission to wait. |
| Access, erasure, correction, objection or other privacy-rights request | Apply the separate UK GDPR deadline, normally one calendar month. A complaint acknowledgement does not restart or extend it. See [PRIVACY_REQUESTS.md](PRIVACY_REQUESTS.md). |
| Possible security incident or data breach | Escalate immediately under [OPERATIONS.md](OPERATIONS.md). Complaint handling does not replace incident notification deadlines. |

Identify mixed requests and run the relevant deadlines in parallel. If information is needed, ask only for what is necessary. Do not insist on an account, signed request file or identity document simply to report a problem. Authenticate proportionately before disclosing someone else's private data or changing an account. If more time is needed, explain why and give a realistic next update; a privacy-rights extension requires its own lawful basis and timely notice.

The privacy-complaint requirements are explained in the [ICO's complaints guidance](https://ico.org.uk/for-organisations/how-to-deal-with-data-protection-complaints/) and [receipt/investigation guidance](https://ico.org.uk/for-organisations/how-to-deal-with-data-protection-complaints/what-do-we-do-when-we-receive-a-complaint/), reviewed 4 November 2026. These are distinct from the [Online Safety Act reporting, removal and complaint duties](https://www.ofcom.org.uk/online-safety/illegal-and-harmful-content/illegal-content-duties-under-the-online-safety-act).

## Investigation and outcome

1. Locate the exact published revision, affected links and relevant moderation record. Preserve only necessary evidence. Check whether an external destination has changed since approval.
2. Consider temporary withdrawal where needed to prevent harm during investigation. Record the reason and scope; do not presume a complainant or submitter is correct without assessment. Never postpone urgent protective action merely to await a submitter's answer.
3. Ask the submitter or relevant contributor for a response where appropriate, without disclosing the reporter's identity or confidential information unnecessarily. Assess permission, attribution and the applicable rule separately from mathematical correctness.
4. Record the decision, evidence considered, resulting action, responsible operator and date. Correct, withdraw or restore the listing as justified, and check the public result. Explain reasons to the complainant and affected submitter to the extent lawful and safe. If no action is taken, explain why.
5. Give a private route to challenge the decision at the same address. The other operator should review the appeal where practical; disclose any lack of independent review. Retain statutory remedies. Privacy complainants may also [complain to the ICO](https://ico.org.uk/make-a-complaint/) or the relevant supervisory authority. Do not describe Ofcom as an individual compensation or moderation-appeal tribunal.

Review open cases regularly and keep complainants informed of material delays. Retain the minimum complaint record needed for accountability under the privacy retention procedure; close and review it when the case finishes. A public correction must not reveal confidential complaint evidence.

## Withdrawing a database-backed listing

This operation is for an authorised application operator. It removes public access through mathStoa while retaining the private draft, submitted revisions, decisions and repository-verification records. It is not erasure, an account ban or removal of the external repository.

From the service's application environment, first inspect without changing anything:

```sh
python server/manage.py unpublish_submission project-<id>
```

After confirming the affected listing and recording the reason, supply the published revision database ID shown by that preview:

```sh
python server/manage.py unpublish_submission project-<id> --expected-revision <revision-id> --execute
```

The command locks the submission, rejects a changed publication pointer when `--expected-revision` is supplied, and clears that pointer. Keep its metadata output with the operator and reason in the restricted case record. It does not put private titles, contributor details or the complaint reason in command output. Check whether the same project exists in `docs/data/projects.json`; a duplicate there requires a separate reviewed archive edit. Confirm the archive JSON, project page and feed after their cache lifetime, and invalidate an intermediary cache if necessary. Copies already downloaded by feed readers and external websites cannot be recalled by this operation.

The existing **Request changes** action does not withdraw an already published revision. Do not use destructive `erase_submission` merely for a temporary investigation. Do not claim an approved historical revision can be approved again: the model deliberately prevents that.

For restoration, resolve the complaint, compare the intended public text with the preserved revision, and ask the owner to save a new draft revision and submit it. Saving creates a new revision even if the intended text is unchanged. A moderator then uses the normal approval route, including repository verification and validation, and records the restoration rationale. If the owner is unavailable, leave the listing withdrawn until a separately authorised restoration method is reviewed; do not bypass the approval checks with a direct database edit. The command does not prevent a later normal approval, so tell all moderators about an unresolved hold and check for pending revisions.

## Operational evidence still needed

Both operators receive the shared mailbox. They must confirm delivery, how they record who is handling each case and absence cover, a private case-record location, and a successful test withdrawal/restoration. These are deployment/operation tasks, not facts established by adding this document. Record dates and results privately, and update the public contact instructions if the route changes.
