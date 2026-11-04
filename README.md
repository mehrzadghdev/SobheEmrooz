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

Tables provide search, click-to-toggle ascending/descending column sorting, and
first/previous/next/last pagination controls with 15 rows per page by default.
Page sizes are 15, 30, 50, or 100. Advertisement and audit searches/orderings run
in SQLite before pagination, so they cover all matching records; report filters
remain available and advertisement exports follow the search and sort. Smaller
tables use client controls, including organization/subject catalogs and media
tables. Search or sorting returns to the first page. Action columns retain their
buttons. Recent-allocation previews show the latest 15 records. Print views of
client tables include all matching rows. Sidebar links use the page progress bar
without individual loading spinners. Public dashboard group percentages use
warning color above their 50% target and green at or below it.

Internal links and page forms update the main content through `static/js/page-navigation.js`, following the adjacent **Hamyar Sakhteman** example. The header, sidebar, and selected theme stay in place. A loading bar and fade/slide show navigation progress. Animated transitions last at least 0.3 seconds; motion respects the browser's reduced-motion setting. Back/Forward restores scroll positions, and date pickers, allocation previews, dialogs, and media type fields initialize for each new page. Flask renders the HTML, so direct URLs and ordinary forms also work without JavaScript.

Sign-in and sign-out use ordinary browser navigation. With JavaScript enabled, Excel exports and attachments download on the current page with a loading indicator until the file is ready; without JavaScript, their ordinary download links work directly. Add `data-native-navigation` to a link, form, or wrapper to opt out. Validation and stale-allocation responses display the updated form for review. Connection failures retain the current page and entered values; submitted writes are never retried automatically.

Request controls share a loading mode with a spinner and accessible busy/disabled state. It covers navigation, native and client form submissions, allocation previews, downloads, and installation, while preserving labels, layout, submitter names/values, and confirmation behavior. Controls recover on success, cancellation, and request errors. Sidebar navigation groups use compact spacing.

The app is installable as a Persian RTL PWA with standalone mode, red newspaper icons, and an installation button when supported. On iPhone/iPad, the button explains **Share → Add to Home Screen**. Installation requires HTTPS or a local development origin such as `http://127.0.0.1:5055`.

The service worker caches public CSS, JavaScript, fonts, icons, the manifest, and a generic Persian offline screen. Reports, account pages, CSRF tokens, allocation API responses, and advertisement attachments stay on the network. An offline navigation inside the app retains the current page; an offline launch or reload shows the connection screen. Recording changes requires a connection. Updates install without reloading an open form, and content-based cache versions remove superseded assets.

Keep `/manifest.webmanifest`, `/service-worker.js`, and `/offline` routed to Flask when deploying behind a reverse proxy. The manifest and worker also respect a deployment URL prefix. Restart the application after deploying changed assets so their versions refresh. This feature requires no database migration.

To seed the requested administrator account instead:

```sh
python -m flask --app app seed-admin
```

This initializes the database if necessary, creates or updates username **admin** with password **123asd#**, assigns the administrator role, and activates the account. Re-running it resets that account to these credentials and invalidates its previous sessions. Other users are preserved. The password is stored as a hash and is not printed or included in the audit log.

For a fresh instance, define **رسانه‌ها** and **سازمان‌ها**, then open **دوره‌ها و سهم هدف**. Select National or Local when creating each media. Create the desired Persian year and activate it. Active, classified media are enrolled automatically; no per-media percentages or membership settings are required. Operators can then enter advertisements. An unconfigured period displays an empty public dashboard and a configuration message on entry.

### Upgrade existing instances

Back up the instance with the existing `backup` command, then run `python -m flask --app app migrate` before restarting the app. Schema version 5 preserves existing records, including legacy advertisements with unspecified volume. Current entry/edit validation requires positive volume. Upgrades from earlier versions also apply schema version 4, which uses one period per Persian year, from Farvardin 1 through the last day of Esfand. Existing halves of the same year are combined, advertisements retain their dates and IDs, and duplicate media memberships are merged. A year stays active if either former half was active. Recovery mode is recalculated against combined annual volume; the 50% local / 50% national targets apply across the whole year. Old period IDs and memberships are recorded in a migration audit event. Preview revisions change so stale saves require review.

