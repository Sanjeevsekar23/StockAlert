# StockAlert

Flask inventory management application. `app.py` exports the WSGI application
as `app`, so it can be served by Gunicorn in production.

## Local development

From PowerShell:

```powershell
Set-Location 'C:\Users\Admin\Downloads\StockAlert'
. .\.venv\Scripts\Activate.ps1
$env:STOCKALERT_SECRET_KEY = (python -c "import secrets; print(secrets.token_urlsafe(32))")
python app.py
```

The local database defaults to `stockalert.db`. Set `STOCKALERT_DATABASE` to a
different path when you want to use another local database. Do not commit local
database files or `.env` files.

## Prepare a Render web service

Connect the repository containing this project as a **Web Service**. Configure:

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1`

Set these environment variables in the Render service settings:

- `STOCKALERT_ENV` = `production`
- `STOCKALERT_SECRET_KEY` = generate a unique secret and store it in Render's
  environment settings. Never commit it to the repository.
- `STOCKALERT_DATABASE` = `/var/data/stockalert.db` when using the SQLite disk
  option below.

The app refuses to start in production if the secret or database path is not
configured. Production sessions use secure, HTTP-only, same-site cookies. On
Windows, running `python app.py` binds the local Flask server to
`127.0.0.1:80` by default. Add `127.0.0.1 stockalert.local` to the Windows
hosts file to use `http://stockalert.local`. Override local binding with
`STOCKALERT_HOST` and `STOCKALERT_PORT` if needed. Render should use the
Gunicorn start command above, not Flask's development server.

## Database choice before storing real inventory

The application currently uses Python's built-in `sqlite3`; it does not accept a
PostgreSQL connection URL. Render's default service filesystem is ephemeral, so
a SQLite database stored in the repository/service filesystem can be lost on a
deploy. Do not add `stockalert.db` to GitHub or expect it to appear on Render.

For a small single-instance deployment, attach a Render Persistent Disk to the
web service with mount path `/var/data`, then set
`STOCKALERT_DATABASE=/var/data/stockalert.db`. SQLite on a persistent disk is
limited to that service instance; keep one instance and take independent
backups. Confirm the disk's availability and price in Render before selecting
this setup. The first start on a new disk creates an empty database. It does not
transfer local inventory data.

For production inventory shared across instances, use Render Postgres. A
Postgres URL cannot be substituted into `STOCKALERT_DATABASE`: the current code
uses SQLite-specific connections, SQL, and migrations. Before using Postgres,
implement and test a PostgreSQL database adapter and an explicit data migration
for products, users, attendance, stock movements, and expiry batches. Keep the
existing SQLite file as a separate backup; do not put it in Git or use it as a
deployment artifact.

The existing SQLite initialization uses `CREATE TABLE IF NOT EXISTS` and
additive `ALTER TABLE` checks. These preserve existing rows when the app starts
against an existing SQLite database, but they are not a PostgreSQL migration
system.

## Private GitHub repository

Connect GitHub to Render from Render's account/deploy settings and grant Render
access to the private repository. Then create a Web Service, choose that
repository and branch, and enter the build/start commands above. Configure the
environment variables and database storage before using real data. Check that
`stockalert.db` is not already tracked in Git; `.gitignore` does not untrack a
file that was committed earlier.

## Custom domain

First register a domain with a registrar after checking that the name is
available. In the deployed Render Web Service, open **Settings → Custom
Domains** and add the domain. Render then displays the DNS records for that
service and domain. Copy those exact record names and values to the registrar's
DNS settings, then return to Render and verify the domain. DNS values are
service-specific; do not use example values from unrelated services. Render
provisions TLS after verification. See Render's current
[custom domain instructions](https://render.com/docs/custom-domains).

## Tests

Install development dependencies with `pip install -r requirements-dev.txt`,
then run `pytest`. The project's tests select a temporary SQLite database via
`STOCKALERT_DATABASE` and do not need the real `stockalert.db`.
