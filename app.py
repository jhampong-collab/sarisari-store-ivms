"""
Sari-Sari Store Inventory Management System - mobile web app (PWA) server
===========================================================================
A Flask + SQLite server that the phone app talks to. The phone app itself is the
HTML/JS in the static/ folder; it can be added to a phone's home screen and updates
itself without reinstalling (see README.md).

Features (same as the desktop version):  login with roles (Owner / Admin / Staff),
products, cash sales, customer credit (utang), reports, and user management.

Run (development):   python3 app.py
Run (real use):      see README.md  (needs HTTPS for phones to install it)
"""

import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Flask, Response, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

# Bump this when you ship an update. Every phone sees an "update available" banner.
APP_VERSION = "1.1.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.config.update(
    DB_PATH=os.environ.get("SARI_DB", os.path.join(BASE_DIR, "sari_sari.db")),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Set SARI_SECURE_COOKIES=1 when the site is served over HTTPS (it should be).
    SESSION_COOKIE_SECURE=os.environ.get("SARI_SECURE_COOKIES") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=64 * 1024,
)


def _load_secret():
    key = os.environ.get("SARI_SECRET_KEY")
    if key:
        return key
    path = os.environ.get("SARI_SECRET_FILE", os.path.join(BASE_DIR, "secret.key"))
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    with open(path) as f:
        return f.read().strip()


app.secret_key = _load_secret()


# ======================================================================
# TIME AND MONEY HELPERS
# ======================================================================
def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.environ.get("SARI_TZ", "Asia/Manila"))
    except Exception:
        return timezone(timedelta(hours=8))


def now():
    return datetime.now(_tz())


def today():
    return now().date()


def parse_money(value, field, allow_zero=False):
    """Pesos (number or text) -> integer centavos. Money is never stored as floats."""
    try:
        d = Decimal(str(value).strip())
        if not d.is_finite():
            raise InvalidOperation
        cents = int((d.quantize(Decimal("0.01")) * 100))
    except (InvalidOperation, ValueError, AttributeError):
        raise ApiError(f"{field} must be a number.")
    if isinstance(value, bool):
        raise ApiError(f"{field} must be a number.")
    if cents < 0 or (cents == 0 and not allow_zero):
        raise ApiError(f"{field} must be greater than zero." if not allow_zero
                       else f"{field} cannot be negative.")
    if cents > 10_000_000_00:
        raise ApiError(f"{field} is too large.")
    return cents


def parse_int(value, field, minimum=0):
    if isinstance(value, bool):
        raise ApiError(f"{field} must be a whole number.")
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        n = int(value.strip())
    else:
        raise ApiError(f"{field} must be a whole number.")
    if n < minimum:
        raise ApiError(f"{field} must be at least {minimum}." if minimum
                       else f"{field} cannot be negative.")
    if n > 1_000_000:
        raise ApiError(f"{field} is too large.")
    return n


def peso(cents):
    return f"P{cents / 100:,.2f}"


def text_field(data, key, label, max_len, required=True):
    value = data.get(key, "")
    value = value.strip() if isinstance(value, str) else ""
    if required and not value:
        raise ApiError(f"{label} is required.")
    if len(value) > max_len:
        raise ApiError(f"{label} must be {max_len} characters or fewer.")
    return value