Upgrades from schema versions 1, 2, 3, or 4 are transactional and repeatable. Version 1 also gains saved media types and missing memberships; versions 1 and 2 merge the former local subgroups into `local`. Media without a recorded category remain **نیازمند تعیین نوع** until classified. Legacy shares remain unused. If the same media has advertisement history in both local and national categories within the same year (including voided advertisements), migration refuses the merge without changes so those historical classifications can be resolved explicitly. Missing historical memberships are also refused without changes.

### Optional demonstration data

After migration and personal administrator creation, on a database with no outlets, periods, or advertisements:

```sh
python -m flask --app app demo-data
```

This explicitly adds six illustrative outlets, three organizations, and eight advertisements totaling 1,000 boxes in the current Persian period. The dashboard marks the instance as a demonstration. The example includes advertisement ID 1002 with 10 boxes assigned to Sobhe Emrooz. Volumes and media types are ordinary editable records; group targets stay fixed at 50% local / 50% national; the allocation engine has no fixed volume budget. Re-running the command or using it on an existing operational instance is refused. Use a separate `SOBHE_DATA_DIR` for demonstrations.

## Allocation policy

### Import the 1405 workbook

Run against the intended instance (local `instance/` by default):

```sh
python -m flask --app app import-1405 'آگهی 1405.xlsx' --username admin
python -m flask --app app import-1405 'آگهی 1405.xlsx' --username admin --apply
```

The first command validates and rolls back a dry run. `--apply` first creates a
verified backup in the instance's `backups/` directory, then imports in one
transaction. The approved mapping imports only Sheet2: 300 advertisements,
66 media, 29 organizations, 1,572 boxes and 40,676,000,000 rials. Sheet1 and the
summary tables are excluded. خراسان رضوی and صبح امروز خراسان رضوی are local;
the other imported media are national. Names are matched exactly after trimming.
The existing صبح امروز entry remains separate from صبح امروز خراسان رضوی.

Advertisement IDs are generated starting at 9000000000000 (or above the largest
existing reference). Proposed dates become both proposed and allocation dates;
approval/actual print dates remain empty and print status uses the app default
منتظر چاپ. Source row and these assumptions are retained in each ad's notes.
Repeated identical imports are skipped, even if the file is renamed. A changed
Sheet2, existing 1405 advertisement history, or conflicting media classifications
are refused for explicit reconciliation. Existing records are never overwritten.

