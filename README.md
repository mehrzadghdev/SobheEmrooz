# سامانه مدیریت آگهی‌های دولتی · Sobhe Emrooz

A Persian RTL government advertisement allocation dashboard built with Python, Flask, and SQLite. Its application factory, SQLAlchemy models, Jinja templates, static CSS/JavaScript, CLI tools, and light/dark interface with the #da1214 primary color and supplied logo follow the adjacent **Hamyar Sakhteman** sample. The application has its own database and accounts.

## Run locally

Python 3.11 or later is required. Node.js is optional and only runs the client calendar tests.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m flask --app app migrate
python -m flask --app app create-admin
python -m flask --app app run --host 127.0.0.1 --port 5055
```

Open **http://127.0.0.1:5055/**. Reports are public. The administrator command privately prompts for the password and creates a personal account. Accounts are created explicitly through CLI/admin actions; public registration is disabled. Login at `/login`.

### Page navigation and installation

Internal links and page forms update the main content through `static/js/page-navigation.js`, following the adjacent **Hamyar Sakhteman** example. The header, sidebar, and selected theme stay in place. A loading bar and fade/slide show navigation progress. Animated transitions last at least 0.3 seconds; motion respects the browser's reduced-motion setting. Back/Forward restores scroll positions, and date pickers, allocation previews, dialogs, and target totals initialize for each new page. Flask renders the HTML, so direct URLs and ordinary forms also work without JavaScript.

Sign-in, sign-out, Excel exports, and attachment downloads use ordinary browser navigation. Add `data-native-navigation` to a link, form, or wrapper to opt out. Validation and stale-allocation responses display the updated form for review. Connection failures retain the current page and entered values; submitted writes are never retried automatically.

The app is installable as a Persian RTL PWA with standalone mode, red newspaper icons, and an installation button when supported. On iPhone/iPad, the button explains **Share → Add to Home Screen**. Installation requires HTTPS or a local development origin such as `http://127.0.0.1:5055`.

The service worker caches public CSS, JavaScript, fonts, icons, the manifest, and a generic Persian offline screen. Reports, account pages, CSRF tokens, allocation API responses, and advertisement attachments stay on the network. An offline navigation inside the app retains the current page; an offline launch or reload shows the connection screen. Recording changes requires a connection. Updates install without reloading an open form, and content-based cache versions remove superseded assets.

Keep `/manifest.webmanifest`, `/service-worker.js`, and `/offline` routed to Flask when deploying behind a reverse proxy. The manifest and worker also respect a deployment URL prefix. Restart the application after deploying changed assets so their versions refresh. This feature requires no database migration.

To seed the requested administrator account instead:

```sh
python -m flask --app app seed-admin
```

This initializes the database if necessary, creates or updates username **admin** with password **123asd#**, assigns the administrator role, and activates the account. Re-running it resets that account to these credentials and invalidates its previous sessions. Other users are preserved. The password is stored as a hash and is not printed or included in the audit log.

For a fresh instance, define **رسانه‌ها** and **سازمان‌ها**, then open **دوره‌ها و سهم هدف**. Create the desired Persian year/half, include each outlet in its category, set shares totaling 100% within each of the three categories, and activate the plan. Operators can then enter advertisements. An unconfigured period displays an empty public dashboard and a configuration message on entry.

### Optional demonstration data

After migration and personal administrator creation, on a database with no outlets, periods, or advertisements:

```sh
python -m flask --app app demo-data
```

This explicitly adds six illustrative outlets, three organizations, and eight advertisements totaling 1,000 boxes in the current Persian period. The dashboard marks the instance as a demonstration. The example includes M.A. 1002 with 10 boxes assigned to Sobhe Emrooz. All volumes and shares are ordinary editable records; the allocation engine has no fixed volume budget. Re-running the command or using it on an existing operational instance is refused. Use a separate `SOBHE_DATA_DIR` for demonstrations.

## Allocation policy

- Each Persian year has two independent periods: Farvardin–Shahrivar and Mehr–Esfand. **تاریخ تخصیص** determines the period and defaults to the Tehran calendar day. Approval, publication, and creation dates remain separate.
- The two local categories each target 25% of the total recorded period volume; national media target 50%. Local category names are editable.
- Outlet shares are percentages within their category. Each category totals 100%. Membership and shares belong to a period; creating the next plan copies the nearest preceding plan into a draft. Changes affect only the selected period.
- Expected outlet boxes = all nonvoid period boxes × category target × outlet share. For 1,000 boxes, a 50% outlet share in a local category means **125 boxes**, even if that category actually received 300 boxes.
- Recommendations give local media priority. If the proposed whole advertisement would push local volume above 60%, recommend national media and retain recovery mode until local share reaches 50% or below. At exactly 60% projected, local remains eligible. An empty period starts with a local recommendation and shows the initial over-quota warning.
- Choose the local category with the greatest deficit. Within the selected category choose the lowest allocated-volume-to-share ratio, favoring outlets below their category share. Ties use the oldest latest allocation record and stable outlet ID; empty categories start with the largest share.
- Inactive or zero-share outlets receive no automatic recommendations. An active outlet with zero share can receive an acknowledged manual allocation. Shares are never silently renormalized. Missing eligible outlets produce an explicit configuration warning and available manual choices.
- Each M.A. advertisement stays intact on one outlet. Both administrators and operators can choose another outlet, increase volume, and proceed past current/projected quota warnings after explicit acknowledgement. These allocations immediately affect subsequent calculations.
- Preview requests never change the database. Signed, 30-minute preview tokens bind the relevant allocation state. Saves recompute inside a serialized SQLite write transaction; stale previews require review. Audit events retain the actor, before/after values, recommendation, and accepted warnings.