# ======================================================================
# DATABASE (SQLite) AND MIGRATIONS
# ======================================================================
# To change the database in a future update: ADD a new entry to the end of this list.
# Never edit an old entry. Phones keep their data; the server upgrades the database
# automatically the next time it starts.
MIGRATIONS = [
    # --- version 1: initial schema ---
    """
    CREATE TABLE users (
        username     TEXT PRIMARY KEY COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role         TEXT NOT NULL CHECK (role IN ('Owner','Admin','Staff')),
        must_change  INTEGER NOT NULL DEFAULT 0,
        session_ver  INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE products (
        id            TEXT PRIMARY KEY,
        name          TEXT NOT NULL,
        category      TEXT NOT NULL DEFAULT '',
        price_cents   INTEGER NOT NULL CHECK (price_cents >= 0),
        quantity      INTEGER NOT NULL CHECK (quantity >= 0),
        reorder_level INTEGER NOT NULL DEFAULT 10 CHECK (reorder_level >= 0),
        expiration    TEXT
    );
    CREATE TABLE sales (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id       TEXT NOT NULL,
        product_name     TEXT NOT NULL,
        quantity         INTEGER NOT NULL,
        unit_price_cents INTEGER NOT NULL,
        sold_at          TEXT NOT NULL,
        username         TEXT NOT NULL
    );
    CREATE TABLE customers (
        id            TEXT PRIMARY KEY,
        name          TEXT NOT NULL,
        contact       TEXT NOT NULL DEFAULT '',
        balance_cents INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0)
    );
    CREATE TABLE credit_tx (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id  TEXT NOT NULL REFERENCES customers(id),
        tx_type      TEXT NOT NULL CHECK (tx_type IN ('CHARGE','PAYMENT')),
        amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
        description  TEXT NOT NULL DEFAULT '',
        tx_at        TEXT NOT NULL,
        username     TEXT NOT NULL
    );
    """,
]

ROLE_OWNER, ROLE_ADMIN, ROLE_STAFF = "Owner", "Admin", "Staff"
ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_STAFF)
_ALL = {"add_product", "restock", "cash_sale", "credit", "reports", "manage_users"}
PERMISSIONS = {
    ROLE_OWNER: set(_ALL),
    ROLE_ADMIN: set(_ALL),  # same actions as Owner; user management blocks Owner accounts
    ROLE_STAFF: {"restock", "cash_sale", "credit"},
}


def has_permission(role, action):
    return action in PERMISSIONS.get(role, set())


# DATABASE_URL selects persistent PostgreSQL (e.g. Supabase session pooler).
# Without it, local development and existing SQLite tests work unchanged.
USE_POSTGRES = bool(os.environ.get("DATABASE_URL"))


class PGRow(dict):
    """Keep compatibility with SQLite Row's name and positional access."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class PGCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.rowcount = cursor.rowcount

    def fetchone(self):
        row = self.cursor.fetchone()
        return PGRow(row) if row is not None else None

    def fetchall(self):
        return [PGRow(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        for row in self.cursor:
            yield PGRow(row)


class PGConnection:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        # SQL is defined by the server, never interpolated from user input.
        sql = sql.replace("?", "%s")
        sql = sql.replace("COLLATE NOCASE", "COLLATE \"default\"")
        cursor = self.conn.execute(sql, params)
        return PGCursor(cursor)

    def close(self):
        self.conn.close()


PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS users (
 username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK (role IN ('Owner','Admin','Staff')),
 must_change INTEGER NOT NULL DEFAULT 0,
 session_ver INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_idx ON users (lower(username));
CREATE TABLE IF NOT EXISTS products (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
 price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
 quantity INTEGER NOT NULL CHECK (quantity >= 0),
 reorder_level INTEGER NOT NULL DEFAULT 10 CHECK (reorder_level >= 0),
 expiration TEXT
);
CREATE TABLE IF NOT EXISTS sales (
 id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
 product_id TEXT NOT NULL, product_name TEXT NOT NULL,
 quantity INTEGER NOT NULL, unit_price_cents INTEGER NOT NULL,
 sold_at TEXT NOT NULL, username TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
 id TEXT PRIMARY KEY, name TEXT NOT NULL,
 contact TEXT NOT NULL DEFAULT '',
 balance_cents INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0)
);
CREATE TABLE IF NOT EXISTS credit_tx (
 id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
 customer_id TEXT NOT NULL REFERENCES customers(id),
 tx_type TEXT NOT NULL CHECK (tx_type IN ('CHARGE','PAYMENT')),
 amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
 description TEXT NOT NULL DEFAULT '', tx_at TEXT NOT NULL,
 username TEXT NOT NULL
);
"""


