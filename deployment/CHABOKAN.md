# Deploy Sobhe Emrooz to Chabokan

This follows the adjacent Hamyar Sakhteman project's managed Flask setup.
The existing Chabokan service is `sobhe-emrooz`; `chabok.json` selects it for
`chabok deploy`. The repository configuration does not change dashboard settings.

## 1. Configure the Flask host

In **Cloud hosting → sobhe-emrooz → Settings**, use the Flask platform and
Python **3.12** (at least 3.11 is required by this project). Set these environment
variables before the first deployment:

| Variable | Value |
| --- | --- |
| `FLASK_MODULE` | `wsgi:application` |
| `SOBHE_ENV` | `production` |
| `SOBHE_DATA_DIR` | `/data` |
| `SOBHE_SECRET_KEY` | A new random secret generated below |
| `GUNICORN_WORKERS` | `1` |

Generate the session secret locally and copy it into the dashboard:

```sh
.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'
```

Keep the same secret across subsequent deployments. `SOBHE_DATA_DIR=/data`
selects the hosted data folder, equivalent to Hamyar's `HAMYAR_DATA_DIR` setting.
The application creates it automatically and stores the database at
`/data/sobhe.db`, private attachments in `/data/storage/`, and any disk session
secret at `/data/.session-secret`. Leave `SOBHE_DATABASE_URL` unset so the
database follows this directory. Register `/data` as a permanent path in step 3.
Use a single application instance with SQLite.

Chabokan installs `requirements.txt` and runs its managed Gunicorn server.
The configured WSGI entry point enables production cookies; `serve.py` is the
separate Waitress entry point for self-managed hosting. Enable HTTPS on the
service domain before signing in.

## 2. Sign in and deploy from the local project root

```sh
cd /Users/mehrzadghodrati/Developer/Projects/SobheEmrooz
npm install -g @chabokan.net/cli
chabok login
chabok service list
chabok deploy
```

The CLI is already installed on this development machine; installation and
login are only needed when missing or signed out. `chabok deploy` targets
`sobhe-emrooz` through `chabok.json`.

For a Google-linked account, obtain an API token from
[Chabokan's API-token page](https://hub.chabokan.net/fa/api-token) and use this
instead of the interactive login (zsh):

```sh
read -rs 'SOBHE_CHABOK_TOKEN?Chabokan API token: '
printf '\n'
chabok login --token "$SOBHE_CHABOK_TOKEN"
unset SOBHE_CHABOK_TOKEN
```

`.chabokignore` excludes local databases (including SQLite journal/WAL files),
uploads, backups, secrets, virtual environments, Node dependencies, and generated
outputs. Local data and accounts are not included in the deployment.

## 3. Preserve the hosted data before initializing it

After the first CLI upload, open **Cloud hosting → sobhe-emrooz → Permanent
paths (مسیرهای دائمی)**. Chabokan enables this section after that first upload.

Add **`/data`** as a permanent path, matching `SOBHE_DATA_DIR=/data` from step 1.
Keep it registered on every future deployment so the SQLite database, private
attachments, backups, and any disk session secret survive code replacement.
An environment variable alone does not make storage persistent.

## 4. Initialize the fresh database in the host console

From the deployed directory containing `app.py`, run:

```sh
python -m flask --app app migrate
python -m flask --app app create-admin
```

`create-admin` prompts for a username, display name, and private password. Use
this command for the initial production account. Neither account creation nor
database migration runs automatically at web-server startup. Public database
pages will be unavailable until migration completes.

Open the service's HTTPS URL, check `/login`, sign in, and configure the outlets,
organizations, and allocation period. Also check a public report and an
attachment after uploading one. Keep `/manifest.webmanifest`, `/service-worker.js`,
and `/offline` routed to Flask for the PWA.

Check the final build/start result under **Deployments** in the dashboard;
an accepted upload alone does not confirm successful startup. Read runtime logs
from the local terminal with:

```sh
chabok service logs --service sobhe-emrooz
```

## Later deployments

### Import the 1405 workbook in production

Deploy the code containing `import-1405` and upload `آگهی 1405.xlsx` to the
deployed application directory (the current `.chabokignore` includes this file
in deployment uploads). In the **host console**, from the directory containing
`app.py`, use the production environment and an existing administrator username:

```sh
export SOBHE_DATA_DIR=/data
python -m flask --app app migrate
python -m flask --app app import-1405 'آگهی 1405.xlsx' --username admin
```

Check the dry run: 300 ads, 66 new media and 29 new organizations on an empty
instance, 1,572 boxes, and 40,676,000,000 rials. Existing matching catalog
entries reduce the new catalog counts. Then apply:

```sh
python -m flask --app app import-1405 'آگهی 1405.xlsx' --username admin --apply
```

Use the host's actual administrator username if it differs from `admin`.
Leave `SOBHE_DATABASE_URL` unset as described above, or ensure its configured
SQLite URL targets the intended production database. The command automatically
backs up the database and attachments to `/data/backups/before-import-1405-*.zip`
before writing. Download that backup through the host file manager. An identical
repeat skips records; an existing 1405 history or changed source is refused.
Check `/admin/advertisements` and the 1405 dashboard after import. Public reports
must use allocation date because the source has no approval dates. This is a
console command; deploying code alone never imports records.

Keep the existing permanent path and session secret. Before replacing a running
version, create a backup in the host console and download it with the file manager:

```sh
python -m flask --app app backup "/data/backups/sobhe-$(date -u +%Y%m%dT%H%M%SZ).zip"
```

Then, from the local project root:

```sh
chabok deploy
chabok service logs --service sobhe-emrooz
```

Ordinary code deployments preserve the current database and accounts. Run a
migration only when a release requires one; do not repeat account creation or
demo seeding. If needed, restart with `chabok service restart --service sobhe-emrooz`.

References: [Flask deployment](https://docs.chabokan.net/cloud-hosting/flask/deploy/),
[Gunicorn settings](https://docs.chabokan.net/cloud-hosting/flask/more/),
[permanent paths](https://docs.chabokan.net/features/permanent-path/),
and [CLI usage](https://github.com/chabokan/cli).
