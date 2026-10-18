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

Feedback is submitted independently from payroll. The dashboard's Submit feedback
button, desktop navigation and phone Feedback tab open `/staff/feedback/`. Staff
choose one of their saved shifts, fill the form, then choose Submit feedback or
Save feedback draft. The page filters sessions by month and To do / Submitted /
All sessions, with pagination. A shift must be saved first so feedback is attached
to the correct date and author; payroll submission is not required.

Timesheets can be submitted with missing, draft or outdated feedback. There is no
feedback step or non-respite checkbox in the staff timesheet editor. Feedback can
be submitted before or after timesheet submission/approval without changing the
payroll status, hours, review or notification. Full respite feedback is still
validated when submitted; other work uses an explicit not-applicable confirmation
on the separate feedback form. Existing responses and confirmations are retained.

Core session and GIRFEC answers, the accuracy confirmation and relevant conditional
details are checked on the server. Saving a draft permits incomplete answers but
still validates field types and lengths. Changing any saved shift invalidates its
completion fingerprint until feedback is reviewed again. The existing revision
check prevents stale browser tabs from overwriting newer work. Feedback saves and
timesheet saves share that revision lock; a feedback save updates the revision and
last-updated timestamp, but never changes payroll status or amounts. Neither the
bulk nor classic timesheet submission route checks feedback completion.

## Storage and access

Migration 0006 adds one SessionFeedback table, linked to each TimeEntry. It does not
change staff credentials or delete/modify existing timesheets. Answers have a schema
version, completion time and shift fingerprint. Each week's timesheet has independent
feedback. Removing an editable shift/draft also removes its attached feedback; the
existing audit history retains action metadata, never the private care answers.

Only the author may edit feedback, including for submitted/approved timesheets.
The author and enabled Administrator may read it. Each Feedback home lists only
the signed-in user's sessions, even for administrators. Existing team-review pages
retain their read-only status/access links. Finance gets completion status only,
not access through a direct feedback URL; care answers are excluded from payroll downloads,
notification bodies, Excel snapshots and general audit details. Existing login,
CSRF, MFA, no-store responses and idle-session protections also apply here.

No feedback is sent to Charitylog. No new emails, invitations, safeguarding alerts,
care-review permissions or background services are added. Staff must continue the
organisation's Charitylog and urgent-concern reporting process. Access for other
care managers can be added separately when explicitly authorised.
