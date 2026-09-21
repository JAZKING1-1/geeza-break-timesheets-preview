# Geeza Break — timesheet review preview

A separate Django project for reviewing the staff timesheet workflow. This repository
contains only the portal and its public logo. It has no dependency on the main website,
no copied Git history, no real staff roster, no payroll database and no Mailjet credentials.

## Review the workflow

1. Open the hosted link on a phone, tablet or computer.
2. Choose **Employee** and enter the private review code shared by the project owner.
3. Choose any of the twelve months in the annual calendar, or change the year. Saved
   shift dates have a dot; each month shows its total and status. Enter each shift in a horizontal row with date, service / work type,
   start/finish, break and purpose. On a phone, swipe across the grid; the date stays
   visible. The saved total and save/submit controls stay below the grid.
4. Save a draft or submit the complete timesheet in one action.
5. Use **Switch view → Finance** to review, return or approve another demo user's sheet.
6. Export a summary or detailed CSV, or print/save a PDF.
7. Switch to **Administrator → Email previews** to see the submission notification.

All accounts are fictional. Shared demo users can see each other's changes when using
the same role. Do not enter real employee, family, care, pay or other sensitive information.
Account creation, invitations, password changes and MFA changes are disabled in review mode.
The review code is validated server-side, rate limited and never embedded in page source.
Share it privately with reviewers; it is separate from the website link.

## Run locally

Use Python 3.11:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py bootstrap_preview
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8010
```

Open http://127.0.0.1:8010. Read the locally generated code from
`.local/preview-access-code.txt`. Keep `.local/` private and out of Git.

## Render review deployment

Deploy this repository as a Python web service with:

- Region: Frankfurt; plan: Free.
- Build command: `bash build.sh`.
- Start command: `bash start.sh`.
- Health check: `/health/`.
- `APP_ENV=preview`.
- A random `DJANGO_SECRET_KEY` of at least 50 characters.
- A separate random `PREVIEW_ACCESS_CODE` of at least 24 characters.

Render's external hostname configures the allowed host and HTTPS URL automatically.
The repository also contains `render.yaml` for a repeatable Blueprint deployment.
It generates the review code in Render and asks for a Django secret of at least
50 characters; no credential values belong in source control.

This preview deliberately uses an isolated disposable SQLite database. Render's free
service sleeps after inactivity and its local files can disappear on sleep, restart or
redeploy. The next start restores fictional sample data. First visits after sleep can
take about a minute. See [Render's free service limits](https://render.com/docs/free).
Use this link for design/workflow review, not payroll record keeping.

Email defaults to private preview files, even if general Mailjet keys exist in the
environment. Real account invitations remain disabled. The preview contains no connection
to the main website database.

## Services and calendar

The January–December overview works across years and includes correct leap-year dates.
It shows only the signed-in employee's saved shifts. Opening a month uses the existing
ownership-protected monthly editor; simply browsing a year does not create records.

Service names were checked on 21 September 2026 against the public
[services page](https://www.geezabreak.org.uk/services/) and
[referral options](https://www.geezabreak.org.uk/referral/): Family Support, Respite Sitting,
Respite Care, Geeza Chance, Family Support Group Work and Kinship Care. Administration,
Project Work, Training and Other remain available for internal work. The previous generic
Respite option is retained on historical records and retired for new shifts.
`bootstrap_preview` synchronises this catalogue without relabelling historical entries.

The existing date, service, start/finish, break, purpose and calculated-total layout is
consistent with the basic structure of [Microsoft's timesheet templates](https://excel.cloud.microsoft/create/en/timesheet-templates/).
This is an hours/approval workflow, not a wage or overtime calculator.

## Optional claim notification email

On this separate Render service, set the following **private environment variables**:

- `TIMESHEET_EMAIL_DELIVERY=mailjet`
- `TIMESHEET_MAILJET_API_KEY` and `TIMESHEET_MAILJET_API_SECRET`: working Mailjet credentials.
- `TIMESHEET_FROM_EMAIL`: an active, verified Mailjet sender address.
- `TIMESHEET_NOTIFICATION_EMAIL`: the complete, confirmed reviewer email address.

Keep delivery set to `preview` until the credentials and addresses have been verified.
The application refuses incomplete email configuration when Mailjet delivery is selected.
This explicit opt-in sends only submission/resubmission notifications to the configured
reviewer. Drafts do not send email. Seed data never queues or sends emails on startup or
redeployment. Account emails still remain local in review mode; demo subjects are marked
`[Review demo]`. No keys are committed and no original website configuration is changed.

Mailjet acceptance or failure is shown on the submitted timesheet. A delivery failure
does not undo the saved claim or resend it automatically; Mailjet acceptance does not
guarantee inbox arrival. `send_timesheet_notifications` processes pending notices after
an interrupted request, without retrying completed or uncertain sends.

## Checks

```powershell
.\.venv\Scripts\python.exe manage.py test staff_portal
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

The inherited portal tests cover ownership, approvals, stale edits, invalid/overlapping
shifts, clock changes, password recovery, CSRF, MFA, CSV safety and batch submission.
Preview-specific tests verify access-code protection, role switching, blocked account
changes, fictional-only seeding and email isolation. The layouts use labelled native
controls, visible keyboard focus, responsive rows and accessible validation summaries.
Formal assistive-technology acceptance testing remains a separate step.

The brighter interface uses 16px form controls, 50px main action targets, underlined
help/shortcut links, a visible current navigation item and distinct keyboard focus.
Unsaved edits, pending submissions and server-confirmed results have text feedback;
colour is supplementary. The main text/button colour pairs were checked at 4.5:1 or
better. A pending-submit guard retains the clicked button's action in the POST and
prevents repeat submission while waiting. The layout was checked at 320px and 390px
phone widths. This follows [W3C guidance for older users](https://www.w3.org/WAI/older-users/)
and [status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html);
it is not a claim of a full WCAG audit or testing with older participants.

## Moving beyond a review

`APP_ENV=production` removes review access and refuses to start without persistent
PostgreSQL, a verified HTTPS URL, real Turnstile configuration and configured Mailjet
delivery. Install a PostgreSQL driver, provision backups, restore-test them, confirm
staff addresses, configure an administrator and perform a separate production review.
Do not use `start.sh` for live payroll: its bootstrap is explicitly restricted to previews.