Box values are stored as integer hundredths, shares as basis points, and comparisons use exact fractions. Display rounding never changes a quota decision. Financial amounts are nonnegative whole **ریال**. M.A. references are positive, globally unique numeric values; Persian/Arabic digits and leading zeros normalize before uniqueness checks.

## Advertisement records and reports

Required fields are M.A., issuing organization, receiving outlet, allocation date, and positive **حجم آگهی** in کادر. Advertisement volume alone drives allocation; approved volume and front-page headline volume are independent optional details.

All supplied E-Rasaneh report fields are supported: advertisement ID, subject and description, M.A., organization, outlet, print status, proposed/final print dates, organization letter number/date, advertisement text, page number/type, box size, front-page headline boxes, advertisement/approved volumes, approval and creation dates, province, file, advertisement/approved amounts, advertisement type, notes, distribution location, and approver name.

Publication and approval values are record details without an additional approval workflow. Creation timestamps are automatic UTC timestamps. Dates are stored as Gregorian dates and presented with the Persian calendar and a keyboard-accessible date picker. The approver is an optional display name, supporting external approvers as well as staff names.

Public reports provide:

- Advertisement details, outlet quotas, and organization summaries.
- Date-field selection (approval by default), a maximum one-Persian-year date range, advertisement-ID and M.A. ranges, organization, letter number, province, type/status multi-selects, subject/description, outlet, advertisement text, and both amount ranges.
- All supplied display columns, pagination, selected-column Excel export, and browser printing. Spreadsheet text is escaped against formula interpretation.
- Clearly separated filtered results and full-period quota comparisons. A custom report can span allocation periods; outlet quotas always use the separately selected complete six-month period, with its own targets.

Public reports default to approval date, so advertisements without that date do not appear until **تاریخ تخصیص** is selected. This behavior stays the same when a staff member signs in. The separate staff list at `/admin/advertisements` defaults to allocation date so newly entered records are immediately visible. Signing in opens this management list. Public dashboards and quotas include all nonvoid allocations regardless of approval or print status.

Navigation groups management routes first: **آگهی‌ها**, **رسانه‌ها**, **سازمان‌ها**, subjects, periods, accounts, and audit history. A separate **گزارش‌های عمومی** group follows, with a distinct icon for each destination. Operators see the advertisement management route; other management routes require an administrator. The active navigation item updates during navigation and browser Back/Forward.

Public pages use `templates/public/` and `layouts/public.html`; management pages use `templates/admin/` and `layouts/admin.html`. Public advertisement and media profiles remain read-only even for signed-in administrators. Management links, forms, profiles, and actions use `/admin/` URLs; the earlier advertisement creation/edit/void URLs remain authenticated compatibility aliases. Voided records are only visible on the administrator detail page. Deletion removes an advertisement from public reports and quotas while retaining its M.A. reference and audit history. The **رسانه‌ها** table offers create, view, edit, and delete/archive actions. Unused media can be deleted; referenced media are archived so historical calculations remain valid.

Advertisement search is a native accordion that starts closed on public and management pages. Inside it, common date, organization, and media filters appear above separate advanced-filter and table-column accordions. Active advanced filters remain open after submission. Summaries identify the selected date basis and range; organization names link to matching advertisement results while preserving filters. The public media table compares received volume and target volume in six columns, with a short expandable explanation. Accordions throughout the app share expanded-state styling, keyboard focus, chevrons, and animations that respect reduced motion.

One optional PDF, PNG, or JPEG file up to 10 MiB can be attached. Files live outside static assets, use generated storage keys, and download with validated content types and attachment headers. The current file of a nonvoid advertisement is publicly downloadable. Replaced files remain in storage for historical audit and backup consistency.

## Accounts and corrections

| Capability | Public | Operator | Administrator |
|---|---|---|---|
| View reports, details, and current files | Yes | Yes | Yes |
| Create advertisements and override warnings | | Yes | Yes |
| Edit or void saved advertisements | | | Yes |
| Manage outlets, organizations, subjects, and period targets | | | Yes |
| Create/deactivate accounts and view audit history | | | Yes |

