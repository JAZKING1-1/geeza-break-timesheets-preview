# Geeza Break — timesheet review preview

A separate Django project for reviewing the staff timesheet workflow. This repository
contains only the portal and its public logo. It has no dependency on the main website,
no copied Git history, no real staff roster, no payroll database and no Mailjet credentials.

## Review the workflow

1. Open the hosted link on a phone, tablet or computer.
2. Choose **Employee** and enter the private review code shared by the project owner.
3. Open the current month. Add rows with date, work type, start/finish, break and purpose.
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

Email is always written to private preview files in review mode, even if Mailjet keys
exist in the environment. No real inbox receives the demo notifications. Real account
invitations are disabled. The preview contains no connection to the main website database.

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

## Moving beyond a review

`APP_ENV=production` removes review access and refuses to start without persistent
PostgreSQL, a verified HTTPS URL, real Turnstile configuration and configured Mailjet
delivery. Install a PostgreSQL driver, provision backups, restore-test them, confirm
staff addresses, configure an administrator and perform a separate production review.
Do not use `start.sh` for live payroll: its bootstrap is explicitly restricted to previews.
