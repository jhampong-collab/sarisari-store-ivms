# Sari-Sari Store Inventory Management System: phone app (PWA)

A web app that runs on a server and opens on any phone. Staff add it to the home screen and it
looks and works like an app. It has Products, Cash sales, Customer credit (utang), Reports, and
user accounts with **Owner / Admin / Staff** roles. Data is kept on the server, so every phone
sees the same stock and balances.

## 1. Try it on your computer (2 minutes)

```
pip install -r requirements.txt
python3 app.py
```
Open http://127.0.0.1:5000 . First login: **admin / admin123**. The app makes you choose a new
password immediately. Create your other accounts under **More > Manage users**.

## 2. Put it on phones (needs HTTPS)

Phones only allow "Add to Home Screen" installs, offline mode, and automatic updates on an
**HTTPS** address (plain `http://` works only on the same computer). Pick one:

| Option | Good for | Notes |
|---|---|---|
| PythonAnywhere / Render / Railway / Fly.io | Easiest. A free or cheap plan gives you an `https://` address. | Set the environment variables below. Use a persistent disk or volume for the database file. |
| A small VPS + Caddy | Full control | Caddy gets a free HTTPS certificate automatically. |
| Your own PC + a tunnel (Cloudflare Tunnel, Tailscale Funnel) | Quick demo | The PC must stay on. |

Run it with a real server instead of `python3 app.py`:
```
pip install gunicorn            # Windows: pip install waitress, then: waitress-serve --port 8000 app:app
gunicorn --workers 2 --threads 4 --bind 0.0.0.0:8000 app:app
```
Environment variables:

| Variable | Meaning |
|---|---|
| `SARI_SECURE_COOKIES=1` | **Set this when using HTTPS.** |
| `SARI_DB` | Where to keep the database file (default: `sari_sari.db` next to `app.py`). |
| `SARI_SECRET_KEY` | Optional. Otherwise a random key is created in `secret.key`. Keep it private. |
| `SARI_TZ` | Store time zone, default `Asia/Manila` (used for "sales today"). |

**Install on a phone:** open the https address. Android Chrome: menu, then **Install app**.
iPhone Safari: Share button, then **Add to Home Screen**.

## 3. Shipping updates (no reinstalling)

1. Change the code or screens.
2. In `app.py`, raise `APP_VERSION` (for example `"1.0.0"` to `"1.1.0"`).
3. Restart the server.

The next time anyone opens the app, or taps **More > Check for updates**, they see
*"A new version is available"* and tap **Update**. The app reloads itself. No reinstall, no app
store. Their data is on the server, so nothing is lost.

**Changing the database in an update:** never edit an old entry in `MIGRATIONS` in `app.py`. Add
a new entry at the end, for example
`"ALTER TABLE products ADD COLUMN supplier TEXT NOT NULL DEFAULT '';"`. The server upgrades the
existing database automatically on startup and keeps all data (this is covered by a test).

## 4. Backups (do this)

Everything is in one file, `sari_sari.db`. Copy it somewhere safe regularly, for example daily.
While the server is running, the safe way is:
```
sqlite3 sari_sari.db ".backup 'backup-2026-10-16.db'"
```

## 5. Roles

| | Owner | Admin | Staff |
|---|---|---|---|
| Products: add | yes | yes | no |
| Products: restock, cash sale | yes | yes | yes |
| Customer credit | yes | yes | yes |
| Reports | yes | yes | no |
| Manage users | yes | yes, but not Owner accounts | no |

Nobody can delete themselves or change their own role. New or reset passwords are temporary:
the person must choose their own at next sign-in, and a reset signs them out everywhere.

## 6. Security built in

Passwords are hashed (never stored as text) / 5 failed logins lock that username for 5 minutes /
sessions are HttpOnly, SameSite cookies / every change request needs a custom header (blocks
cross-site forgery) / strict Content-Security-Policy / all text is shown as text, never as HTML /
money is stored as whole centavos, so totals are always exact / writes are serialized so two
phones can't sell the last item twice.

## 7. Known limits (honest list)

* **Needs a connection to record anything.** The screens open offline, but sales and credit are
  saved on the server, so the app tells you you're offline instead of pretending to save.
  Offline queueing is a good future update.
* One store, one database. Uses SQLite, which is fine for a store's traffic, not for hundreds of
  simultaneous users.
* The server must stay running. Back up the database file.
* No password-reset email. An Owner or Admin resets passwords in Manage users.

## 8. Files

```
app.py                  server: login, roles, products, credit, reports, database upgrades
static/index.html       the app page
static/app.js           all screens
static/style.css        phone styling (light and dark mode)
static/sw.js            offline shell and update delivery
static/manifest.webmanifest   makes it installable
static/icons/           app icons (regenerate: python3 tools/make_icons.py)
tests/test_api.py       18 automated tests:  python3 -m unittest discover -s tests -v
```


## Supabase PostgreSQL on Render Free (version 1.1)

This version uses PostgreSQL **only when `DATABASE_URL` is set**. Without it, local
SQLite works as before. A database on Supabase persists across Render restarts,
provided the Supabase project remains active.

1. Supabase dashboard > Connect > Session pooler > URI. Replace the password
   placeholder with your database password. URL-encode special password characters.
2. Render > Web Service > Environment: set `DATABASE_URL` to that URI,
   `SARI_SECURE_COOKIES=1`, `SARI_TZ=Asia/Manila`, and set a **fixed, random**
   `SARI_SECRET_KEY` (never commit secrets to GitHub).
3. Render build command: `pip install -r requirements.txt`.
4. Render start command: `gunicorn --workers 1 --threads 4 --bind 0.0.0.0:$PORT app:app`.
5. Deploy; check logs for errors. Sign in using the initial credentials only on
   a new empty database and immediately change the password.
6. Create a **test product**, restart/redeploy Render, then confirm it remains.
   Test stock sales, customer credit, reports and user roles before real use.

**Existing data:** This does NOT automatically migrate old SQLite records to
PostgreSQL. If you have an existing local `sari_sari.db`, back it up first; a
separate data migration is required. Data previously lost from Render's
ephemeral filesystem cannot be recovered from this code.

**Limitations:** Supabase Free may pause inactive projects and does not replace
regular off-platform backups. Do not rely on free hosting alone for critical
financial records. Database initialization is designed for a new database;
future PostgreSQL schema changes require explicit migrations.