Organizations and subjects offer delete actions: unused entries are removed, while entries referenced by any advertisement (including voided records) are archived. Empty periods can be deleted together with their targets; periods with advertisement history cannot be deleted. Unused accounts can be deleted, and accounts with advertisements or audit activity are archived with immediate session invalidation. The current account and the final active administrator are protected. These actions require administrator access and CSRF tokens, record audit events, and use accessible in-app confirmation dialogs with action-specific labels, Escape/cancel support, and focus restoration. No database migration is required.

Administrator corrections update the affected period totals and recovery state. Moving a record updates both periods. Voiding preserves the advertisement, reason, history, and reserved M.A. reference while removing it from public reports and quotas. Print cancellation alone does not void an allocation. Outlets already used in a period cannot be removed from that period's plan.

Account changes invalidate that account's previous sessions. The final active administrator cannot be disabled or demoted. Server operators can recover a password privately:

```sh
python -m flask --app app reset-password --username your-username
```

## API

`GET /api/allocation-preview` requires an active administrator/operator session. Parameters:

| Parameter | Meaning |
|---|---|
| `allocation_date` | ISO Gregorian or `YYYY/MM/DD` Persian date |
| `volume` | Positive boxes with at most two decimal places |
| `outlet_id` | Optional manual choice; otherwise use the recommendation |
| `exclude_id` | Administrator-only edit preview, excluding the original record |

The response includes period, recommendation, current and projected selected-outlet statistics, overall local share, warnings, available outlets, and a signed preview token. Failures return a Persian JSON `error`. Entry/edit POSTs use CSRF, this token, and acknowledgement when warnings exist. No external E-Rasaneh synchronization is performed.

## Deployment and persistent data

For the existing Chabokan Flask host **sobhe-emrooz**, follow
[the Chabokan deployment guide](deployment/CHABOKAN.md). It includes dashboard
settings, CLI login/deployment commands, permanent storage, and first-account
setup. `chabok.json` selects this service; `.chabokignore` keeps local data and
secrets out of uploads. Set `SOBHE_DATA_DIR=/data` in the host environment and
register `/data` as a permanent path so the database, attachments, and backups
are kept together across deployments.

For self-managed hosting:

```sh
export SOBHE_DATA_DIR=/absolute/path/to/persistent-data
export SOBHE_ENV=production
# Set SOBHE_SECRET_KEY through your hosting secret manager.
python -m flask --app app migrate
python -m flask --app app create-admin
python serve.py
```

Waitress binds to `127.0.0.1:8000` by default. Configure `SOBHE_HOST`/`SOBHE_PORT` as needed and place it behind an HTTPS reverse proxy. `wsgi:application` is also available. Production session cookies require HTTPS; development startup remains local HTTP. Authentication uses Werkzeug password hashing, HttpOnly/SameSite session cookies, CSRF protection for writes, authorization on every request, and session invalidation for inactive accounts. HTML, exports, and private responses are not cached.

`SOBHE_DATA_DIR` defaults to `instance/` and contains `sobhe.db`, storage, and a persisted private session secret when `SOBHE_SECRET_KEY` is absent. Persist this directory across restarts. `SOBHE_DATABASE_URL` is available for a SQLite URL override; write locking and migration tooling are deliberately SQLite-specific. Schema creation/migration and account seeding never happen on HTTP startup. The initial migration is idempotent and refuses unsupported/unversioned existing databases.

### Backup and restore

```sh
python -m flask --app app backup /absolute/path/to/backups/sobhe-2026-11-01.zip
```

The command uses SQLite's online backup API and verifies database integrity, then includes the corresponding immutable advertisement files. The destination must be new and is created with owner-only permissions.

To restore, stop all server processes, preserve the current instance as a rollback copy, extract `sobhe.db` and `storage/` from the ZIP into the persistent data directory, and preserve the existing `.session-secret` or configured secret. Restart and verify login, one public report, and an attachment. `PRAGMA integrity_check` should return `ok` and `PRAGMA foreign_key_check` should return no rows. No production deployment is performed by this project setup.

## Verification

```sh
python scripts/run_tests.py
```

The suite uses temporary databases and files. It covers allocation arithmetic, projected tolerance/recovery, fractional volumes, period boundaries and target isolation, overrides, concurrent M.A. submissions, stale previews/edits, authorization/CSRF/session invalidation, field round trips, report filters and exports, attachments, corrections/voiding, bootstrap, and backup readability. Node.js, if available, also checks Persian client calendar conversion. Run the application to inspect desktop/mobile layouts, light/dark mode, the date picker, and the live allocation preview.

PWA checks also verify the manifest and icon dimensions, cache allowlist, deployment prefixes, session-free offline screen, and availability before database initialization. For optional real-browser regression tests:

```sh
npm ci
npx playwright install chromium
python scripts/run_browser_tests.py
```

The browser suite starts its own loopback server with a disposable database and tests navigation, loading feedback, form validation, stale previews, uploads/downloads, history, mobile/reduced motion, install prompts, offline recovery, and browsing without JavaScript. To use an already installed Chrome instead of downloading Chromium, set `SOBHE_BROWSER_CHANNEL=chrome` for the test command. Node.js and Playwright are only development dependencies.
