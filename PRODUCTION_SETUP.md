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

## Hosting decision required before purchase

Proposed dedicated infrastructure in Frankfurt: Starter web service, Basic-256mb
Postgres with 1 GB storage. Render's published July 2026 example is about US$13/month
for compute, plus US$0.30/GB/month storage, taxes and any usage beyond the included
allowance. Confirm current pricing and the owner's spending approval before purchasing.

Sources: [Render cost model](https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses),
[current pricing](https://render.com/pricing),
[database recovery](https://render.com/docs/postgresql-backups).
Free Render databases expire after 30 days and are unsuitable for permanent payroll
records. The current SQLite review database is disposable; do not import its sample data.

## Required private configuration

Configure these on the separate service before switching `APP_ENV` to `production`:

| Setting | Value or source |
| --- | --- |
| `DATABASE_URL` | Internal URL of the new dedicated Postgres database |
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

The locally available Mailjet pair was rejected with HTTP 401 on 21 September 2026.
A replacement must be verified before sending is enabled. Do not paste keys into Git,
chat, public pages or build commands. Production refuses SQLite, incomplete CAPTCHA,
known CAPTCHA test keys and preview email delivery.

## Cutover and verification

1. Provision the approved separate database; use its internal connection URL. Restrict
   external access to the required operators. Confirm paid backups/PITR are available.
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

If initial activation expires, an authorised server operator can reissue a setup
invitation with the existing `staff_portal.accounts.issue_link(owner, "setup")` function,
using the profile with `roster_key="portal-owner"`. It sends to the registered address
through the configured mail backend. Active accounts use password recovery. Do not
reset a person's password by rerunning bootstrap.

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
