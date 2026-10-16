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
import csv
import io
import re
import secrets
import sqlite3
import time
import json
import urllib.request
import urllib.error
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Flask, Response, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

# Bump this when you ship an update. Every phone sees an "update available" banner.
APP_VERSION = "2.0.0-test.4"

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
    # v2: nullable costs protect historical records (unknown != zero).
    """
    ALTER TABLE products ADD COLUMN cost_cents INTEGER CHECK (cost_cents >= 0);
    ALTER TABLE sales ADD COLUMN unit_cost_cents INTEGER CHECK (unit_cost_cents >= 0);
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
                db.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS cost_cents INTEGER CHECK (cost_cents >= 0)")
                db.execute("ALTER TABLE sales ADD COLUMN IF NOT EXISTS unit_cost_cents INTEGER CHECK (unit_cost_cents >= 0)")
                db.execute("INSERT INTO schema_migrations (version) VALUES (2) "
                           "ON CONFLICT (version) DO NOTHING")
                db.execute("INSERT INTO schema_migrations (version) VALUES (1) "
                           "ON CONFLICT (version) DO NOTHING")
                # v2 test: never bootstrap an insecure default legacy admin.
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
            # v2 test: never bootstrap an insecure default legacy admin.
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
def block_legacy_business_api():
    # TEST BUILD: original endpoints are NOT tenant-aware; never expose them.
    if request.path.startswith('/api/') and not (request.path.startswith('/api/v2/') or request.path == '/api/version'):
        return jsonify({'error': 'This legacy endpoint is disabled until tenant isolation is implemented.'}), 403


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
        "connect-src 'self' https:; manifest-src 'self'; worker-src 'self'; "
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
            "reorder_level": row["reorder_level"], "expiration": exp, "status": status,
            "cost": row["cost_cents"] / 100 if row["cost_cents"] is not None else None}


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
    cost = parse_money(d.get("cost"), "Purchase cost", allow_zero=True) if str(d.get("cost", "")).strip() else None
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
                   "reorder_level, expiration, cost_cents) VALUES (?,?,?,?,?,?,?,?)",
                   (pid, name, category, price, qty, reorder, exp, cost))
    return jsonify(product_json(get_product(get_db(), pid))), 201


@app.patch("/api/products/<product_id>/cost")
@login_required("add_product")
def set_product_cost(product_id):
    d = request.get_json(silent=True) or {}
    cost = parse_money(d.get("cost"), "Purchase cost", allow_zero=True)
    with write_tx() as db:
        product = get_product(db, product_id)
        db.execute("UPDATE products SET cost_cents = ? WHERE id = ?", (cost, product["id"]))
    return jsonify(product_json(get_product(get_db(), product_id)))


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
                   "sold_at, username, unit_cost_cents) VALUES (?,?,?,?,?,?,?)",
                   (product["id"], product["name"], qty, product["price_cents"],
                    now().isoformat(timespec="seconds"), current_user()["username"], product["cost_cents"]))
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
    best = db.execute("SELECT product_name, SUM(quantity) AS qty FROM sales GROUP BY product_id, product_name "
                      "ORDER BY qty DESC, product_name LIMIT 5").fetchall()
    today_rows = db.execute("SELECT quantity, unit_price_cents, unit_cost_cents FROM sales WHERE sold_at LIKE ?", (today_prefix + "%",)).fetchall()
    today_known_profit = sum(r["quantity"] * (r["unit_price_cents"] - r["unit_cost_cents"]) for r in today_rows if r["unit_cost_cents"] is not None)
    today_unknown = sum(r["quantity"] for r in today_rows if r["unit_cost_cents"] is None)
    today_transactions = one("SELECT COUNT(*) FROM sales WHERE sold_at LIKE ?", today_prefix + "%")
    owing = db.execute("SELECT * FROM customers WHERE balance_cents > 0 "
                       "ORDER BY balance_cents DESC").fetchall()
    products = [product_json(r) for r in db.execute("SELECT * FROM products ORDER BY name COLLATE NOCASE")]
    return jsonify({
        "today_transactions": today_transactions,
        "today_profit_known": today_known_profit / 100,
        "today_profit_missing_cost_units": today_unknown,
        "today_profit_complete": today_unknown == 0,
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


@app.get("/api/export/<kind>.csv")
@login_required("reports")
def export_csv(kind):
    # Spreadsheet-safe values and no modification to stored records.
    queries = {
        "products": ("SELECT id, name, category, price_cents, cost_cents, quantity, reorder_level, expiration FROM products ORDER BY name", ("id", "name", "category", "price_cents", "cost_cents", "quantity", "reorder_level", "expiration")),
        "sales": ("SELECT id, product_id, product_name, quantity, unit_price_cents, unit_cost_cents, sold_at, username FROM sales ORDER BY id DESC", ("id", "product_id", "product_name", "quantity", "unit_price_cents", "unit_cost_cents", "sold_at", "username")),
        "credit": ("SELECT id, customer_id, tx_type, amount_cents, description, tx_at, username FROM credit_tx ORDER BY id DESC", ("id", "customer_id", "tx_type", "amount_cents", "description", "tx_at", "username")),
    }
    if kind not in queries:
        raise ApiError("Unknown export type", 404)
    query, cols = queries[kind]
    rows = get_db().execute(query).fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(cols)
    for row in rows:
        cells = []
        for col in cols:
            value = row[col]
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            cells.append("" if value is None else value)
        writer.writerow(cells)
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=sari-sari-{kind}.csv", "Cache-Control": "no-store"})


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
    return send_from_directory(STATIC_DIR, "onboarding.html")


@app.get("/sw.js")
def service_worker():
    resp = Response("self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.registration.unregister().then(()=>caches.keys().then(keys=>Promise.all(keys.map(k=>caches.delete(k))))));", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.get("/manifest.webmanifest")
def manifest():
    return Response(_static_text("manifest.webmanifest"), mimetype="application/manifest+json")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "version": APP_VERSION})


@app.get('/dashboard')
def v2_dashboard():
    return send_from_directory(STATIC_DIR, 'dashboard.html')


@app.get('/api/v2/config')
def v2_config():
    return jsonify({'url': os.environ.get('SUPABASE_URL','').rstrip('/'), 'key': os.environ.get('SUPABASE_PUBLISHABLE_KEY','')})


def _supabase_request(path, method='GET', payload=None, bearer=None):
    base = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key = os.environ.get('SUPABASE_PUBLISHABLE_KEY', '')
    if not base.startswith('https://') or not key:
        raise ApiError('Supabase authentication is not configured.', 503)
    headers = {'apikey': key, 'Content-Type': 'application/json'}
    if bearer:
        headers['Authorization'] = 'Bearer ' + bearer
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base + '/auth/v1/' + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise ApiError('Authentication request was rejected.', 401 if e.code in (401,403) else 502)
    except (urllib.error.URLError, TimeoutError):
        raise ApiError('Authentication service is unavailable.', 503)


def _verified_auth_user():
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer ') or len(header) > 4096:
        raise ApiError('Sign in required.', 401)
    user = _supabase_request('user', bearer=header[7:])
    if not user.get('email_confirmed_at'):
        raise ApiError('Verify your email before activating your store.', 403)
    return user


@app.post('/api/v2/provision')
def v2_provision():
    user = _verified_auth_user()
    user_id = user['id']
    metadata = user.get('user_metadata') or {}
    name = str(metadata.get('store_name') or '').strip()
    full_name = str(metadata.get('full_name') or '').strip()
    if not (1 <= len(name) <= 120 and 1 <= len(full_name) <= 120):
        raise ApiError('Store name and owner name are required.', 400)
    if not USE_POSTGRES:
        raise ApiError('Test PostgreSQL connection required.', 503)
    db = get_db()
    db.execute('BEGIN')
    try:
        db.execute('SELECT pg_advisory_xact_lock(hashtext(?))', (user_id,))
        row = db.execute("SELECT store_id FROM store_memberships WHERE user_id = ? AND role = 'owner' LIMIT 1", (user_id,)).fetchone()
        if row:
            store_id = str(row['store_id'])
        else:
            store = db.execute('INSERT INTO stores(name) VALUES (?) RETURNING id', (name,)).fetchone()
            store_id = str(store['id'])
            db.execute("INSERT INTO store_memberships(store_id,user_id,role) VALUES (?,?,'owner')", (store_id,user_id))
        db.execute('INSERT INTO user_profiles(user_id,full_name) VALUES (?,?) ON CONFLICT (user_id) DO UPDATE SET full_name=EXCLUDED.full_name', (user_id,full_name))
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise
    return jsonify({'ok': True, 'store_id': store_id, 'message': 'Store account activated. Opening your private dashboard.'})


@app.get('/api/v2/account')
def v2_account():
    user = _verified_auth_user()
    row = get_db().execute("SELECT s.id,s.name,m.role FROM store_memberships m JOIN stores s ON s.id=m.store_id WHERE m.user_id=? ORDER BY CASE WHEN m.role='owner' THEN 0 ELSE 1 END, s.name LIMIT 1", (user['id'],)).fetchone()
    return jsonify({'email': user.get('email'), 'store': dict(row) if row else None})



# STEP 6: Tenant-safe business endpoints. Never accept store_id from the browser.
def _v2_context():
    user = _verified_auth_user()
    if not USE_POSTGRES:
        raise ApiError('PostgreSQL is required.', 503)
    row = get_db().execute('SELECT s.id,s.name,m.role FROM store_memberships m '
        'JOIN stores s ON s.id=m.store_id WHERE m.user_id=? ORDER BY s.name LIMIT 1',
        (user['id'],)).fetchone()
    if not row:
        raise ApiError('Activate your verified store first.', 403)
    if row['role'] not in ('owner','manager','cashier'):
        raise ApiError('Unrecognized membership role.', 403)
    return str(row['id']), str(user['id']), row


def _v2_require(*roles):
    sid, uid, store = _v2_context()
    if store['role'] not in roles:
        raise ApiError('Your store role does not allow this operation.', 403)
    return sid, uid, store


@app.get('/api/v2/staff/invitations')
def v2_list_staff_invitations():
    sid, _, _ = _v2_require('owner')
    rows = get_db().execute('SELECT id,email,role,status,created_at,expires_at FROM staff_invitations WHERE store_id=? ORDER BY created_at DESC LIMIT 100', (sid,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post('/api/v2/staff/invitations')
def v2_invite_staff():
    sid, uid, _ = _v2_require('owner')
    d = request.get_json(silent=True) or {}
    email = str(d.get('email', '')).strip().lower()
    role = str(d.get('role', '')).strip().lower()
    if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise ApiError('Enter a valid staff email address.', 400)
    if role not in ('manager', 'cashier'):
        raise ApiError('Choose manager or cashier.', 400)
    if email == str(_verified_auth_user().get('email', '')).strip().lower():
        raise ApiError('You cannot invite yourself.', 400)
    with write_tx() as db:
        db.execute("UPDATE staff_invitations SET status='expired' WHERE store_id=? AND status='pending' AND expires_at<=now()", (sid,))
        already = db.execute('SELECT 1 FROM store_memberships m JOIN auth.users u ON u.id=m.user_id WHERE m.store_id=? AND lower(u.email)=?', (sid,email)).fetchone()
        if already: raise ApiError('This user is already a store member.', 409)
        exists = db.execute("SELECT 1 FROM staff_invitations WHERE store_id=? AND lower(trim(email))=? AND status='pending'", (sid,email)).fetchone()
        if exists: raise ApiError('An invitation is already pending for this email.', 409)
        row = db.execute("INSERT INTO staff_invitations(store_id,email,role,invited_by) VALUES (?,?,?,?) RETURNING id", (sid,email,role,uid)).fetchone()
    return jsonify({'id':str(row['id']), 'email':email,'role':role,'message':'Invitation saved. Share its ID privately with the invited staff member; email delivery is not enabled yet.'}), 201


@app.post('/api/v2/staff/invitations/<invitation_id>/revoke')
def v2_revoke_invitation(invitation_id):
    sid, _, _ = _v2_require('owner')
    with write_tx() as db:
        row = db.execute("UPDATE staff_invitations SET status='revoked' WHERE id=? AND store_id=? AND status='pending' RETURNING id", (invitation_id,sid)).fetchone()
        if not row: raise ApiError('Pending invitation not found.', 404)
    return jsonify({'ok':True})


@app.post('/api/v2/staff/accept')
def v2_accept_invitation():
    user = _verified_auth_user()
    uid = str(user['id'])
    email = str(user.get('email','')).strip().lower()
    d = request.get_json(silent=True) or {}
    invitation_id = str(d.get('invitation_id','')).strip()
    try:
        import uuid
        uuid.UUID(invitation_id)
    except (ValueError, AttributeError):
        raise ApiError('Invalid invitation ID.', 400)
    with write_tx() as db:
        inv = db.execute('SELECT * FROM staff_invitations WHERE id=? FOR UPDATE', (invitation_id,)).fetchone()
        if not inv or inv['status']!='pending': raise ApiError('Invitation is not pending.', 404)
        if inv['expires_at'] <= datetime.now(timezone.utc): raise ApiError('Invitation has expired.', 410)
        if inv['email'].strip().lower()!=email: raise ApiError('This invitation belongs to a different verified email.', 403)
        exists = db.execute('SELECT 1 FROM store_memberships WHERE store_id=? AND user_id=?', (inv['store_id'],uid)).fetchone()
        if exists: raise ApiError('You already belong to this store.', 409)
        db.execute('INSERT INTO store_memberships(store_id,user_id,role) VALUES (?,?,?)', (inv['store_id'],uid,inv['role']))
        db.execute("UPDATE staff_invitations SET status='accepted',accepted_at=now() WHERE id=?", (invitation_id,))
        db.execute("INSERT INTO user_profiles(user_id,full_name) VALUES (?,?) ON CONFLICT(user_id) DO NOTHING", (uid, str((user.get('user_metadata') or {}).get('full_name') or email).strip()[:120]))
    return jsonify({'ok':True,'message':'Invitation accepted. Open your store dashboard.'})


@app.get('/api/v2/staff/members')
def v2_staff_members():
    sid, _, _ = _v2_require('owner')
    rows = get_db().execute('SELECT m.user_id,m.role,u.email,p.full_name FROM store_memberships m JOIN auth.users u ON u.id=m.user_id LEFT JOIN user_profiles p ON p.user_id=m.user_id WHERE m.store_id=? ORDER BY m.created_at', (sid,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post('/api/v2/staff/members/<user_id>/remove')
def v2_remove_staff(user_id):
    sid, _, _ = _v2_require('owner')
    with write_tx() as db:
        row=db.execute("DELETE FROM store_memberships WHERE store_id=? AND user_id=? AND role IN ('manager','cashier') RETURNING user_id", (sid,user_id)).fetchone()
        if not row: raise ApiError('Staff member not found.', 404)
    return jsonify({'ok':True})


def _v2_product(db, sid, pid):
    row = db.execute('SELECT * FROM products WHERE store_id=? AND id=?', (sid,pid)).fetchone()
    if not row:
        raise ApiError('Product not found in your store.', 404)
    return row


def _v2_customer(db, sid, cid):
    row = db.execute('SELECT * FROM customers WHERE store_id=? AND id=?', (sid,cid)).fetchone()
    if not row:
        raise ApiError('Customer not found in your store.', 404)
    return row


@app.get('/api/v2/dashboard')
def v2_dashboard_data():
    sid, _, store = _v2_context()
    db = get_db()
    prows = db.execute('SELECT * FROM products WHERE store_id=? ORDER BY name', (sid,)).fetchall()
    customers = db.execute('SELECT * FROM customers WHERE store_id=? ORDER BY name', (sid,)).fetchall()
    today_sales = db.execute('SELECT quantity,unit_price_cents,unit_cost_cents FROM sales WHERE store_id=? AND sold_at LIKE ?',
        (sid,today().isoformat()+'%')).fetchall()
    all_sales = db.execute('SELECT COALESCE(SUM(quantity*unit_price_cents),0) AS total FROM sales WHERE store_id=?', (sid,)).fetchone()
    low = [product_json(r) for r in prows if r['quantity'] <= r['reorder_level']]
    return jsonify({'store': {'id':sid,'name':store['name'],'role':store['role']},
        'product_count':len(prows),'low_stock':low,'customer_count':len(customers),
        'outstanding':sum(r['balance_cents'] for r in customers)/100,
        'today_sales':sum(r['quantity']*r['unit_price_cents'] for r in today_sales)/100,
        'today_transactions':len(today_sales),
        'today_profit_known':sum(r['quantity']*(r['unit_price_cents']-r['unit_cost_cents']) for r in today_sales if r['unit_cost_cents'] is not None)/100,
        'today_profit_complete':all(r['unit_cost_cents'] is not None for r in today_sales),
        'total_cash_sales':all_sales['total']/100})


@app.get('/api/v2/products')
def v2_products():
    sid,_,_= _v2_context()
    return jsonify([product_json(r) for r in get_db().execute('SELECT * FROM products WHERE store_id=? ORDER BY name', (sid,)).fetchall()])


@app.post('/api/v2/products')
def v2_add_product():
    sid,_,_=_v2_require('owner','manager')
    d=request.get_json(silent=True) or {}
    pid=text_field(d,'id','Product ID',30)
    if not re.fullmatch(r'[\w.\-]+',pid):
        raise ApiError('Invalid product ID.')
    name=text_field(d,'name','Name',80)
    category=text_field(d,'category','Category',40,required=False)
    price=parse_money(d.get('price'),'Price',allow_zero=True)
    cost=parse_money(d.get('cost'),'Purchase cost',allow_zero=True) if str(d.get('cost','')).strip() else None
    qty=parse_int(d.get('quantity'),'Quantity')
    reorder=parse_int(d.get('reorder_level',10),'Reorder level')
    exp=text_field(d,'expiration','Expiration date',10,required=False) or None
    if exp:
        try: date.fromisoformat(exp)
        except ValueError: raise ApiError('Invalid expiration date.')
    try:
        with write_tx() as db:
            db.execute('INSERT INTO products (store_id,id,name,category,price_cents,cost_cents,quantity,reorder_level,expiration) VALUES (?,?,?,?,?,?,?,?,?)',
                (sid,pid,name,category,price,cost,qty,reorder,exp))
    except Exception as exc:
        if 'duplicate key' in str(exc).lower(): raise ApiError('Product ID already exists in your store.',409)
        raise
    return jsonify(product_json(_v2_product(get_db(),sid,pid))),201


@app.post('/api/v2/products/<pid>/restock')
def v2_restock(pid):
    sid,_,_=_v2_require('owner','manager')
    qty=parse_int((request.get_json(silent=True) or {}).get('quantity'),'Quantity',1)
    with write_tx() as db:
        result=db.execute('UPDATE products SET quantity=quantity+? WHERE store_id=? AND id=?', (qty,sid,pid))
        if result.rowcount != 1: raise ApiError('Product not found.',404)
    return jsonify(product_json(_v2_product(get_db(),sid,pid)))


@app.post('/api/v2/sales')
def v2_sale():
    sid,uid,_=_v2_require('owner','manager','cashier')
    d=request.get_json(silent=True) or {}
    qty=parse_int(d.get('quantity'),'Quantity',1)
    pid=str(d.get('product_id','')).strip()
    with write_tx() as db:
        product=_v2_product(db,sid,pid)
        result=db.execute('UPDATE products SET quantity=quantity-? WHERE store_id=? AND id=? AND quantity>=?',(qty,sid,pid,qty))
        if result.rowcount != 1: raise ApiError('Insufficient stock.',400)
        db.execute('INSERT INTO sales (store_id,product_id,product_name,quantity,unit_price_cents,unit_cost_cents,sold_at,username) VALUES (?,?,?,?,?,?,?,?)',
            (sid,pid,product['name'],qty,product['price_cents'],product['cost_cents'],now().isoformat(timespec='seconds'),uid))
    return jsonify({'ok':True,'total':qty*product['price_cents']/100})


@app.get('/api/v2/customers')
def v2_customers():
    sid,_,_=_v2_context()
    return jsonify([customer_json(r) for r in get_db().execute('SELECT * FROM customers WHERE store_id=? ORDER BY name',(sid,)).fetchall()])


@app.post('/api/v2/customers')
def v2_add_customer():
    sid,_,_=_v2_require('owner','manager')
    d=request.get_json(silent=True) or {}
    cid=text_field(d,'id','Customer ID',30)
    if not re.fullmatch(r'[\w.\-]+',cid): raise ApiError('Invalid customer ID.')
    name=text_field(d,'name','Customer name',80)
    contact=text_field(d,'contact','Contact',40,required=False)
    try:
        with write_tx() as db:
            db.execute('INSERT INTO customers(store_id,id,name,contact,balance_cents) VALUES (?,?,?,?,0)',(sid,cid,name,contact))
    except Exception as exc:
        if 'duplicate key' in str(exc).lower(): raise ApiError('Customer ID already exists in your store.',409)
        raise
    return jsonify(customer_json(_v2_customer(get_db(),sid,cid))),201


@app.post('/api/v2/customers/<cid>/charge')
def v2_charge(cid):
    sid,uid,_=_v2_require('owner','manager')
    d=request.get_json(silent=True) or {}
    pid=str(d.get('product_id','')).strip()
    qty=parse_int(d.get('quantity'),'Quantity',1)
    with write_tx() as db:
        customer=_v2_customer(db,sid,cid)
        product=_v2_product(db,sid,pid)
        result=db.execute('UPDATE products SET quantity=quantity-? WHERE store_id=? AND id=? AND quantity>=?',(qty,sid,pid,qty))
        if result.rowcount != 1: raise ApiError('Insufficient stock.',400)
        amount=qty*product['price_cents']
        db.execute('UPDATE customers SET balance_cents=balance_cents+? WHERE store_id=? AND id=?',(amount,sid,cid))
        db.execute('INSERT INTO credit_tx(store_id,customer_id,tx_type,amount_cents,description,tx_at,username) VALUES (?,?,?,?,?,?,?)',
            (sid,cid,'CHARGE',amount,f'{qty} x {product["name"]}',now().isoformat(timespec='seconds'),uid))
    return jsonify({'ok':True,'amount':amount/100})


@app.post('/api/v2/customers/<cid>/payment')
def v2_payment(cid):
    sid,uid,_=_v2_require('owner','manager')
    amount=parse_money((request.get_json(silent=True) or {}).get('amount'),'Payment')
    with write_tx() as db:
        _v2_customer(db,sid,cid)
        result=db.execute('UPDATE customers SET balance_cents=balance_cents-? WHERE store_id=? AND id=? AND balance_cents>=?',(amount,sid,cid,amount))
        if result.rowcount != 1: raise ApiError('Payment exceeds balance.',400)
        db.execute('INSERT INTO credit_tx(store_id,customer_id,tx_type,amount_cents,description,tx_at,username) VALUES (?,?,?,?,?,?,?)',
            (sid,cid,'PAYMENT',amount,'Payment received',now().isoformat(timespec='seconds'),uid))
    return jsonify({'ok':True,'amount':amount/100})


@app.get('/api/v2/customers/<cid>/ledger')
def v2_ledger(cid):
    sid,_,_=_v2_require('owner','manager')
    db=get_db()
    customer=_v2_customer(db,sid,cid)
    rows=db.execute('SELECT * FROM credit_tx WHERE store_id=? AND customer_id=? ORDER BY id DESC LIMIT 200',(sid,cid)).fetchall()
    return jsonify({'customer':customer_json(customer),'transactions':[{'type':r['tx_type'],'amount':r['amount_cents']/100,'description':r['description'],'at':r['tx_at']} for r in rows]})


@app.get('/api/v2/reports')
def v2_reports():
    sid,_,_=_v2_require('owner','manager')
    db=get_db()
    sales=db.execute('SELECT product_name,quantity,unit_price_cents,unit_cost_cents,sold_at FROM sales WHERE store_id=? ORDER BY id DESC LIMIT 500',(sid,)).fetchall()
    return jsonify({'recent_sales':[{'product':r['product_name'],'quantity':r['quantity'],'total':r['quantity']*r['unit_price_cents']/100,'at':r['sold_at']} for r in sales],
        'note':'Showing the latest 500 cash-sale records for this store.'})

init_db()

if __name__ == "__main__":
    # Development server only. For real use see README.md (a proper server + HTTPS).
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")))
