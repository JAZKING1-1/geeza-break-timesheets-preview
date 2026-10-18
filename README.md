# Geeza Break — staff timesheets

A separate Django project for recording worked hours, submitting monthly claims and finance review. This repository
contains only the portal and its public logo. It has no dependency on the main website,
no copied Git history, no real staff roster, no payroll database and no Mailjet credentials.

The [hosted staff portal](https://geeza-break-timesheets-preview.onrender.com/) now
runs in production mode with Neon PostgreSQL, Cloudflare Turnstile and Mailjet.
Its existing URL still contains `preview`; the live application has personal accounts
and no demo login. Credentials and records are stored outside this repository.

## Record and review hours

1. Open the staff portal on a phone, tablet or computer.
2. Sign in with your registered work email and personal password.
3. Choose **Add new timesheet** (or **Add work** in the navigation), select the first date you worked, then choose
   **Continue to shifts**. An existing draft opens so you can continue it; an already
   submitted month shows a clear explanation and a link to the claim. Use the date picker inside each shift.
   For another month or year, expand **Different month or year?**, choose a date and
   select **Open timesheet**. The matching sheet opens with that date in a new row.
   Enter each shift in a horizontal
   row with date, service / work type, start/finish, break and purpose. On a phone,
   the same entries become labelled shift cards with large controls. Tablets can
   scroll the horizontal grid. The saved total and save/submit controls follow the entries.
4. Save a draft or submit the complete timesheet in one action.
5. An authorised finance reviewer can review, return or approve another employee's sheet.
6. Download a saved timesheet as Excel, or print/save a PDF. Save draft before downloading new changes.
7. Submission email status is displayed on the timesheet; email delivery must be configured.

**Review mode only:** all accounts are fictional. Shared demo users can see each other's changes when using
the same role. Do not enter real employee, family, care, pay or other sensitive information.
Account creation, invitations, password changes and MFA changes are disabled in review mode.
The review code is validated server-side, rate limited and never embedded in page source.
Share it privately with reviewers; it is separate from the website link.

## Personal accounts and production

See [PRODUCTION_SETUP.md](PRODUCTION_SETUP.md) for the live configuration,
owner-account flow, dedicated database, costs and cutover checks.
`APP_ENV=development` uses personal accounts in a separate local database.
`APP_ENV=production` requires persistent PostgreSQL, real CAPTCHA and email delivery.
The one-use setup link lets the owner choose their own password. Startup never resets
that password. Sample accounts and role switching are unavailable outside review mode.
The sign-in page's **Set up my account** link sends a fresh activation email
only to an enabled, registered staff address that still needs activation. It does not
allow public self-registration or reveal whether an address belongs to the organisation.

### Staff and finance access

An administrator uses **Staff access → Add employee** to enter a confirmed work email
and choose **Employee**, **Finance**, or **Administrator**. After saving, choose **Invite**
beside the person. Each recipient sets their own password through the private email link.
The site does not publish or share staff passwords. Existing users can use **Forgot your password?**.

Give the finance officer the **Finance** role. Finance users land on **Finance dashboard**
after signing in and can still open **My own timesheets**. Staff without finance access
can view and download only their own records.

### Monthly deadlines and follow-up

The staff dashboard, each timesheet and the Finance dashboard show the submission
deadline for the selected month. Payday is the **last Thursday of the month**, and
the deadline is **14 calendar days earlier**, calculated using UK local dates. The
current-month dashboard advances when the month changes; older timesheets keep their
own month's deadline. The due date is inclusive. Late submissions remain possible.
This rule does not move a deadline for bank holidays or change the monthly claim period.

Finance and Administrators can select **Who still needs to submit?** / **Needs chasing**
to list enabled staff with missing, draft or returned sheets, or accounts awaiting setup.
The filter also applies to Excel, ZIP and CSV downloads. **Email employee** opens the
reviewer's email app; it does not send a reminder automatically. Give team access only
to authorised reviewers, such as Karen using her confirmed work email and the Finance role.

### Delete an unsubmitted draft

An employee can choose **Delete draft** beside their own timesheet or at the bottom of
the editor. A separate confirmation page shows its month, shift count and saved hours.
Deletion removes the draft and entries, retains the audit history, and allows a new
timesheet for that month. A stale confirmation cannot delete newer work. Finance and
Administrators cannot delete another employee's draft. Submitted, returned and approved
payroll records, including emailed claim snapshots, are protected from deletion; use
the review/correction flow for those records. No schema migration is needed for this update.

### Finance downloads

Choose the month, employee and status, then **Show timesheets**. The displayed totals
and every export follow those filters:

- **Download team Excel**: one workbook with a team summary and all recorded shifts.
- **Download individual files**: a ZIP containing that summary plus one Excel file for
  every matching timesheet. Unique timesheet references prevent duplicate names overwriting files.
- **Excel** beside an employee: their current saved timesheet, including status and revision.
- Summary CSV, detailed CSV and Print/PDF remain available below the team list.

Missing timesheets appear in the summary; no individual file is invented for them.
Draft, returned and submitted hours are separated from approved hours. Downloads show
current saved records, while submission emails retain their original immutable snapshots.
Downloading does not approve hours, change a timesheet or send an email. All downloads
require an active authorised account, are marked private/no-store and are recorded in the audit history.

## Phone and app navigation

On phones, the bottom navigation provides **My hours**, **Add work**, **Finance** for
reviewers, and **More** for account and staff settings. The layout accounts for phone
safe areas, uses labelled controls and keeps keyboard focus and action feedback visible.
The same pages retain a desktop sidebar and horizontal entry rows on larger screens.

Use **More → Add to home screen** for device-specific instructions. Compatible browsers
can install the web app directly; iPhone users can add it through Safari's Share menu.
An internet connection is required. The service worker never caches staff pages or
queues submissions. An offline notice prevents a form being presented as saved when
there is no connection; a successful server response remains the confirmation of saving.

## Run the review locally

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

The following is for creating an additional disposable review environment. Do not
apply these preview settings to the live staff service described above.
Deploy a separate copy as a Python web service with:

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

The date popup is in the entry workflow, beside the timesheet controls. It accepts any
valid day, month and year, including leap days, and opens the correct monthly sheet.
The saved-month table retains all years. Row dates are validated against their month;
choosing another month above the rows preserves existing records and approvals.

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

## Submission email with Excel attachment

When a member of staff chooses **Submit timesheet**, the server generates a real
`.xlsx` file and queues one email to the configured reviewer. For this rollout the
recipient is `devansh.sharma@geezabreak.org.uk`. The workbook contains the employee,
claim month, submission reference, dated shifts, service, start/finish, unpaid breaks,
purposes and total worked hours. It is a submitted claim, not a payment approval.
Draft saves do not send email.

Delivery follows the official referral mailer's `mailjet_rest.Client` / `send.create`
workflow, using the Mailjet v3.1 attachment format. This is a separate implementation
in this repository: it never imports, calls or changes the original website.
Only the configured reviewer receives timesheet emails; referral recipients are not copied.
The Excel bytes are saved with the notification inside the submission transaction,
so delayed delivery cannot attach later edits. Staff text is written as plain text,
not executable spreadsheet formulas or hyperlinks. Clock-change hours come from the
validated worked minutes, and cached totals work in email/mobile viewers.

On this separate Render service, set the following **private environment variables**:

- `TIMESHEET_EMAIL_DELIVERY=mailjet`
- `TIMESHEET_MAILJET_API_KEY` and `TIMESHEET_MAILJET_API_SECRET`: working Mailjet credentials.
- `TIMESHEET_FROM_EMAIL`: an active, verified Mailjet sender address.
- `TIMESHEET_NOTIFICATION_EMAIL`: the complete, confirmed reviewer email address.

The existing referral Mailjet account is reused through this service's private
`TIMESHEET_MAILJET_*` variables. On 22 September 2026, Mailjet authentication and the
sender were verified, and a clearly labelled fictional claim with an Excel attachment
was accepted for delivery to Devansh's work email. This verifies provider acceptance,
not inbox arrival. The original website and its environment were not changed. Never
put credentials into source files or chat.

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
Old queued notices without an Excel snapshot fail safely instead of attaching a
potentially different current timesheet. Previously sent notices are not re-sent.
Local previews include the same MIME attachment, but never send email.

## Website content review

The separate timesheet service also hosts public, read-only pages for Gillian to
review proposed website updates at `/website-review/`:

- `brighter-beginnings/`: the supplied flyer, service bio and six full testimonials.
- `adrs-respite-sitting/`: the ADRS/FASS service replacing Respite Care in this review.
- `family-support-group-work/`: regular groups and workshops, including Brighter Beginnings.

The `website_review` app has no models, forms or database queries. These pages do
not grant staff access or alter timesheet/email workflows. They are labelled as
website previews and send noindex headers; they are shareable without a login.
Only the supplied public-facing flyer is included, not email screenshots.
Deploy these changes only to `geeza-break-timesheets-preview`; the original
`www.geezabreak.org.uk` website and its repository are outside this change.

## Checks

```powershell
.\.venv\Scripts\python.exe manage.py test staff_portal
.\.venv\Scripts\python.exe manage.py test website_review
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
`start.sh` selects the personal-owner bootstrap for production, and the fictional seed
only for review mode. Never switch the hosted service before completing the production
configuration and verification steps.

## Registering staff without sending invitations

An administrator can register confirmed work emails in Staff access, then share
`/staff/login/` in a common welcome message. Staff choose **Set up my account** or
**Forgot your password?**; both send a private, single-use setup link for a
registered account that has no password yet. Existing active accounts use normal
password recovery. Unknown, disabled and demo profiles receive no account email.
Registration itself sends no emails, creates no shared passwords, and grants
Employee access unless Finance is explicitly selected. Finance can review and
download the team's timesheets; Employees can access only their own records.

For an authorised batch, keep a JSON file outside version control, containing a
fresh UUID `id`, the existing administrator's `owner_email`, and a `staff` list
of `name`, `email`, `job_title` and `role` (`employee` or `finance`). An optional
`previous_name` may match an unused placeholder with no email or linked user.
Run `python manage.py register_staff --file /private/roster.json` to validate
without saving, then repeat with `--apply`. Existing credentials and MFA are
preserved. Conflicting, protected or disabled records abort the whole batch.

On the dedicated service, a temporary private `STAFF_REGISTRATION_BATCH`
environment variable can hold the same JSON. `start.sh` applies it after the
existing owner is verified. Successful batches are recorded in the audit log and
will not reapply on restarts. Clear this one variable after completion. Never
replace the service's complete environment, add a roster to Git, or use this
procedure against the original website. No database migration is needed.