For production console instructions, see [the deployment guide](deployment/CHABOKAN.md#import-the-1405-workbook-in-production).

### Import `12 آبان.xlsx`

The revised export uses Sheet1 with a leading **شناسه آگهی** column. The command
finds the bundled workbook beside `app.py`, regardless of the console directory,
and resolves composed/decomposed Unicode filenames. No workbook path is needed:

```sh
python -m flask --app app import-12-aban --username admin
python -m flask --app app import-12-aban --username admin --apply
```

Replace `admin` with an existing active administrator username. The first command
is a dry run; the second backs up and applies. Expected source totals: **300 rows,
158 distinct source IDs, 66 media, 29 organizations, 1,578 boxes, and
40,586,000,000 rials**. Each publication row gets a unique generated app ID;
the original source ID is preserved in notes, including repeated source IDs.
The previous media mapping and date/status assumptions also apply here.

`import-12-aban` imports incrementally by default, including when the previous
workbook has already been imported. Rows match by organization, media, proposed
date, box volume, and amount; the old workbook has no source IDs. Repeated
publication rows are compared by count so separate occurrences are preserved.
Against an unchanged import of `آگهی 1405.xlsx`, expect **294 skipped rows and
6 new rows**, totaling **36 boxes and 612,000,000 rials** added. The six older
rows absent from the new export remain saved; no existing records are updated
or deleted. Applying again adds zero rows, including after reordering or
renaming the workbook. Source fingerprints are recorded in the import audit
for future comparisons after edits or voids. Dry runs roll back all changes.

`import-1405` accepts this format too; use `--incremental` to enable the same
comparison there. Without that flag it retains the original initial-import
safeguards. No records are imported by deployment alone.


- Each Persian calendar year is one independent period: Farvardin through Esfand, including leap days. **تاریخ تخصیص** determines the period and defaults to the Tehran calendar day. Approval, publication, and creation dates remain separate.
- All local media share one category targeting 50% of total recorded period volume; national media target the other 50%.
- Media have a saved National or Local type. New periods enroll active, classified media as a draft. Newly created or reactivated media join existing periods immediately. Type changes preserve categories in periods with any advertisement history, including voided records; other periods use the new type.
- Expected group boxes = all nonvoid period boxes × group target. For 1,000 boxes, all local media together target **500 boxes** and national media target **500 boxes**. Individual media have no target percentages or quotas.
- Recommendations prioritize advertisement size: fewer than 5 boxes always recommends national media; 5 boxes or more recommends local media, regardless of current or projected local share or recovery mode. Percentage targets and excess warnings remain informational. This rule also applies to empty periods.
- Choose the active media with the lowest received volume in the period across the entire eligible local or national group. Ties use the oldest latest allocation record and stable outlet ID.
- Inactive or unclassified media receive no automatic recommendations. Missing active media in a group produce a configuration warning and available manual choices; that group’s target is never redistributed. Legacy individual share values have no effect on eligibility or allocation.
- Each advertisement stays intact on one outlet. Both administrators and operators can choose another outlet, increase volume, and proceed past current/projected group-target warnings after explicit acknowledgement. These allocations immediately affect subsequent calculations.
- Preview requests never change the database. Signed, 30-minute preview tokens bind the relevant allocation state. Saves recompute inside a serialized SQLite write transaction; stale previews require review. Audit events retain the actor, before/after values, recommendation, and accepted warnings.

Box values are stored as integer hundredths, and group-target comparisons use exact fractions. Display rounding never changes a quota decision. Financial amounts are nonnegative whole **ریال**. Advertisement IDs are positive, globally unique numeric values; Persian/Arabic digits and leading zeros normalize before uniqueness checks.

## Advertisement records and reports

**شناسه آگهی**, issuing organization, receiving outlet, and positive **حجم کادر** are required. All other fields are optional, and detail accordions start closed. Recommendations and group-share information appear in a separate visible card outside the accordions. A blank allocation date uses today for a new ad and preserves the saved date when editing. Volume uses the signed preview and warning acknowledgement workflow. Existing legacy records with unspecified volume remain readable; editing them requires entering a positive volume. Approved volume and front-page headline volume remain independent optional details.

All supplied E-Rasaneh report fields are supported: advertisement ID, subject and description, organization, outlet, print status, proposed/final print dates, organization letter number/date, advertisement text, page number/type, box size, front-page headline boxes, advertisement/approved volumes, approval and creation dates, province, file, advertisement/approved amounts, advertisement type, notes, distribution location, and approver name.

Publication and approval values are record details without an additional approval workflow. Creation timestamps are automatic UTC timestamps. Dates are stored as Gregorian dates and presented with the Persian calendar and a keyboard-accessible date picker. The approver is an optional display name, supporting external approvers as well as staff names.

Public reports provide:

- Advertisement details, group targets, media actuals, and organization summaries.
- Date-field selection (allocation by default), a maximum one-Persian-year date range, advertisement-ID and internal-ID ranges, organization, letter number, province, type/status multi-selects, subject/description, outlet, advertisement text, and both amount ranges.
- All supplied display columns, pagination, selected-column Excel export, and browser printing. Spreadsheet text is escaped against formula interpretation.
- Clearly separated filtered results and full-period quota comparisons. A custom report can span allocation periods; group targets always use the separately selected complete Persian year. Media exports include actuals in the main sheet and group targets in a separate **گروه‌ها** sheet.

Public reports default to **تاریخ تخصیص**, so advertisements appear even when their optional approval or print dates are blank. Selecting another date field limits results to records with that date in the chosen range. This behavior stays the same when a staff member signs in. The separate staff list at `/admin/advertisements` shows all nonvoid advertisements across years, with create, export, print, pagination, and record actions. It has no search form, period selector, or summary cards. Signing in opens this management list. Public dashboards and quotas include all nonvoid allocations regardless of approval or print status.

Public dashboards and report views include a grouped bar chart and a donut pie showing local and national shares. Each chart has its own yearly/monthly/weekly controls inside its card, with a Persian month selector and a week date picker. Both charts default independently to the full selected Persian year; changing one preserves the other chart’s period and rendered content. The yearly bar chart shows months; monthly and weekly views show days. Weeks run Saturday through Friday and are limited to the selected year. The pie’s selected range also applies to its media breakdown; table filters retain their own scope. Clicking a pie slice or its legend opens each media outlet’s percentage within that group. Only the media list scrolls in the dialog, keeping the pie and heading visible on desktop and mobile. Charts use allocation dates and recorded media categories, include inactive outlets with history, and exclude voided advertisements. Keyboard controls, light/dark themes, empty ranges, exact-value popovers, an accessible data table, and reduced-motion preferences are supported. Chart cards and range toggles keep neutral hover backgrounds and readable text in both themes.

Filters, searches, sorting, pagination, and chart ranges update their affected content in place without reloading the document or animating the whole page. Existing controls, open filter disclosures, search focus, and scroll position are preserved; Back/Forward restores the results and filter values. Advertisement and audit tables retain database search and pagination across all matching records. Report filters preserve the current table search, sort, and chart selection. Native links and forms still work without JavaScript.

Group percentages show a warning both below and above the 50% target, with explicit status labels. Only an exact target match is green; periods without allocations are neutral. Default advertisement tables include the advertisement ID, subject, and print status, including the dashboard and media recent-ad lists. The application has no footer.

Navigation groups management routes first: **آگهی‌ها**, **رسانه‌ها**, **سازمان‌ها**, subjects, periods, accounts, and audit history. A separate **گزارش‌های عمومی** group follows, with a distinct icon for each destination. Operators see the advertisement management route; other management routes require an administrator. The active navigation item updates during navigation and browser Back/Forward.

Public pages use `templates/public/` and `layouts/public.html`; management pages use `templates/admin/` and `layouts/admin.html`. Public advertisement and media profiles remain read-only even for signed-in administrators. Management links, forms, profiles, and actions use `/admin/` URLs; the earlier advertisement creation/edit/void URLs remain authenticated compatibility aliases. Voided records are only visible on the administrator detail page. Deletion removes an advertisement from public reports and quotas while retaining its advertisement ID and audit history. The **رسانه‌ها** table offers create, view, edit, and delete/archive actions. Unused media can be deleted; referenced media are archived so historical calculations remain valid.

Public advertisement search is a native accordion that starts closed. Inside it, common date, organization, and media filters appear above separate advanced-filter and table-column accordions. Active advanced filters remain open after submission. Summaries identify the selected date basis and range; organization names link to matching advertisement results while preserving filters. The public media table shows received volume and actual percentages in five columns. Separate summaries compare each group’s actual volume with its fixed target, with a short expandable explanation. Accordions throughout the app share expanded-state styling, keyboard focus, chevrons, and animations that respect reduced motion.

One optional PDF, PNG, or JPEG file up to 10 MiB can be attached. Files live outside static assets, use generated storage keys, and download with validated content types and attachment headers. The current file of a nonvoid advertisement is publicly downloadable. Replaced files remain in storage for historical audit and backup consistency.

## Accounts and corrections

| Capability | Public | Operator | Administrator |
|---|---|---|---|
| View reports, details, and current files | Yes | Yes | Yes |
| Create advertisements and override warnings | | Yes | Yes |
| Edit or void saved advertisements | | | Yes |
| Manage media types, organizations, subjects, and periods | | | Yes |
| Create/deactivate accounts and view audit history | | | Yes |

Organizations and subjects offer delete actions: unused entries are removed, while entries referenced by any advertisement (including voided records) are archived. Empty periods can be deleted together with their media memberships; periods with advertisement history cannot be deleted. Unused accounts can be deleted, and accounts with advertisements or audit activity are archived with immediate session invalidation. The current account and the final active administrator are protected. These actions require administrator access and CSRF tokens, record audit events, and use accessible in-app confirmation dialogs with action-specific labels, Escape/cancel support, and focus restoration. No database migration is required.

Administrator corrections update the affected period totals and recovery state. Moving a record updates both periods. Voiding preserves the advertisement, reason, history, and reserved advertisement ID while removing it from public reports and quotas. Print cancellation alone does not void an allocation. Outlets already used in a period cannot be removed from that period's plan.

Account changes invalidate that account's previous sessions. The final active administrator cannot be disabled or demoted. Server operators can recover a password privately:

```sh
python -m flask --app app reset-password --username your-username
```

## API

`GET /api/allocation-preview` requires an active administrator/operator session. Parameters:

| Parameter | Meaning |
|---|---|
| `allocation_date` | Optional ISO Gregorian or `YYYY/MM/DD` Persian date; blank uses today or the saved edit date |
| `volume` | Optional positive boxes with at most two decimal places; blank previews add no volume |
| `outlet_id` | Optional manual choice; otherwise use the recommendation |
| `exclude_id` | Administrator-only edit preview, excluding the original record |

The response includes period, recommendation, selected media’s actual and projected volume, overall local share, warnings, available media, and a signed preview token. `groups` maps the two category keys (`local` and `national`) to their fixed `target_percent`, current/projected volume, target volume, actual percentage, and excess status. Individual media no longer expose `share_bp`, `target_share`, `target_percent`, `expected`, or individual quota/excess fields. Failures return a Persian JSON `error`. Entry/edit POSTs require positive volume, CSRF, the preview token, and acknowledgement when warnings exist. No external E-Rasaneh synchronization is performed.

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

The suite uses temporary databases and files. It covers allocation arithmetic, size-based recommendations independent of percentage/recovery, fractional volumes, period boundaries, automatic membership, historical categories, group targets, schema upgrades, and 1,000-media scalability, overrides, concurrent advertisement-ID submissions, stale previews/edits, authorization/CSRF/session invalidation, field round trips, report filters and exports, attachments, corrections/voiding, bootstrap, and backup readability. Node.js, if available, also checks Persian client calendar conversion. Run the application to inspect desktop/mobile layouts, light/dark mode, the date picker, and the live allocation preview.

PWA checks also verify the manifest and icon dimensions, cache allowlist, deployment prefixes, session-free offline screen, and availability before database initialization. For optional real-browser regression tests:

```sh
npm ci
npx playwright install chromium
python scripts/run_browser_tests.py
```

The browser suite starts its own loopback server with a disposable database and tests navigation, loading feedback, form validation, stale previews, uploads/downloads, history, mobile/reduced motion, install prompts, offline recovery, and browsing without JavaScript. To use an already installed Chrome instead of downloading Chromium, set `SOBHE_BROWSER_CHANNEL=chrome` for the test command. Node.js and Playwright are only development dependencies.

## Government advertisement directive (schema 6)

The **دستورالعمل و سفارش‌ها** workspace adds verified media profiles, provincial
priority recommendations, the documented 1403 tariff and effective-dated later
rates, order placements, publication evidence, certificates, payments, sanctions
and follow-up obligations. See [setup and article coverage](docs/DIRECTIVE.md).
Run the existing backup command and `python -m flask --app app migrate` before
using the workspace. The new policy remains inactive until the administrator
confirms office settings, the calendar and tariff and classifies current-year
history. No production data is changed by deployment alone.
