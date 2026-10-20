# Only Pawns

A computerized pawnshop system for Palawan Pawn Star. Customers use a phone app (a PWA). Employees use a dashboard in the browser. Both sit on the same Django project and the same database.

**Group:** Jhon Grover Longsud, Allan Dexter P. Cuizon, Leeh Vann Joshua Lomocso

## Run it

You need Python 3.10 or newer.

```
bash setup.sh          # Windows: setup.bat
python manage.py runserver
```

The setup script makes a virtual environment, installs the packages, creates the database tables and loads demo data.
If you'd rather do it by hand: `pip install -r requirements.txt`, then `python manage.py makemigrations pawn`, `migrate`, `seed`.

| Where | Address |
|---|---|
| Customer app | http://127.0.0.1:8000/app/ |
| Staff dashboard | http://127.0.0.1:8000/staff/login/ |
| REST API | http://127.0.0.1:8000/api/ |
| Django admin (backup tool only) | http://127.0.0.1:8000/django-admin/ |

### Demo accounts

Staff (password for all three: `OnlyPawns#2026`): `admin`, `cashier`, `appraiser`.
Clients: `maria.santos@example.com`, `juan.delacruz@example.com`, `ana.reyes@example.com`, PIN `123456`.

Change these before the system holds anything real.

## The demo story

The seed gives you three tickets so every screen has something to show.

1. **Maria's gold necklace** is a fresh ticket (loan 10,000 at 3%). Walk it through renewal, then redemption, as the cashier. Open the same ticket on Maria's phone and watch the amounts change.
2. **Ana's phone** matures in three days. It shows up as a warning on the cashier dashboard and in Ana's app.
3. **Juan's watch** is five months old. The nightly job already moved it to Expired, so the admin can schedule an auction and record the result.

A full cycle for the presentation: register a walk-in, verify the ID, appraise an item, issue the ticket, renew, redeem, then show the cash ledger and export a report.

## What is in it

| Spec item | Where |
|---|---|
| Customer registration, unique Customer ID, view/update/deactivate | `/staff/customers/`, `/app/register/` |
| Pawn transactions, ticket number, ID and item photos | Appraisals, then "Issue ticket" |
| Interest and due date computation, alerts | `compute_due()` in `pawn/services.py`, `python manage.py nightly` |
| Redemption and renewal with receipts | Ticket page, receipts are PDFs with a QR code |
| Auction management | `/staff/auctions/` (administrator) |
| Cash flow and reports, PDF and Excel | `/staff/reports/`, `/staff/ledger/` |
| Roles | Administrator, Cashier, Appraiser, Client |
| Security and backup | Argon2 hashing, encrypted ID photos and ID numbers, audit log, `python manage.py backup` |
| Extras from the build guide | Padala, Load, Bot Star, Pawn Patrol, Wrap Star, branch map and queue counter, currency display, transaction tracker, QR lookup |

CRUD lives in the staff screens (customers, employees, branches, rates, currencies, accepted IDs) and in the API (`/api/customers/`, `/api/branches/`, `/api/categories/`).
Read endpoints: `/api/tickets/`, `/api/tickets/<id>/due/?date=YYYY-MM-DD`, `/api/transactions/`. Chat: `POST /api/bot/`.

## How the money works

- Interest = loan x monthly rate x months. Months are counted from the loan date and a partial month counts as a full one. Ten thousand at 3%, redeemed in month 2, owes 600 interest.
- Redeem = principal + interest still owed + service fee + penalty.
- Renew = interest only (at least one month's worth). The maturity date moves a month ahead, the principal stays. Interest already paid in earlier renewals is not charged again when you redeem.
- The rate on a ticket is copied from the category on the loan date, so a later rate change never touches old tickets.
- Grace period defaults to 90 days after maturity. After that the nightly job sets the ticket to Expired and the item to For Auction.
- Amounts are stored in pesos with the currency and rate used that day. The client app can display USD or EUR, which is display only.

## Settings that are your call

These are the open questions from the build guide. The choices made here are easy to change.

- **Loan vs Sangla:** Sangla is for any item. Loan works the same way but is blocked for jewelry.
- **Penalty:** off by default. Set `PAWN_PENALTY_RATE=0.02` in `.env` to charge 2% of the principal once a ticket is past maturity.
- **Payments:** counter only. Nothing in the app takes money.
- **Padala and Load:** simulated, and the receipts say so. Say that in the presentation.
- **SMS:** `SMS_BACKEND=mock` writes to the SMS log only. Set `SMS_BACKEND=semaphore` plus `SEMAPHORE_API_KEY` to send real texts.
- **Google Sign-In:** put your OAuth client ID in `GOOGLE_CLIENT_ID` (Google Cloud Console, OAuth client of type Web, add your site as an authorized JavaScript origin). With no ID, the app asks for the Google email directly so you can still demo. Never leave it in that mode on a public server.
- **Interest rates, grace days, fees:** edit them under Admin, Rates and categories. The interest and grace numbers in the guide still need real references (BSP rules, actual shop practice) for the written defense.

## Lists: search, sort, paging

Every staff list has a search box (`q`, matches any of several fields), a "Sort by" dropdown that remembers your choice, and paging (20 per page). Sorting only accepts the fields offered in the dropdown. The admin screens (branches, categories, currencies, exchange rates, accepted IDs) are class-based views (`ListView`, `CreateView`, `UpdateView`, `DeleteView`) in `pawn/views_staff.py`; the other screens are function views that share the same `search_qs`, `sort_qs` and `paginate` helpers and the `includes/search_sort.html` and `includes/pagination.html` templates. The staff dashboard shows real totals, including this year's registrations and tickets.

## Deploying

See `DEPLOY.md` (PythonAnywhere).

## Scheduled jobs

Run both every night. Linux cron:

```
5 0 * * *  cd /path/to/onlypawns && .venv/bin/python manage.py nightly
30 0 * * * cd /path/to/onlypawns && .venv/bin/python manage.py backup
```

On Windows use Task Scheduler with the same commands. The admin can also run a backup from Admin, Backups. Backups go to `backups/` as zip files (database, photos, encrypted IDs) and the last 14 are kept.

## The phone app (PWA)

Open `/app/` on a phone and use "Install" or "Add to Home Screen". Browsers only allow installing from `localhost` or HTTPS, so for a demo on a real phone either deploy with HTTPS or use a tunnel such as ngrok or Cloudflare Tunnel. The service worker keeps the look of the app available offline. Money screens always need a connection so that nothing shown is out of date.

## Tests

```
python manage.py test
```

They cover the interest maths, renewal then redemption, short payments, the auction path and role access.

## Before real use

- Set a new `SECRET_KEY` and `DEBUG=0` in `.env`. The key also protects the encrypted ID data, so changing it later makes old ID photos unreadable. Pick it once and keep it safe.
- Serve over HTTPS and run behind a proper web server (gunicorn or similar), and move to PostgreSQL if more than one branch will use it at once.
- Receipt PDFs are only handed out through a permission check, but in development mode Django also serves the `media/` folder openly. Turn DEBUG off or block that path in production.

## Folder tour

```
config/            settings and root urls
pawn/models.py     every table from the ERD
pawn/services.py   interest engine, transactions, receipts, SMS, reports, Wrap Star, Bot Star
pawn/views_staff.py, views_client.py, api.py
pawn/templates/    staff/ and app/
pawn/static/pawn/  css, js, service worker, manifest, icons
pawn/management/commands/   seed, nightly, backup
```