def connect():
    if USE_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row
        return PGConnection(psycopg.connect(
            os.environ["DATABASE_URL"], autocommit=True,
            row_factory=dict_row, connect_timeout=15,
            sslmode="require"))
    db = sqlite3.connect(app.config["DB_PATH"], timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    """Create/upgrade the selected database and first Owner account."""
    db = connect()
    try:
        if USE_POSTGRES:
            db.execute("BEGIN")
            try:
                # Serialize schema initialization across multiple workers.
                db.execute("SELECT pg_advisory_xact_lock(82431760)")
                for statement in PG_SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("INSERT INTO schema_migrations (version) VALUES (1) "
                           "ON CONFLICT (version) DO NOTHING")
                if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                    db.execute("INSERT INTO users (username, password_hash, role, must_change) "
                               "VALUES ('admin', ?, 'Owner', 1)",
                               (generate_password_hash("admin123"),))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        else:
            db.execute("PRAGMA journal_mode = WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            for i in range(version, len(MIGRATIONS)):
                db.executescript(MIGRATIONS[i])
                db.execute(f"PRAGMA user_version = {i + 1}")
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                db.execute("INSERT INTO users (username, password_hash, role, must_change) "
                           "VALUES ('admin', ?, 'Owner', 1)", (generate_password_hash("admin123"),))
    finally:
        db.close()


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def write_tx():
    """Serialize writes across all web workers to avoid overselling stock."""
    db = get_db()
    db.execute("BEGIN" if USE_POSTGRES else "BEGIN IMMEDIATE")
    try:
        if USE_POSTGRES:
            db.execute("SELECT pg_advisory_xact_lock(82431761)")
        yield db
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


# ======================================================================
# ERRORS, SECURITY HEADERS
# ======================================================================
class ApiError(Exception):
    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


@app.errorhandler(ApiError)
def handle_api_error(e):
    body = {"error": e.message}
    if e.code:
        body["code"] = e.code
    return jsonify(body), e.status


@app.errorhandler(404)
def handle_404(_e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found."}), 404
    return Response("Not found", status=404)


@app.errorhandler(405)
def handle_405(_e):
    return jsonify({"error": "Method not allowed."}), 405


@app.errorhandler(413)
def handle_413(_e):
    return jsonify({"error": "Request too large."}), 413


@app.errorhandler(Exception)
def handle_unexpected(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    app.logger.exception("Unexpected error")
    return jsonify({"error": "Something went wrong on the server."}), 500


@app.before_request
def csrf_guard():
    # A custom header can't be added by another website without our permission (CORS),
    # so requiring it on every change request blocks cross-site request forgery.
    if request.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("X-Requested-With") != "sari-app":
            raise ApiError("Bad request.", 400)


@app.after_request
def add_headers(resp):
    resp.headers["X-App-Version"] = APP_VERSION
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    else:
        resp.headers["Cache-Control"] = "no-cache"  # always check for a newer file
    return resp


# ======================================================================
# AUTH
# ======================================================================
def current_user():
    if "user" in g:
        return g.user
    g.user = None
    username = session.get("u")
    if username:
        row = get_db().execute(
            "SELECT username, role, must_change, session_ver FROM users WHERE username = ?",
            (username,)).fetchone()
        # session_ver changes whenever the password is reset, so old logins stop working
        if row and row["session_ver"] == session.get("sv"):
            g.user = row
    return g.user


def login_required(action=None, allow_must_change=False):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                raise ApiError("Please log in.", 401)
            if user["must_change"] and not allow_must_change:
                raise ApiError("You must change your password first.", 403, code="must_change")
            if action and not has_permission(user["role"], action):
                raise ApiError(f"Your role ({user['role']}) is not allowed to do this.", 403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


_FAILED = {}  # (ip, username) -> [count, locked_until]
MAX_FAILS, LOCK_SECONDS = 5, 300
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def _throttle_key(username):
    return (request.remote_addr or "?", username.lower())


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if not username or not password:
        raise ApiError("Please enter both username and password.")
    key = _throttle_key(username)
    entry = _FAILED.get(key)
    if entry and entry[1] > time.time():
        wait = int(entry[1] - time.time()) // 60 + 1
        raise ApiError(f"Too many failed attempts. Try again in about {wait} minute(s).", 429)
    row = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password) and row
    if not ok:
        count = (entry[0] if entry else 0) + 1
        _FAILED[key] = [count, time.time() + LOCK_SECONDS if count >= MAX_FAILS else 0]
        if count >= MAX_FAILS:
            _FAILED[key][0] = 0
        raise ApiError("Invalid username or password.", 401)
    _FAILED.pop(key, None)
    session.clear()
    session.permanent = True
    session["u"], session["sv"] = row["username"], row["session_ver"]
    return jsonify(me_payload(row))


def me_payload(row):
    return {"username": row["username"], "role": row["role"],
            "must_change": bool(row["must_change"]), "version": APP_VERSION,
            "permissions": sorted(PERMISSIONS[row["role"]])}


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
@login_required(allow_must_change=True)
def me():
    return jsonify(me_payload(current_user()))


@app.get("/api/version")
def version():
    return jsonify({"version": APP_VERSION})


@app.post("/api/change-password")
@login_required(allow_must_change=True)
def change_password():
    data = request.get_json(silent=True) or {}
    user = current_user()
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE username = ?", (user["username"],)).fetchone()
    if not check_password_hash(row["password_hash"], str(data.get("current_password", ""))):
        raise ApiError("Your current password is incorrect.", 403)
    new = str(data.get("new_password", ""))
    check_new_password(new, current=str(data.get("current_password", "")))
    with write_tx() as tx:
        tx.execute("UPDATE users SET password_hash = ?, must_change = 0, "
                   "session_ver = session_ver + 1 WHERE username = ?",
                   (generate_password_hash(new), user["username"]))
    session["sv"] = row["session_ver"] + 1  # this phone stays logged in; others are signed out
    g.pop("user", None)
    return jsonify(me_payload(current_user()))


def check_new_password(new, current=None):
    if len(new) < 6:
        raise ApiError("The new password must be at least 6 characters.")
    if len(new) > 100:
        raise ApiError("The new password is too long.")
    if new == current:
        raise ApiError("Choose a password different from the current one.")
    if new.lower() in {"admin123", "password", "123456", "password123"}:
        raise ApiError("That password is too easy to guess. Choose another.")


# ======================================================================
# PRODUCTS AND CASH SALES
# ======================================================================
def product_json(row):
    exp = row["expiration"]
    if exp and today() > date.fromisoformat(exp):
        status = "EXPIRED"
    elif row["quantity"] <= row["reorder_level"]:
        status = "LOW STOCK"
    else:
        status = "OK"
    return {"id": row["id"], "name": row["name"], "category": row["category"],
            "price": row["price_cents"] / 100, "quantity": row["quantity"],
            "reorder_level": row["reorder_level"], "expiration": exp, "status": status}


def get_product(db, product_id):
    row = db.execute("SELECT * FROM products WHERE id = ?", (str(product_id).strip(),)).fetchone()
    if row is None:
        raise ApiError(f"Product ID '{product_id}' not found.", 404)
    return row


@app.get("/api/products")
@login_required()
def list_products():
    rows = get_db().execute("SELECT * FROM products ORDER BY name COLLATE NOCASE").fetchall()
    return jsonify([product_json(r) for r in rows])


@app.post("/api/products")
@login_required("add_product")
def add_product():
    d = request.get_json(silent=True) or {}
    pid = text_field(d, "id", "Product ID", 30)
    if not re.fullmatch(r"[\w.\-]+", pid):
        raise ApiError("Product ID can only use letters, numbers, dots, dashes, and underscores.")
    name = text_field(d, "name", "Name", 80)
    category = text_field(d, "category", "Category", 40, required=False)
    price = parse_money(d.get("price"), "Price", allow_zero=True)
    qty = parse_int(d.get("quantity"), "Quantity")
    reorder = parse_int(d.get("reorder_level", 10), "Reorder level")
    exp = text_field(d, "expiration", "Expiration date", 10, required=False) or None
    if exp:
        try:
            date.fromisoformat(exp)
        except ValueError:
            raise ApiError("Expiration date must be a real date (YYYY-MM-DD).")
    with write_tx() as db:
        if db.execute("SELECT 1 FROM products WHERE id = ?", (pid,)).fetchone():
            raise ApiError(f"Product ID '{pid}' already exists.", 409)
        db.execute("INSERT INTO products (id, name, category, price_cents, quantity, "
                   "reorder_level, expiration) VALUES (?,?,?,?,?,?,?)",
                   (pid, name, category, price, qty, reorder, exp))
    return jsonify(product_json(get_product(get_db(), pid))), 201


@app.post("/api/products/<product_id>/restock")
@login_required("restock")
def restock(product_id):
    qty = parse_int((request.get_json(silent=True) or {}).get("quantity"), "Amount to add", 1)
    with write_tx() as db:
        product = get_product(db, product_id)
        db.execute("UPDATE products SET quantity = quantity + ? WHERE id = ?", (qty, product["id"]))
    return jsonify(product_json(get_product(get_db(), product_id)))


@app.post("/api/sales")
@login_required("cash_sale")
def cash_sale():
    d = request.get_json(silent=True) or {}
    qty = parse_int(d.get("quantity"), "Quantity", 1)
    with write_tx() as db:
        product = get_product(db, d.get("product_id", ""))
        cur = db.execute("UPDATE products SET quantity = quantity - ? WHERE id = ? AND quantity >= ?",
                         (qty, product["id"], qty))
        if cur.rowcount == 0:
            raise ApiError(f"Not enough stock for '{product['name']}'. Available: {product['quantity']}")
        db.execute("INSERT INTO sales (product_id, product_name, quantity, unit_price_cents, "
                   "sold_at, username) VALUES (?,?,?,?,?,?)",
                   (product["id"], product["name"], qty, product["price_cents"],
                    now().isoformat(timespec="seconds"), current_user()["username"]))
    total = qty * product["price_cents"]
    return jsonify({"total": total / 100, "message": f"Sold {qty} x {product['name']} = {peso(total)}"})


# ======================================================================
# CUSTOMER CREDIT (UTANG)
# ======================================================================
def customer_json(row):
    return {"id": row["id"], "name": row["name"], "contact": row["contact"],
            "balance": row["balance_cents"] / 100}


def get_customer(db, customer_id):
    row = db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if row is None:
        raise ApiError(f"Customer ID '{customer_id}' not found.", 404)
    return row


@app.get("/api/customers")
@login_required("credit")
def list_customers():
    rows = get_db().execute("SELECT * FROM customers ORDER BY name COLLATE NOCASE").fetchall()
    return jsonify([customer_json(r) for r in rows])


@app.post("/api/customers")
@login_required("credit")
def add_customer():
    d = request.get_json(silent=True) or {}
    name = text_field(d, "name", "Customer name", 80)
    contact = text_field(d, "contact", "Contact", 40, required=False)
    with write_tx() as db:
        nums = [int(r["id"][1:]) for r in db.execute("SELECT id FROM customers") if re.fullmatch(r"C\d+", r["id"])]
        cid = f"C{(max(nums) + 1 if nums else 1):03d}"
        db.execute("INSERT INTO customers (id, name, contact) VALUES (?,?,?)", (cid, name, contact))
    return jsonify(customer_json(get_customer(get_db(), cid))), 201


@app.post("/api/customers/<customer_id>/charge")
@login_required("credit")
def charge(customer_id):
    d = request.get_json(silent=True) or {}
    qty = parse_int(d.get("quantity"), "Quantity", 1)
    with write_tx() as db:
        customer = get_customer(db, customer_id)
        product = get_product(db, d.get("product_id", ""))
        cur = db.execute("UPDATE products SET quantity = quantity - ? WHERE id = ? AND quantity >= ?",
                         (qty, product["id"], qty))
        if cur.rowcount == 0:
            raise ApiError(f"Not enough stock for '{product['name']}'. Available: {product['quantity']}")
        amount = qty * product["price_cents"]
        db.execute("UPDATE customers SET balance_cents = balance_cents + ? WHERE id = ?",
                   (amount, customer["id"]))
        db.execute("INSERT INTO credit_tx (customer_id, tx_type, amount_cents, description, tx_at, username) "
                   "VALUES (?,?,?,?,?,?)",
                   (customer["id"], "CHARGE", amount,
                    f"{qty} x {product['name']} @ {peso(product['price_cents'])}",
                    now().isoformat(timespec="seconds"), current_user()["username"]))
    updated = get_customer(get_db(), customer_id)
    return jsonify({**customer_json(updated),
                    "message": f"Charged {peso(amount)} to {updated['name']}."})


@app.post("/api/customers/<customer_id>/payment")
@login_required("credit")
def payment(customer_id):
    amount = parse_money((request.get_json(silent=True) or {}).get("amount"), "Payment")
    with write_tx() as db:
        customer = get_customer(db, customer_id)
        if amount > customer["balance_cents"]:
            raise ApiError(f"Payment is more than the balance ({peso(customer['balance_cents'])}).")
        db.execute("UPDATE customers SET balance_cents = balance_cents - ? WHERE id = ?",
                   (amount, customer["id"]))
        db.execute("INSERT INTO credit_tx (customer_id, tx_type, amount_cents, description, tx_at, username) "
                   "VALUES (?,?,?,?,?,?)",
                   (customer["id"], "PAYMENT", amount, "Payment received",
                    now().isoformat(timespec="seconds"), current_user()["username"]))
    updated = get_customer(get_db(), customer_id)
    return jsonify({**customer_json(updated),
                    "message": f"Payment of {peso(amount)} recorded for {updated['name']}."})


@app.get("/api/customers/<customer_id>/ledger")
@login_required("credit")
def ledger(customer_id):
    db = get_db()
    customer = get_customer(db, customer_id)
    rows = db.execute("SELECT * FROM credit_tx WHERE customer_id = ? ORDER BY id DESC", (customer_id,))
    return jsonify({"customer": customer_json(customer), "transactions": [
        {"id": r["id"], "type": r["tx_type"], "amount": r["amount_cents"] / 100,
         "description": r["description"], "at": r["tx_at"], "by": r["username"]} for r in rows]})


# ======================================================================
# REPORTS
# ======================================================================
@app.get("/api/reports")
@login_required("reports")
def reports():
    db = get_db()
    one = lambda sql, *a: db.execute(sql, a).fetchone()[0] or 0
    today_prefix = today().isoformat()
    best = db.execute("SELECT product_name, SUM(quantity) AS qty FROM sales GROUP BY product_id "
                      "ORDER BY qty DESC, product_name LIMIT 5").fetchall()
    owing = db.execute("SELECT * FROM customers WHERE balance_cents > 0 "
                       "ORDER BY balance_cents DESC").fetchall()
    products = [product_json(r) for r in db.execute("SELECT * FROM products ORDER BY name COLLATE NOCASE")]
    return jsonify({
        "cash_sales_total": one("SELECT SUM(quantity * unit_price_cents) FROM sales") / 100,
        "cash_sales_today": one("SELECT SUM(quantity * unit_price_cents) FROM sales WHERE sold_at LIKE ?",
                                today_prefix + "%") / 100,
        "credit_charged": one("SELECT SUM(amount_cents) FROM credit_tx WHERE tx_type='CHARGE'") / 100,
        "credit_paid": one("SELECT SUM(amount_cents) FROM credit_tx WHERE tx_type='PAYMENT'") / 100,
        "outstanding": one("SELECT SUM(balance_cents) FROM customers") / 100,
        "best_sellers": [{"name": r["product_name"], "quantity": r["qty"]} for r in best],
        "customers_with_balance": [customer_json(r) for r in owing],
        "low_stock": [p for p in products if p["status"] == "LOW STOCK"],
        "expired": [p for p in products if p["status"] == "EXPIRED"],
    })


# ======================================================================
# USER MANAGEMENT (Owner and Admin only)
# ======================================================================
USERNAME_RE = re.compile(r"[A-Za-z0-9._-]{3,30}")


def allowed_roles(actor_role):
    return list(ROLES) if actor_role == ROLE_OWNER else [ROLE_ADMIN, ROLE_STAFF]


def can_manage(actor_role, target_role):
    return actor_role == ROLE_OWNER or target_role != ROLE_OWNER


@app.get("/api/users")
@login_required("manage_users")
def list_users():
    rows = get_db().execute("SELECT username, role FROM users ORDER BY username COLLATE NOCASE")
    actor = current_user()
    return jsonify({"allowed_roles": allowed_roles(actor["role"]),
                    "users": [{"username": r["username"], "role": r["role"],
                               "editable": can_manage(actor["role"], r["role"]) and r["username"].lower() != actor["username"].lower()}
                              for r in rows]})


@app.post("/api/users")
@login_required("manage_users")
def create_user():
    actor = current_user()
    d = request.get_json(silent=True) or {}
    username = str(d.get("username", "")).strip()
    password, role = str(d.get("password", "")), d.get("role")
    if not USERNAME_RE.fullmatch(username):
        raise ApiError("Username must be 3-30 characters: letters, numbers, dots, dashes, underscores.")
    if role not in allowed_roles(actor["role"]):
        raise ApiError(f"You can't create {role} accounts." if role in ROLES else "Choose a role.", 403)
    check_new_password(password)
    with write_tx() as db:
        if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ApiError(f"Username '{username}' already exists.", 409)
        # The person must pick their own password the first time they sign in.
        db.execute("INSERT INTO users (username, password_hash, role, must_change) VALUES (?,?,?,1)",
                   (username, generate_password_hash(password), role))
    return jsonify({"username": username, "role": role}), 201


def _target(db, username):
    row = db.execute("SELECT username, role FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        raise ApiError("User not found.", 404)
    return row


@app.patch("/api/users/<username>")
@login_required("manage_users")
def update_user(username):
    actor = current_user()
    d = request.get_json(silent=True) or {}
    with write_tx() as db:
        target = _target(db, username)
        if not can_manage(actor["role"], target["role"]):
            raise ApiError("Admins can't modify Owner accounts.", 403)
        is_self = target["username"].lower() == actor["username"].lower()
        new_pw, new_role = d.get("password"), d.get("role")
        if new_pw is None and new_role is None:
            raise ApiError("Nothing to change.")
        if is_self:
            raise ApiError("Use Change Password for your own account. You can't change your own role.", 403)
        if new_role is not None and new_role != target["role"]:
            if new_role not in allowed_roles(actor["role"]):
                raise ApiError(f"You can't assign the {new_role} role.", 403)
            db.execute("UPDATE users SET role = ?, session_ver = session_ver + 1 WHERE username = ?",
                       (new_role, target["username"]))
        if new_pw is not None:
            check_new_password(str(new_pw))
            db.execute("UPDATE users SET password_hash = ?, must_change = 1, session_ver = session_ver + 1 "
                       "WHERE username = ?", (generate_password_hash(str(new_pw)), target["username"]))
    return jsonify({"ok": True})


@app.delete("/api/users/<username>")
@login_required("manage_users")
def delete_user(username):
    actor = current_user()
    with write_tx() as db:
        target = _target(db, username)
        if target["username"].lower() == actor["username"].lower():
            raise ApiError("You can't delete the account you are logged in with.", 403)
        if not can_manage(actor["role"], target["role"]):
            raise ApiError("Admins can't delete Owner accounts.", 403)
        db.execute("DELETE FROM users WHERE username = ?", (target["username"],))
    return jsonify({"ok": True})


# ======================================================================
# THE APP FILES (served with the version filled in so updates are detected)
# ======================================================================
def _static_text(name):
    with open(os.path.join(STATIC_DIR, name), encoding="utf-8") as f:
        return f.read().replace("__VERSION__", APP_VERSION)


@app.get("/")
def index():
    return Response(_static_text("index.html"), mimetype="text/html")


@app.get("/sw.js")
def service_worker():
    resp = Response(_static_text("sw.js"), mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.get("/manifest.webmanifest")
def manifest():
    return Response(_static_text("manifest.webmanifest"), mimetype="application/manifest+json")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "version": APP_VERSION})


init_db()

if __name__ == "__main__":
    # Development server only. For real use see README.md (a proper server + HTTPS).
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")))
