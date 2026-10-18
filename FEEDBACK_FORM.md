# Respite Feedback in the timesheet portal

This implements the form documented in the owner's ChatGPT project **finally done**,
using the **Charity Log Data Analysis** handover (Respite Feedback field list), followed
by the later Samantha corrections recorded in **Pre Assessment Cleanup** and
**Map Charitylog SOPs**. It is a portal copy of those documented questions, not a live
Charitylog API integration or a verified export of today's Charitylog configuration.

The recovered sections are Session details, Child wellbeing, Outdoor Risk Assessment,
Prescribed Medication Administration, Session feedback, and GIRFEC and activities.
The handover also listed empty/underused sections without questions; no questions
have been invented for those sections.

Applied corrections:

- Put concerns and reporting to the line manager directly below the session date.
- Omit both minimum-location-safety questions that Samantha asked to remove.
- Use one Respite provider feedback field, and retain the separate GIRFEC Activities
  undertaken field. Do not recreate the duplicated activity/provider feedback field.
- Keep one copy of the family-risk-awareness and medication-date questions.
- Put misplaced fields into their documented sections; hazards use multiline text.
- Keep the source's Additional comments field; its removal was only a suggestion.
- Unknown historical dropdown options use free text rather than guessed choices.
  Questions with an explicit Yes/No meaning use Yes/No with no preselected answer.
- Add routing questions for applicability, outdoor assessment and medication so
  staff do not have to invent answers for irrelevant work. These are portal controls,
  not claims that those routing questions exist in Charitylog.
- Signature fields accept typed names; they are not captured handwritten signatures.
  Parent/carer signature is optional and must not be entered on someone else's behalf.

## Flow and validation

Save shifts, open Session feedback for each respite shift, save drafts as needed,
then Complete feedback and Submit timesheet. Respite categories cannot use Not
applicable. For other categories, staff must either complete feedback or explicitly
confirm that no respite session took place. That declaration is recorded per shift.
The bulk declaration never replaces an existing full feedback response.

Core session and GIRFEC answers, the accuracy confirmation and relevant conditional
details are checked on the server. Saving a draft permits incomplete answers but
still validates field types and lengths. Changing any saved shift invalidates its
completion fingerprint until feedback is reviewed again. The existing revision
check prevents stale browser tabs from overwriting newer work. Submission checks
apply to both bulk and classic routes and roll back failed submissions atomically.
All new submissions (including returned claims) require current feedback. Existing
submitted/approved claims are not rewritten or retrospectively marked complete.

## Storage and access

Migration 0006 adds one SessionFeedback table, linked to each TimeEntry. It does not
change staff credentials or delete/modify existing timesheets. Answers have a schema
version, completion time and shift fingerprint. Each week's timesheet has independent
feedback. Removing an editable shift/draft also removes its attached feedback; the
existing audit history retains action metadata, never the private care answers.

Only the author may edit an editable timesheet's feedback. The author and enabled
Administrator may read it. Finance gets completion status only, including when
using a direct feedback URL; care answers are excluded from payroll downloads,
notification bodies, Excel snapshots and general audit details. Existing login,
CSRF, MFA, no-store responses and idle-session protections also apply here.

No feedback is sent to Charitylog. No new emails, invitations, safeguarding alerts,
care-review permissions or background services are added. Staff must continue the
organisation's Charitylog and urgent-concern reporting process. Access for other
care managers can be added separately when explicitly authorised.
