# Online safety assessment and operations

Prepared: 4 November 2026. Scope: the mathStoa archive, account/submission service and links it publishes. Status: **provisional assessment; operator decisions and operational evidence remain outstanding**. This is an implementation record, not a legal opinion, regulatory approval or completed compliance certification.

## Service and scope

The code stores private project drafts, freezes submitted revisions, and requires staff approval before publishing an abstract, attribution and links. Approval exposes the user's submitted text; editing a draft does not replace the public version. The archive distributes approved records. There are no on-site comments, direct messages, media uploads, payments or personalised recommendations in the reviewed implementation. External repositories and websites can change after approval. GitHub hosts a separate legacy submission/discussion route.

The named operators are Tommaso Rosati and Simon Gabriel, acting personally. Confirm their operating locations, the service's UK connection, and the first date the relevant functionality was available to UK users. The README records deployment of the account service on 3 November 2026; that is evidence to check, not a determination of the legal start date.

**Working scope conclusion:** treat the service as potentially a regulated user-to-user service while resolving scope. Direct user submissions that others encounter can satisfy section 55 of the Online Safety Act. Human approval is a safeguard, not an established exemption; user-generated material does not become provider content simply for the comments-on-provider-content exemption. The single-catalogue search does not itself amount to searching multiple websites/databases. These are conclusions from the observed workflow and [the Act](https://www.legislation.gov.uk/ukpga/2023/50/section/55/data.html), to be checked with [Ofcom's scope tool](https://www.ofcom.org.uk/os-toolkit/regulation-checker/regulation-checker).

If covered, complete and retain the applicable assessments, select proportionate safety measures, publish the relevant moderation/reporting terms, and review effectiveness. New covered services normally have three months for the initial illegal-content assessment. Confirm the actual deadline from the relevant launch date; do not assume a pre-existing service receives a fresh period. See [Ofcom's illegal-content duties](https://www.ofcom.org.uk/online-safety/illegal-and-harmful-content/illegal-content-duties-under-the-online-safety-act).

## Provisional illegal-content assessment

The narrow research purpose, short text submissions and approval gate support a **provisional low exposure assessment**, not a finding of negligible risk. No audience counts, age breakdown, incident history, moderator workload, mailbox cover or live control-effectiveness evidence was available for this assessment. Final category ratings must be recorded after those facts and the current Ofcom risk profiles have been examined. Serious harm can result even from a rare event.

The following is an initial service-specific review of the 17 priority categories, using the [Ofcom risk-assessment guidance](https://www.ofcom.org.uk/siteassets/resources/documents/online-safety/information-for-industry/illegal-harms/risk-assessment-guidance-and-risk-profiles.pdf?v=419933). Each row's likelihood is provisionally low, conditional on effective moderation; none has been signed off. Both readers encountering material and the service facilitating an offence must be considered.

| Category | Plausible use of this service and possible harm | Review focus |
| --- | --- | --- |
| Terrorism | A disguised abstract or link promotes an organisation or facilitates an attack; potentially severe physical harm. | Text, context and link destinations. |
| Harassment, stalking and threats | False attribution or targeted text exposes or intimidates a researcher. | Names, allegations, private details and repeated submissions. |
| Coercion and control | Attribution or repeated notices become a means to target a person. | Reports from affected contributors and notification misuse. |
| Hate offences | An abstract contains unlawful targeting or incitement. | Read the entire proposed public text. |
| Abuse involving intimate images | A link distributes abusive material despite no native image uploads. | Suspicious destinations and urgent reports; do not collect copies. |
| Extreme pornography | Links could direct readers to unlawful material. | Reject such content and withdraw identified links promptly. |
| Child sexual exploitation or abuse | Text or links can facilitate exploitation; harm is extremely serious. | Urgent escalation, no copying/distribution of suspected material. |
| Adult sexual exploitation | A disguised project advertises or facilitates exploitation. | Project purpose, links and complaints. |
| Immigration offences | Links or misleading project descriptions could facilitate offences. | Context; do not equate lawful immigration discussion with illegality. |
| Human trafficking | A purported project could recruit or direct people to exploitation. | Recruitment claims and destination changes. |
| Fraud and financial crime | Impersonated contributors or a compromised link solicit credentials or money. | Attribution authority, repository control, suspicious destinations. |
| Criminal proceeds | A link could solicit or channel illicit transfers. | Reject unrelated transactions or financial solicitation. |
| Encouraging or assisting suicide | Text or links could encourage or enable lethal action. | Urgent content review and escalation. |
| Drugs and psychoactive substances | Links could facilitate unlawful sales. | Destination and project-scope review. |
| Weapons offences | Disguised material could facilitate unlawful acquisition or use. | Context and links; distinguish lawful mathematics from facilitation. |
| Foreign interference | False scholarly identities or projects could support unlawful influence. | Identity claims, impersonation reports and coordinated misuse. |
| Animal welfare offences | Text/links could promote or facilitate unlawful cruelty. | Scope review and complaints. |

Other unlawful content, including infringement or unlawful disclosure of personal data, must be considered where evidence indicates it is likely. Mathematical disagreement, unpopular lawful research and unproven results do not by themselves establish illegality. Moderators must not substitute a judgment of mathematical correctness for a content-safety judgment.

## Safeguards and limits

Code evidence: `server/submissions/models.py` gates publication and preserves submitted revisions; `server/submissions/views.py` limits private draft access and serves only published revisions; repository verification is required at submission and approval. These checks reduce opportunities for abuse but do not prove copyright ownership, a contributor's consent, lawful content or enduring safety of an external link. A moderator may approve their own submission, so there is no independent second-person control.

Operational requirements under this procedure:

1. Before approval, read all public text and attribution, check project scope and permitted links, and record the decision. Avoid opening suspected abusive material unnecessarily; escalate uncertainty.
2. Review reports through [the complaint procedure](COMPLAINTS.md). Check external destinations again when a report or suspicious change is identified. Record what was checked without retaining unnecessary sensitive material.
3. Remove access to known illegal material swiftly. A request for changes to a new revision does **not** remove an earlier published revision. Use the withdrawal procedure in `COMPLAINTS.md`; do not erase evidence merely to hide a listing.
4. Keep a private record of the accountable operator, moderation cover, reports, decisions, response times and relevant control failures. Test that the public archive and project page reflect a withdrawal.
5. Reassess before significant feature changes and after a serious incident, changed audience or new evidence. Adding comments, messaging, automatic publication, uploads or broader search requires fresh review.

## Children's access assessment

**Stage 1: children can access the service.** Reading is public. The reviewed implementation has no highly effective age assurance or access control excluding under-18s. ORCID, institutional email, sign-in or an adult-oriented description must not be treated as proof of age.

**Stage 2: unresolved.** The specialised research focus may limit appeal, but mathematically interested school pupils may benefit from and use the archive. Actual child usage, outreach to schools, subject matter, presentation and attraction have not been assessed. Lack of age data is not evidence that the child-user condition is absent. Evaluate both actual usage and whether the service is likely to attract a significant number of children, in this service's context, without collecting unnecessary identity documents.

Record the evidence and outcome. If likely to be accessed by children, complete the children's risk assessment and implement the applicable protections. If concluding otherwise, retain the detailed supporting evidence and review within 12 months or earlier when required. New covered services normally have three months for the initial access assessment. No final negative conclusion is made here. [Ofcom children's-access guidance](https://www.ofcom.org.uk/online-safety/illegal-and-harmful-content/childrens-access-assessment-duties-under-the-online-safety-act).

## Decisions and evidence still required

| Action | Responsible person | Current status |
| --- | --- | --- |
| Confirm operating locations, UK connection and earliest relevant launch; record the OSA scope conclusion and applicable deadlines. | Tommaso and Simon jointly | Unconfirmed. |
| Share accountability for online safety equally; record who handles each report and arrange review and absence cover. | Tommaso and Simon jointly | Equal responsibility confirmed by the operator on 4 November 2026; practical cover still to verify. |
| Check each provisional category against current Ofcom risk profiles, audience/usage and actual operation; record final risks and code measures or justified alternatives. | Tommaso and Simon jointly | Initial analysis only. |
| Complete children's-access stage 2; if applicable, complete the children's risk assessment and protections. | Tommaso and Simon jointly | Open. No adult-only conclusion. |
| Verify private report delivery, published policy accessibility and a reversible withdrawal/republication exercise in a test environment. | Operators and application maintainer | Code/tooling supplied; production operation not verified. |
| Record assessment approval date, evidence location and next review, without storing complaint contents in this public repository. | Tommaso and Simon jointly | Not approved. |

Sources above were reviewed on 4 November 2026. A change in law, service features or operating evidence may change this assessment. EU Digital Services Act and US/state obligations require separate jurisdictional review; this document does not determine them.
