# Personal staff accounts and production cutover

This applies **only** to the separate timesheet service:
`srv-dalau4m7bikc73evjqqg` in workspace `tea-d2vee18gjchc73b5fsg0`.
Do not edit the original Geezabreak website, its database, service or `.org.uk` domain.

## Prepared application changes

- Date worked opens a native calendar beside the timesheet entry controls. Selecting
  any day/month/year opens that monthly timesheet and prefills the date in a new row.
- Each row has a date picker for that month. The server validates the month, breaks,
  overlaps and ownership. It still saves/submits all rows together.
- `APP_ENV=production` uses individual email/password accounts, with no shared review
  code, role switching or demo seeding. The existing permissions and audit trail apply.
- `bootstrap_owner` creates one personal administrator on a **fresh database**. It
  refuses a database containing demo profiles or existing unrelated users. An existing
  owner's password, setup-link expiry and account state are never reset on startup.
- The owner sets their own password using a private, one-use link, valid for 24 hours.
  The database stores only its hash. Authenticator protection is available under
  Account security. The administrator cannot approve their own claim.

This records hours and approvals for finance to process. It does not calculate wages
or send payroll payments. Finance should validate the exported hours before payroll.

## Live hosting configuration — 22 September 2026

- The separate Render service now runs with `APP_ENV=production` on its Free plan.
- Persistent records use the `geezabreak_timesheets` database in the owner's Neon
  Free project. Connections require TLS. No local or hosted demo records were imported.
- The dedicated **Geeza Break Staff Timesheets** Turnstile widget uses Managed mode
  and allows only `geeza-break-timesheets-preview.onrender.com`. Pre-clearance is off.
- General account email and Excel claim notifications use the existing verified
  Mailjet account. Notifications go to `devansh.sharma@geezabreak.org.uk`.
- Startup created Devansh Sharma's personal administrator account. The owner must
  choose their own password through the private, one-use setup page.

No paid database or service has been purchased. Keep both hosting plans free, monitor
their allowances, and arrange private backups before collecting real claims.
Free hosting can sleep, so the first page load may take longer after inactivity.

Sources: [Neon plans](https://neon.com/docs/introduction/plans),
[Render free limits](https://render.com/docs/free).
Render's free databases expire after 30 days. The current SQLite review database is
disposable; neither is the intended permanent store for payroll records. Do not import
fictional sample data into the new database. Firebase would require a separate backend
migration; it is not connected by this implementation.

The live site is https://geeza-break-timesheets-preview.onrender.com/.
The existing hostname retains the word `preview`, but the application now uses real
accounts and persistent storage. Shared demo login and role switching are disabled.
Backup restoration and a complete staff/finance approval exercise remain operational
checks; this configuration change does not claim those have been completed.

## Required private configuration

These are configured privately on the separate service. Use this table when restoring
or recreating that deployment; never replace its database with the disposable review one:

| Setting | Value or source |
| --- | --- |
| `DATABASE_URL` | Private connection URL for the new dedicated PostgreSQL database, with TLS required for an external provider |
| `DJANGO_SECRET_KEY` | Keep the existing strong private value |
| `MAILJET_API_KEY`, `MAILJET_API_SECRET` | Working, verified Mailjet credentials for account invitations/recovery |
| `DEFAULT_FROM_EMAIL` | An active sender in that Mailjet account |
| `TIMESHEET_EMAIL_DELIVERY` | `mailjet` |
| `TIMESHEET_MAILJET_API_KEY`, `TIMESHEET_MAILJET_API_SECRET` | Working Mailjet credentials for claim notifications |
| `TIMESHEET_FROM_EMAIL` | The verified sender |
| `TIMESHEET_NOTIFICATION_EMAIL` | `devansh.sharma@geezabreak.org.uk` |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Real keys from a separate Cloudflare Turnstile widget |
| `TURNSTILE_HOSTNAMES` | `geeza-break-timesheets-preview.onrender.com` |
| `BOOTSTRAP_OWNER_EMAIL` | `devansh.sharma@geezabreak.org.uk` |
| `BOOTSTRAP_OWNER_NAME` | `Devansh Sharma` |
| `BOOTSTRAP_OWNER_TOKEN` | A private random token of at least 32 characters |

Working referral Mailjet credentials were verified on 22 September 2026 and configured
only on the separate timesheet service. Mailjet accepted a fictional test claim with
an Excel attachment for `devansh.sharma@geezabreak.org.uk`. General account-mail
variables are now configured for production invitations and recovery as well.
Do not paste keys into Git, chat, public pages or build commands. Production refuses
SQLite, incomplete CAPTCHA, known CAPTCHA test keys and preview email delivery.

## Cutover and verification

1. Connect a separate free PostgreSQL database and use its TLS connection URL. Keep
   credentials private and use a region close to the Render service where available.
   Arrange backups that fit the charity's budget; do not assume free hosting includes them.
2. Configure the above secrets and verified sender; add only this app's hostname to its
   separate Turnstile widget. Verify Mailjet authentication and sender status.
3. Prepare the owner's private link using the random token:
   `/staff/set-password/<BOOTSTRAP_OWNER_TOKEN>/`. Store it privately, not in this file.
4. Set `APP_ENV=production` and deploy. `start.sh` runs migrations and `bootstrap_owner`.
   Demo startup is not called, and none of the old demo records enter the new database.
5. The owner opens the private link, chooses their own password and enables an
   authenticator. Confirm sign-in, account ownership and blocked shared-role routes.
6. Test a clearly identified sample submission and Mailjet acceptance, then verify
   approval with a separately authorised reviewer. Confirm data survives a redeploy.
7. Export and restore a database backup into an isolated test database and verify the
   restored records. Agree retention and payroll sign-off with finance before staff use.

If initial activation expires, the owner can choose **Set up your staff account** on
the sign-in page and enter the registered work email. CAPTCHA and request limits apply;
only a registered, enabled, inactive account receives the one-use setup email. Active
accounts use password recovery. Do not reset a person's password by rerunning bootstrap.

## Local personal account

Use `APP_ENV=development` and the project's own `.venv`. This uses
`.local/development.sqlite3`, separate from the old sample database. Then run:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py bootstrap_owner --email devansh.sharma@geezabreak.org.uk --name "Devansh Sharma"
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8010
```

The private local setup link is saved to `.local/owner-setup-link.txt`. Local development
uses a visible CAPTCHA simulator and local email files; it is not the hosted production
deployment. The personal account starts with no demo staff or timesheets.
