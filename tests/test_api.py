"""
API tests. Run from the project folder with:   python3 -m unittest discover -s tests -v
They use a temporary database, so your real data is never touched.
"""
import os
import sys
import tempfile
import threading
import unittest

_TMP = tempfile.mkdtemp(prefix="sari_test_")
os.environ["SARI_DB"] = os.path.join(_TMP, "test.db")
os.environ["SARI_SECRET_FILE"] = os.path.join(_TMP, "secret.key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as S  # noqa: E402

H = {"X-Requested-With": "sari-app"}


class Api(unittest.TestCase):
    def setUp(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(S.app.config["DB_PATH"] + suffix)
            except FileNotFoundError:
                pass
        S._FAILED.clear()
        S.init_db()

    # ---------- helpers ----------
    def client(self, user=None, pw=None):
        c = S.app.test_client()
        if user:
            r = c.post("/api/login", json={"username": user, "password": pw}, headers=H)
            self.assertEqual(r.status_code, 200, r.get_json())
        return c

    def owner(self):
        """An Owner who has already replaced the default password."""
        c = self.client("admin", "admin123")
        r = c.post("/api/change-password", json={"current_password": "admin123", "new_password": "OwnerPass1"}, headers=H)
        self.assertEqual(r.status_code, 200, r.get_json())
        return c

    def make_user(self, owner, name, role, pw="Temp1234"):
        r = owner.post("/api/users", json={"username": name, "password": pw, "role": role}, headers=H)
        self.assertEqual(r.status_code, 201, r.get_json())
        c = self.client(name, pw)
        r = c.post("/api/change-password", json={"current_password": pw, "new_password": pw + "x"}, headers=H)
        self.assertEqual(r.status_code, 200, r.get_json())
        return c

    def product(self, c, pid="P1", price="15.50", qty=10, reorder=3, **extra):
        r = c.post("/api/products", json={"id": pid, "name": "Milk " + pid, "category": "Dairy",
                                          "price": price, "quantity": qty, "reorder_level": reorder, **extra}, headers=H)
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()

    def post(self, c, url, body=None):
        return c.post(url, json=body or {}, headers=H)

    # ---------- login and first-run safety ----------
    def test_default_admin_must_change_password_before_anything_else(self):
        c = self.client("admin", "admin123")
        self.assertTrue(c.get("/api/me").get_json()["must_change"])
        r = c.get("/api/products")
        self.assertEqual((r.status_code, r.get_json()["code"]), (403, "must_change"))
        self.assertEqual(self.post(c, "/api/change-password", {"current_password": "bad", "new_password": "Whatever1"}).status_code, 403)
        self.assertEqual(self.post(c, "/api/change-password", {"current_password": "admin123", "new_password": "admin123"}).status_code, 400)
        self.assertEqual(self.post(c, "/api/change-password", {"current_password": "admin123", "new_password": "short"}).status_code, 400)
        self.assertEqual(self.post(c, "/api/change-password", {"current_password": "admin123", "new_password": "Whatever1"}).status_code, 200)
        self.assertEqual(c.get("/api/products").status_code, 200)

    def test_wrong_login_and_throttle(self):
        c = S.app.test_client()
        r = self.post(c, "/api/login", {"username": "admin", "password": "nope"})
        self.assertEqual((r.status_code, r.get_json()["error"]), (401, "Invalid username or password."))
        self.assertEqual(self.post(c, "/api/login", {"username": "ghost", "password": "x"}).status_code, 401)
        for _ in range(4):
            self.post(c, "/api/login", {"username": "admin", "password": "nope"})
        r = self.post(c, "/api/login", {"username": "admin", "password": "admin123"})  # correct, but locked
        self.assertEqual(r.status_code, 429)

    def test_login_is_case_insensitive_and_requires_login_for_data(self):
        self.assertEqual(S.app.test_client().get("/api/products").status_code, 401)
        c = S.app.test_client()
        self.assertEqual(self.post(c, "/api/login", {"username": "ADMIN", "password": "admin123"}).status_code, 200)

    def test_change_requests_need_the_csrf_header(self):
        c = S.app.test_client()
        r = c.post("/api/login", json={"username": "admin", "password": "admin123"})  # no header
        self.assertEqual(r.status_code, 400)

    # ---------- roles ----------
    def test_staff_permissions(self):
        owner = self.owner()
        staff = self.make_user(owner, "juan", "Staff")
        self.product(owner)
        me = staff.get("/api/me").get_json()
        self.assertEqual(me["role"], "Staff")
        self.assertNotIn("reports", me["permissions"])
        self.assertEqual(self.post(staff, "/api/products", {"id": "X", "name": "x", "price": 1, "quantity": 1}).status_code, 403)
        self.assertEqual(staff.get("/api/reports").status_code, 403)
        self.assertEqual(staff.get("/api/users").status_code, 403)
        self.assertEqual(self.post(staff, "/api/users", {"username": "evil", "password": "Passw0rd", "role": "Owner"}).status_code, 403)
        self.assertEqual(self.post(staff, "/api/products/P1/restock", {"quantity": 5}).status_code, 200)
        self.assertEqual(self.post(staff, "/api/sales", {"product_id": "P1", "quantity": 2}).status_code, 200)
        cust = self.post(staff, "/api/customers", {"name": "Aling Nena"}).get_json()
        self.assertEqual(self.post(staff, f"/api/customers/{cust['id']}/charge", {"product_id": "P1", "quantity": 1}).status_code, 200)

    def test_admin_cannot_touch_owner_accounts(self):
        owner = self.owner()
        admin = self.make_user(owner, "maria", "Admin")
        users = admin.get("/api/users").get_json()
        self.assertEqual(users["allowed_roles"], ["Admin", "Staff"])
        self.assertFalse(next(u for u in users["users"] if u["username"] == "admin")["editable"])
        self.assertEqual(self.post(admin, "/api/users", {"username": "boss", "password": "Passw0rd", "role": "Owner"}).status_code, 403)
        self.assertEqual(admin.patch("/api/users/admin", json={"password": "Hacked123"}, headers=H).status_code, 403)
        self.assertEqual(admin.patch("/api/users/admin", json={"role": "Staff"}, headers=H).status_code, 403)
        self.assertEqual(admin.delete("/api/users/admin", headers=H).status_code, 403)
        self.assertEqual(self.client("admin", "OwnerPass1").get("/api/me").status_code, 200)  # Owner password unchanged
        self.assertEqual(self.post(admin, "/api/users", {"username": "temp1", "password": "Passw0rd", "role": "Staff"}).status_code, 201)
        self.assertEqual(admin.delete("/api/users/temp1", headers=H).status_code, 200)

    def test_nobody_can_delete_or_demote_themselves(self):
        owner = self.owner()
        self.assertEqual(owner.delete("/api/users/admin", headers=H).status_code, 403)
        self.assertEqual(owner.patch("/api/users/admin", json={"role": "Staff"}, headers=H).status_code, 403)
        self.assertEqual(owner.patch("/api/users/admin", json={"password": "Another123"}, headers=H).status_code, 403)

    def test_username_rules(self):
        owner = self.owner()
        for bad in ("ab", "has space", "x" * 31, "semi;colon"):
            self.assertEqual(self.post(owner, "/api/users", {"username": bad, "password": "Passw0rd", "role": "Staff"}).status_code, 400, bad)
        self.assertEqual(self.post(owner, "/api/users", {"username": "juan", "password": "Passw0rd", "role": "Staff"}).status_code, 201)
        self.assertEqual(self.post(owner, "/api/users", {"username": "JUAN", "password": "Passw0rd", "role": "Staff"}).status_code, 409)
        self.assertEqual(self.post(owner, "/api/users", {"username": "weak", "password": "123", "role": "Staff"}).status_code, 400)
        self.assertEqual(self.post(owner, "/api/users", {"username": "norole", "password": "Passw0rd", "role": "God"}).status_code, 403)

    def test_password_reset_signs_the_user_out_and_forces_a_new_password(self):
        owner = self.owner()
        staff = self.make_user(owner, "juan", "Staff")
        self.assertEqual(staff.get("/api/products").status_code, 200)
        self.assertEqual(owner.patch("/api/users/juan", json={"password": "Reset1234"}, headers=H).status_code, 200)
        self.assertEqual(staff.get("/api/products").status_code, 401)  # old login no longer valid
        again = self.client("juan", "Reset1234")
        self.assertTrue(again.get("/api/me").get_json()["must_change"])

    def test_role_change_and_delete_take_effect_immediately(self):
        owner = self.owner()
        staff = self.make_user(owner, "juan", "Staff")
        self.assertEqual(owner.patch("/api/users/juan", json={"role": "Admin"}, headers=H).status_code, 200)
        self.assertEqual(staff.get("/api/products").status_code, 401)  # must sign in again with the new role
        c = self.client("juan", "Temp1234x")
        self.assertEqual(c.get("/api/me").get_json()["role"], "Admin")
        self.assertEqual(owner.delete("/api/users/juan", headers=H).status_code, 200)
        self.assertEqual(c.get("/api/products").status_code, 401)

    # ---------- products and cash sales ----------
    def test_product_validation_and_status(self):
        o = self.owner()
        self.product(o, "A1", reorder=3, qty=3)  # at the reorder level -> low
        self.product(o, "A2", qty=50)
        self.product(o, "A3", expiration="2020-01-01")
        status = {p["id"]: p["status"] for p in o.get("/api/products").get_json()}
        self.assertEqual(status, {"A1": "LOW STOCK", "A2": "OK", "A3": "EXPIRED"})
        base = {"id": "Z", "name": "z", "price": 1, "quantity": 1}
        self.assertEqual(self.post(o, "/api/products", {**base, "id": "A1"}).status_code, 409)
        for bad in ({"id": ""}, {"id": "has space"}, {"name": ""}, {"price": "abc"}, {"price": -1}, {"price": True},
                    {"quantity": 1.5}, {"quantity": -1}, {"quantity": "x"}, {"expiration": "2026-13-45"},
                    {"expiration": "01/01/2030"}, {"price": "99999999999"}):
            self.assertEqual(self.post(o, "/api/products", {**base, **bad}).status_code, 400, bad)
        self.assertEqual(self.post(o, "/api/products", {**base, "id": "Z", "price": "0"}).status_code, 201)  # free item ok

    def test_cash_sale_and_stock_rules(self):
        o = self.owner()
        self.product(o, "P1", price="15.55", qty=10)
        r = self.post(o, "/api/sales", {"product_id": "P1", "quantity": 4}).get_json()
        self.assertEqual(r["total"], 62.2)
        self.assertEqual(o.get("/api/products").get_json()[0]["quantity"], 6)
        for q in (0, -1, 1.5, "abc", None):
            self.assertEqual(self.post(o, "/api/sales", {"product_id": "P1", "quantity": q}).status_code, 400, q)
        r = self.post(o, "/api/sales", {"product_id": "P1", "quantity": 7})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(o.get("/api/products").get_json()[0]["quantity"], 6)  # unchanged after a failed sale
        self.assertEqual(self.post(o, "/api/sales", {"product_id": "NOPE", "quantity": 1}).status_code, 404)
        self.assertEqual(self.post(o, "/api/products/P1/restock", {"quantity": 0}).status_code, 400)
        self.assertEqual(self.post(o, "/api/products/P1/restock", {"quantity": 4}).get_json()["quantity"], 10)

    def test_two_phones_cannot_sell_the_last_item_twice(self):
        o = self.owner()
        self.product(o, "P1", qty=1)
        results = []
        def sell():
            c = self.client("admin", "OwnerPass1")
            results.append(self.post(c, "/api/sales", {"product_id": "P1", "quantity": 1}).status_code)
        threads = [threading.Thread(target=sell) for _ in range(4)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(sorted(results), [200, 400, 400, 400])
        self.assertEqual(o.get("/api/products").get_json()[0]["quantity"], 0)

    # ---------- customer credit ----------
    def test_credit_charge_payment_and_exact_money(self):
        o = self.owner()
        self.product(o, "P1", price="0.10", qty=100)
        self.product(o, "P2", price="15.50", qty=20)
        c1 = self.post(o, "/api/customers", {"name": "Aling Nena", "contact": "0917 123 4567"}).get_json()
        c2 = self.post(o, "/api/customers", {"name": "Mang Tomas"}).get_json()
        self.assertEqual((c1["id"], c2["id"]), ("C001", "C002"))
        self.assertEqual(c1["contact"], "0917 123 4567")  # leading zero kept
        self.assertEqual(self.post(o, "/api/customers", {"name": " "}).status_code, 400)
        r = self.post(o, "/api/customers/C001/charge", {"product_id": "P1", "quantity": 3}).get_json()
        self.assertEqual(r["balance"], 0.30)  # 3 x 0.10 is exactly 0.30, not 0.30000000000000004
        r = self.post(o, "/api/customers/C001/charge", {"product_id": "P2", "quantity": 4}).get_json()
        self.assertEqual(r["balance"], 62.30)
        stock = {p["id"]: p["quantity"] for p in o.get("/api/products").get_json()}
        self.assertEqual(stock, {"P1": 97, "P2": 16})
        too_many = self.post(o, "/api/customers/C001/charge", {"product_id": "P2", "quantity": 99})
        self.assertEqual(too_many.status_code, 400)
        self.assertEqual(o.get("/api/customers").get_json()[0]["balance"], 62.30)  # unchanged after failure
        self.assertEqual({p["id"]: p["quantity"] for p in o.get("/api/products").get_json()}, stock)
        self.assertEqual(self.post(o, "/api/customers/C001/payment", {"amount": "20"}).get_json()["balance"], 42.30)
        self.assertEqual(self.post(o, "/api/customers/C001/payment", {"amount": 1000}).status_code, 400)
        for bad in (0, -5, "abc", True, None):
            self.assertEqual(self.post(o, "/api/customers/C001/payment", {"amount": bad}).status_code, 400, bad)
        self.assertEqual(self.post(o, "/api/customers/C001/payment", {"amount": "42.30"}).get_json()["balance"], 0)
        self.assertEqual(self.post(o, "/api/customers/C404/payment", {"amount": 1}).status_code, 404)
        self.assertEqual(self.post(o, "/api/customers/C404/charge", {"product_id": "P1", "quantity": 1}).status_code, 404)
        led = o.get("/api/customers/C001/ledger").get_json()
        self.assertEqual([(t["type"], t["amount"]) for t in led["transactions"]],
                         [("PAYMENT", 42.3), ("PAYMENT", 20.0), ("CHARGE", 62.0), ("CHARGE", 0.3)])

    # ---------- reports ----------
    def test_reports(self):
        o = self.owner()
        self.product(o, "P1", price="10", qty=20, reorder=5)
        self.product(o, "P2", price="7", qty=5, reorder=5)
        self.product(o, "P3", price="3", qty=9, expiration="2020-01-01")
        self.post(o, "/api/sales", {"product_id": "P1", "quantity": 3})
        self.post(o, "/api/sales", {"product_id": "P1", "quantity": 2})
        self.post(o, "/api/sales", {"product_id": "P3", "quantity": 1})
        self.post(o, "/api/customers", {"name": "Aling Nena"})
        self.post(o, "/api/customers/C001/charge", {"product_id": "P2", "quantity": 2})
        self.post(o, "/api/customers/C001/payment", {"amount": 4})
        r = o.get("/api/reports").get_json()
        self.assertEqual((r["cash_sales_total"], r["cash_sales_today"]), (53.0, 53.0))
        self.assertEqual((r["credit_charged"], r["credit_paid"], r["outstanding"]), (14.0, 4.0, 10.0))
        self.assertEqual(r["best_sellers"][0], {"name": "Milk P1", "quantity": 5})
        self.assertEqual([c["name"] for c in r["customers_with_balance"]], ["Aling Nena"])
        self.assertEqual([p["id"] for p in r["low_stock"]], ["P2"])  # 5 - 2 = 3 <= 5
        self.assertEqual([p["id"] for p in r["expired"]], ["P3"])

    # ---------- updates ----------
    def test_database_upgrades_keep_existing_data(self):
        o = self.owner()
        self.product(o, "P1", qty=7)
        S.MIGRATIONS.append("ALTER TABLE products ADD COLUMN note TEXT NOT NULL DEFAULT '';")
        try:
            S.init_db()
            db = S.connect()
            cols = [r["name"] for r in db.execute("PRAGMA table_info(products)")]
            self.assertIn("note", cols)
            self.assertEqual(db.execute("SELECT quantity FROM products WHERE id='P1'").fetchone()[0], 7)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], len(S.MIGRATIONS))
            db.close()
        finally:
            S.MIGRATIONS.pop()

    def test_app_files_carry_the_version(self):
        c = S.app.test_client()
        page = c.get("/")
        self.assertEqual(page.status_code, 200)
        text = page.get_data(as_text=True)
        self.assertNotIn("__VERSION__", text)
        self.assertIn(f'data-version="{S.APP_VERSION}"', text)
        sw = c.get("/sw.js")
        self.assertIn(S.APP_VERSION, sw.get_data(as_text=True))
        self.assertEqual(sw.headers["Service-Worker-Allowed"], "/")
        self.assertEqual(c.get("/manifest.webmanifest").get_json()["display"], "standalone")
        self.assertEqual(c.get("/api/version").get_json()["version"], S.APP_VERSION)
        self.assertEqual(page.headers["X-App-Version"], S.APP_VERSION)
        self.assertIn("default-src 'self'", page.headers["Content-Security-Policy"])
        self.assertEqual(c.get("/api/products").headers["Cache-Control"], "no-store")
        self.assertEqual(c.get("/api/does-not-exist").status_code, 404)

    def test_database_file_is_not_downloadable(self):
        c = S.app.test_client()
        for path in ("/static/../sari_sari.db", "/sari_sari.db", "/secret.key", "/static/../secret.key", "/app.py"):
            self.assertEqual(c.get(path).status_code, 404, path)


if __name__ == "__main__":
    unittest.main()
