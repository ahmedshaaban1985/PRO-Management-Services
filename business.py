# -*- coding: utf-8 -*-
"""شركات العملاء (تجارة وأنشطة عامة) - bookkeeping for client companies.

A company whose setting company_kind = 'business' gets, in its own database:
  * items (أصناف) - stock goods or services, with moving-average cost
  * suppliers (موردين), purchase bills (فواتير مشتريات) and supplier payments
  * sales invoices (فواتير بيع) with VAT and customer receipts (سندات قبض)
  * stock adjustments (أرصدة أول المدة / جرد / تالف)
  * company documents (ملفات الشركة: سجل، رخصة، عقود...) with expiry dates

Journal entries (Dr / Cr):
  Purchase bill  : Dr inventory (stock lines) / Dr expense account (other lines) / Dr VAT input
                   Cr accounts payable (supplier)
  Supplier pay   : Dr accounts payable (supplier) / Cr cash or bank
  Sales invoice  : Dr receivables (client) / Cr sales / Cr VAT output
                   + Dr cost of goods sold / Cr inventory  (moving-average cost)
  Receipt        : Dr cash or bank / Cr receivables (client)
  Adjustment +   : Dr inventory / Cr chosen account (opening balances by default)
  Adjustment -   : Dr stock shortage / Cr inventory
"""
import datetime as dt
import json
import shutil
from pathlib import Path

from accounting import r2

KINDS = {"clearing": ("شركة خدمات وتخليص معاملات", "Services & transaction clearing"),
         "business": ("شركة عميل (تجارة وأنشطة عامة)", "Client company (trading & general)")}

# accounts a client company needs on top of the default chart
# code, arabic, english, type, parent code, is_group, is_money, sys_key
BUSINESS_ACCOUNTS = [
    ("1107", "مخزون البضاعة", "Inventory", "asset", "11", 0, 0, "inventory"),
    ("1108", "ضريبة القيمة المضافة - مدخلات", "VAT Input (recoverable)", "asset", "11", 0, 0, "vat_in"),
    ("2105", "ضريبة القيمة المضافة - مخرجات", "VAT Output (payable)", "liability", "21", 0, 0, "vat_out"),
    ("4201", "المبيعات", "Sales", "income", "4", 0, 0, "sales"),
    ("5201", "تكلفة البضاعة المباعة", "Cost of Goods Sold", "expense", "5", 0, 0, "cogs"),
    ("5202", "عجز وتالف المخزون", "Stock Shortage & Damage", "expense", "5", 0, 0, "shrink"),
]

PREFIXES = {"items": "ITM", "suppliers": "SUP", "purchase_bills": "PUR",
            "supplier_payments": "SPY", "sales_invoices": "INV", "sales_receipts": "RCV",
            "stock_adjustments": "ADJ"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, name TEXT NOT NULL,
    unit TEXT DEFAULT '', kind TEXT DEFAULT 'stock', sale_price REAL DEFAULT 0,
    purchase_price REAL DEFAULT 0, reorder_level REAL DEFAULT 0, barcode TEXT DEFAULT '',
    account_id INTEGER, active INTEGER DEFAULT 1, notes TEXT DEFAULT '', created TEXT
);
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, name TEXT NOT NULL,
    phone TEXT DEFAULT '', email TEXT DEFAULT '', vat_number TEXT DEFAULT '',
    cr_number TEXT DEFAULT '', address TEXT DEFAULT '', contact_person TEXT DEFAULT '',
    active INTEGER DEFAULT 1, notes TEXT DEFAULT '', created TEXT
);
CREATE TABLE IF NOT EXISTS purchase_bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    date TEXT NOT NULL, due_date TEXT DEFAULT '', supplier_ref TEXT DEFAULT '',
    lines TEXT DEFAULT '[]', discount REAL DEFAULT 0, vat_rate REAL DEFAULT 0,
    notes TEXT DEFAULT '', status TEXT DEFAULT 'posted', created TEXT
);
CREATE TABLE IF NOT EXISTS supplier_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    bill_id INTEGER REFERENCES purchase_bills(id) ON DELETE SET NULL,
    date TEXT NOT NULL, amount REAL DEFAULT 0, method TEXT DEFAULT '',
    paid_from_id INTEGER, note TEXT DEFAULT '', created TEXT
);
CREATE TABLE IF NOT EXISTS sales_invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    date TEXT NOT NULL, due_date TEXT DEFAULT '',
    lines TEXT DEFAULT '[]', discount REAL DEFAULT 0, vat_rate REAL DEFAULT 0,
    notes TEXT DEFAULT '', status TEXT DEFAULT 'posted', created TEXT
);
CREATE TABLE IF NOT EXISTS sales_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    invoice_id INTEGER REFERENCES sales_invoices(id) ON DELETE SET NULL,
    date TEXT NOT NULL, amount REAL DEFAULT 0, method TEXT DEFAULT '',
    account_id INTEGER, note TEXT DEFAULT '', created TEXT
);
CREATE TABLE IF NOT EXISTS stock_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    date TEXT NOT NULL, qty REAL DEFAULT 0, unit_cost REAL DEFAULT 0,
    reason TEXT DEFAULT 'opening', account_id INTEGER, note TEXT DEFAULT '', created TEXT
);
CREATE TABLE IF NOT EXISTS company_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
    doc_number TEXT DEFAULT '', issue_date TEXT DEFAULT '', expiry TEXT DEFAULT '',
    path TEXT DEFAULT '', original_name TEXT DEFAULT '', notes TEXT DEFAULT '', created TEXT
);
CREATE INDEX IF NOT EXISTS idx_pb_supplier ON purchase_bills(supplier_id);
CREATE INDEX IF NOT EXISTS idx_si_client ON sales_invoices(client_id);
CREATE INDEX IF NOT EXISTS idx_sr_invoice ON sales_receipts(invoice_id);
CREATE INDEX IF NOT EXISTS idx_sp_bill ON supplier_payments(bill_id);
CREATE INDEX IF NOT EXISTS idx_jl_supplier ON jlines(supplier_id);
"""

ADJ_REASONS = [("opening", "رصيد أول المدة", "Opening stock"),
               ("count", "تسوية جرد", "Stock count adjustment"),
               ("damage", "تالف / هالك", "Damaged / expired")]

DOC_TITLES = ["السجل التجاري|Commercial Registration", "الرخصة التجارية|Trade License",
              "بطاقة المنشأة|Establishment Card", "شهادة التسجيل الضريبي|VAT Certificate",
              "عقد الإيجار|Lease Contract", "عقد التأسيس|Articles of Association",
              "الدفاع المدني|Civil Defence", "شهادة البلدية|Municipality Certificate", "أخرى|Other"]


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def doc_lines(row):
    try:
        return json.loads(row["lines"] or "[]")
    except (TypeError, ValueError):
        return []


def doc_totals(lines, discount=0, vat_rate=0):
    """(subtotal, discount, net, vat, total) of a sales or purchase document."""
    sub = r2(sum(_num(l.get("qty")) * _num(l.get("price")) for l in lines))
    disc = r2(min(_num(discount), sub)) if sub > 0 else r2(_num(discount))
    net = r2(sub - disc)
    vat = r2(net * _num(vat_rate) / 100.0)
    return sub, disc, net, vat, r2(net + vat)


class BusinessMixin:
    """Mixed into core.DB."""

    # ---------- setup ----------
    @property
    def company_kind(self):
        r = self.con.execute("SELECT value FROM settings WHERE key='company_kind'").fetchone()
        return r[0] if r and r[0] in KINDS else "clearing"

    @property
    def is_business(self):
        return self.company_kind == "business"

    def _business_setup(self):
        """Tables are created in every database (harmless); accounts only for client companies."""
        self.con.executescript(SCHEMA)
        if self.is_business:
            self._business_accounts()
        self.con.commit()

    def _business_accounts(self):
        have = {r[0]: r[1] for r in self.con.execute("SELECT code, id FROM accounts")}
        for code, ar, en, typ, parent, grp, money, key in BUSINESS_ACCOUNTS:
            if code in have:
                # make sure the system key is there (needed for posting)
                self.con.execute("UPDATE accounts SET sys_key=? WHERE code=? AND COALESCE(sys_key,'')=''",
                                 (key, code))
                continue
            if self.sys_account(key):
                continue
            self.con.execute(
                "INSERT INTO accounts(code, name_ar, name_en, type, parent_id, is_group, is_money, sys_key) "
                "VALUES(?,?,?,?,?,?,?,?)", (code, ar, en, typ, have.get(parent), grp, money, key))

    def set_company_kind(self, kind):
        if kind not in KINDS:
            raise ValueError("kind")
        self.set("company_kind", kind)
        if kind == "business":
            self._business_accounts()
            self._commit()

    def vat_rate(self):
        return _num(self.get("vat_rate"))

    # ---------- items ----------
    def items(self, search="", active_only=False):
        s = f"%{search.strip()}%"
        sql = "SELECT * FROM items WHERE (name LIKE ? OR code LIKE ? OR barcode LIKE ?)"
        if active_only:
            sql += " AND active=1"
        return self.q(sql + " ORDER BY name", (s, s, s))

    def item(self, iid):
        return self.one("SELECT * FROM items WHERE id=?", (iid,))

    def save_item(self, data, iid=None):
        if not (data.get("name") or "").strip():
            raise ValueError("item_name")
        d = dict(data)
        d["account_id"] = d.get("account_id") or None
        fields = ("name", "unit", "kind", "sale_price", "purchase_price", "reorder_level",
                  "barcode", "account_id", "active", "notes")
        vals = [d.get(f) if f == "account_id" else (d.get(f) if d.get(f) is not None else "")
                for f in fields]
        if iid:
            old = self.item(iid)
            if old and old["kind"] != d.get("kind") and self._item_used(iid):
                raise ValueError("item_kind_used")
            self.x(f"UPDATE items SET {', '.join(f + '=?' for f in fields)} WHERE id=?", (*vals, iid))
            return iid
        return self.x(f"INSERT INTO items({', '.join(fields)}, code, created) "
                      f"VALUES({', '.join('?' * len(fields))}, ?, ?)",
                      (*vals, self.next_code("items"), _now()))

    def _item_used(self, iid):
        if self.one("SELECT 1 FROM stock_adjustments WHERE item_id=? LIMIT 1", (iid,)):
            return True
        for t in ("purchase_bills", "sales_invoices"):
            for r in self.q(f"SELECT lines FROM {t}"):
                if any(l.get("item_id") == iid for l in doc_lines(r)):
                    return True
        return False

    def delete_item(self, iid):
        if self._item_used(iid):
            raise ValueError("item_used")
        self.x("DELETE FROM items WHERE id=?", (iid,))

    # ---------- stock: moving average, computed from the documents ----------
    def _stock_events(self, date_to=None):
        """Every stock movement in time order: (date, order, kind, doc_id, item_id, qty, unit_cost)."""
        stock = {r["id"] for r in self.q("SELECT id FROM items WHERE kind='stock'")}
        ev = []
        for a in self.q("SELECT * FROM stock_adjustments"):
            if a["item_id"] in stock:
                ev.append((a["date"], 0 if a["qty"] >= 0 else 2, "adj", a["id"], a["item_id"],
                           _num(a["qty"]), _num(a["unit_cost"])))
        for b in self.q("SELECT * FROM purchase_bills WHERE status<>'cancelled'"):
            lines = doc_lines(b)
            sub = sum(_num(l.get("qty")) * _num(l.get("price")) for l in lines)
            factor = (sub - _num(b["discount"])) / sub if sub > 0 else 1.0
            for l in lines:
                if l.get("item_id") in stock and _num(l.get("qty")):
                    ev.append((b["date"], 1, "bill", b["id"], l["item_id"], _num(l.get("qty")),
                               _num(l.get("price")) * factor))
        for s in self.q("SELECT * FROM sales_invoices WHERE status<>'cancelled'"):
            for l in doc_lines(s):
                if l.get("item_id") in stock and _num(l.get("qty")):
                    ev.append((s["date"], 2, "sale", s["id"], l["item_id"], -_num(l.get("qty")), 0.0))
        if date_to:
            ev = [e for e in ev if e[0] <= date_to]
        ev.sort(key=lambda e: (e[0], e[1], e[3]))
        return ev

    def stock_run(self, date_to=None):
        """Walk the movements. Returns (state per item {qty, avg, value}, cost of each out-movement
        {(kind, doc_id): cost}, card per item [rows])."""
        state, costs, cards = {}, {}, {}
        for date, _o, kind, doc, item, qty, ucost in self._stock_events(date_to):
            st = state.setdefault(item, {"qty": 0.0, "avg": 0.0, "value": 0.0})
            if qty > 0:
                new_qty = st["qty"] + qty
                st["value"] = r2(st["value"] + qty * ucost) if st["qty"] >= 0 else r2(new_qty * ucost)
                st["qty"] = new_qty
                st["avg"] = st["value"] / new_qty if new_qty > 0 else ucost
                cost = r2(qty * ucost)
                if kind == "adj":
                    costs[(kind, doc)] = costs.get((kind, doc), 0) + cost
            else:
                out = -qty
                avg = st["avg"] if st["avg"] else (self._last_price(item) or 0.0)
                cost = r2(out * avg)
                st["qty"] -= out
                st["value"] = r2(st["value"] - cost) if st["qty"] > 1e-9 else (
                    0.0 if abs(st["qty"]) <= 1e-9 else r2(st["qty"] * avg))
                st["avg"] = avg
                costs[(kind, doc)] = r2(costs.get((kind, doc), 0) + cost)
            cards.setdefault(item, []).append({
                "date": date, "kind": kind, "doc_id": doc, "qty_in": qty if qty > 0 else 0,
                "qty_out": -qty if qty < 0 else 0, "cost": cost, "balance": st["qty"],
                "avg": st["avg"], "value": st["value"]})
        return state, costs, cards

    def _last_price(self, item_id):
        it = self.item(item_id)
        return _num(it["purchase_price"]) if it else 0.0

    def stock_on_hand(self, item_id, date_to=None, exclude_sale=None):
        """Quantity in stock (optionally ignoring one sales invoice that is being edited)."""
        qty = 0.0
        for e in self._stock_events(date_to):
            if e[4] == item_id and not (exclude_sale and e[2] == "sale" and e[3] == exclude_sale):
                qty += e[5]
        return qty

    def inventory_valuation(self, date_to=None):
        state, _, _ = self.stock_run(date_to)
        out = []
        for it in self.q("SELECT * FROM items WHERE kind='stock' ORDER BY name"):
            st = state.get(it["id"], {"qty": 0.0, "avg": 0.0, "value": 0.0})
            out.append({"item": it, "qty": st["qty"], "avg": st["avg"], "value": st["value"],
                        "low": it["reorder_level"] and st["qty"] <= it["reorder_level"]})
        return out

    def item_card(self, item_id, date_to=None):
        _, _, cards = self.stock_run(date_to)
        rows = cards.get(item_id, [])
        for r in rows:
            r["ref"] = self._doc_ref(r["kind"], r["doc_id"])
        return rows

    def _doc_ref(self, kind, doc_id):
        t = {"adj": "stock_adjustments", "bill": "purchase_bills", "sale": "sales_invoices"}[kind]
        r = self.one(f"SELECT code FROM {t} WHERE id=?", (doc_id,))
        return r["code"] if r else ""

    def low_stock(self):
        return [v for v in self.inventory_valuation() if v["low"]]

    # ---------- suppliers ----------
    def suppliers(self, search="", active_only=False):
        s = f"%{search.strip()}%"
        sql = ("SELECT * FROM suppliers WHERE (name LIKE ? OR code LIKE ? OR phone LIKE ? "
               "OR vat_number LIKE ?)")
        if active_only:
            sql += " AND active=1"
        return self.q(sql + " ORDER BY name", (s, s, s, s))

    def supplier(self, sid):
        return self.one("SELECT * FROM suppliers WHERE id=?", (sid,))

    def save_supplier(self, data, sid=None):
        if not (data.get("name") or "").strip():
            raise ValueError("supplier_name")
        fields = ("name", "phone", "email", "vat_number", "cr_number", "address",
                  "contact_person", "active", "notes")
        vals = [data.get(f) if data.get(f) is not None else "" for f in fields]
        if sid:
            self.x(f"UPDATE suppliers SET {', '.join(f + '=?' for f in fields)} WHERE id=?", (*vals, sid))
            return sid
        return self.x(f"INSERT INTO suppliers({', '.join(fields)}, code, created) "
                      f"VALUES({', '.join('?' * len(fields))}, ?, ?)",
                      (*vals, self.next_code("suppliers"), _now()))

    def delete_supplier(self, sid):
        if self.one("SELECT 1 FROM purchase_bills WHERE supplier_id=? LIMIT 1", (sid,)) or \
                self.one("SELECT 1 FROM supplier_payments WHERE supplier_id=? LIMIT 1", (sid,)):
            raise ValueError("supplier_used")
        self.x("DELETE FROM suppliers WHERE id=?", (sid,))

    def ap_balances(self):
        """What we owe each supplier (positive = we owe)."""
        return {r["supplier_id"]: r2(r["b"]) for r in self.q(
            """SELECT l.supplier_id, SUM(l.credit - l.debit) b FROM jlines l
               WHERE l.account_id=? AND l.supplier_id IS NOT NULL GROUP BY l.supplier_id""",
            (self.sys_account("ap"),))}

    def supplier_ledger(self, sid):
        rows, bal = [], 0.0
        for r in self.q("""SELECT j.date, j.code AS entry_code, j.ref, j.memo, l.debit, l.credit
                           FROM jlines l JOIN journal j ON j.id=l.entry_id
                           WHERE l.account_id=? AND l.supplier_id=? ORDER BY j.date, j.id""",
                        (self.sys_account("ap"), sid)):
            bal = r2(bal + r["credit"] - r["debit"])
            rows.append({"date": r["date"], "ref": r["ref"] or r["entry_code"], "desc": r["memo"],
                         "debit": r["debit"], "credit": r["credit"], "balance": bal})
        return rows

    # ---------- purchase bills ----------
    def purchase_bills(self, search="", date_from=None, date_to=None, supplier_id=None):
        s = f"%{search.strip()}%"
        sql = """SELECT b.*, s.name AS supplier_name FROM purchase_bills b
                 JOIN suppliers s ON s.id=b.supplier_id
                 WHERE (b.code LIKE ? OR s.name LIKE ? OR b.supplier_ref LIKE ?)"""
        args = [s, s, s]
        if date_from:
            sql += " AND b.date>=?"
            args.append(date_from)
        if date_to:
            sql += " AND b.date<=?"
            args.append(date_to)
        if supplier_id:
            sql += " AND b.supplier_id=?"
            args.append(supplier_id)
        return self.q(sql + " ORDER BY b.date DESC, b.id DESC", args)

    def purchase_bill(self, bid):
        return self.one("""SELECT b.*, s.name AS supplier_name FROM purchase_bills b
                           JOIN suppliers s ON s.id=b.supplier_id WHERE b.id=?""", (bid,))

    def bill_totals(self, b):
        return doc_totals(doc_lines(b), b["discount"], b["vat_rate"])

    def bill_paid(self, bid):
        r = self.one("SELECT COALESCE(SUM(amount),0) s FROM supplier_payments WHERE bill_id=?", (bid,))
        return r2(r["s"])

    def save_purchase_bill(self, data, bid=None):
        d = dict(data)
        if not d.get("supplier_id"):
            raise ValueError("supplier_missing")
        lines = [l for l in (d.get("lines") or []) if _num(l.get("qty")) or _num(l.get("price"))]
        if not lines:
            raise ValueError("lines_missing")
        d["lines"] = json.dumps(lines, ensure_ascii=False)
        fields = ("supplier_id", "date", "due_date", "supplier_ref", "lines", "discount",
                  "vat_rate", "notes", "status")
        d.setdefault("status", "posted")
        vals = [d.get(f) if d.get(f) is not None else "" for f in fields]
        if bid:
            old = self.purchase_bill(bid)
            self._check_lock(old["date"] if old else None, d.get("date"))
            self.x(f"UPDATE purchase_bills SET {', '.join(f + '=?' for f in fields)} WHERE id=?",
                   (*vals, bid))
        else:
            self._check_lock(d.get("date"))
            bid = self.x(f"INSERT INTO purchase_bills({', '.join(fields)}, code, created) "
                         f"VALUES({', '.join('?' * len(fields))}, ?, ?)",
                         (*vals, self.next_code("purchase_bills"), _now()))
        self.post_bill(bid, commit=False)
        self.repost_stock_costs()
        return bid

    def delete_purchase_bill(self, bid):
        if self.one("SELECT 1 FROM supplier_payments WHERE bill_id=? LIMIT 1", (bid,)):
            raise ValueError("bill_paid")
        self._clear_source("bill", bid)
        self.x("DELETE FROM purchase_bills WHERE id=?", (bid,))
        self.repost_stock_costs()

    def post_bill(self, bid, commit=True):
        self._post("bill", bid, lambda: self._build_bill(bid), commit)

    def _build_bill(self, bid):
        b = self.one("SELECT * FROM purchase_bills WHERE id=?", (bid,))
        if not b or b["status"] == "cancelled":
            return
        lines = doc_lines(b)
        sub, disc, net, vat, total = doc_totals(lines, b["discount"], b["vat_rate"])
        if not total:
            return
        factor = net / sub if sub else 1.0
        stock = {r["id"] for r in self.q("SELECT id FROM items WHERE kind='stock'")}
        debits = {}
        for l in lines:
            amount = _num(l.get("qty")) * _num(l.get("price")) * factor
            if l.get("item_id") in stock:
                acc = self.sys_account("inventory")
            else:
                acc = l.get("account_id")
                if not acc and l.get("item_id"):
                    it = self.item(l["item_id"])
                    acc = it["account_id"] if it else None
                acc = acc or self.sys_account("other_exp")
            debits[acc] = debits.get(acc, 0.0) + amount
        entry = [{"account_id": a, "debit": r2(v)} for a, v in debits.items()]
        # rounding: make the debits add up exactly to the net
        diff = r2(net - sum(r2(v) for v in debits.values()))
        if diff and entry:
            entry[0]["debit"] = r2(entry[0]["debit"] + diff)
        if vat:
            entry.append({"account_id": self.sys_account("vat_in"), "debit": vat})
        sup = self.supplier(b["supplier_id"])
        entry.append({"account_id": self.sys_account("ap"), "credit": total,
                      "supplier_id": b["supplier_id"]})
        memo = f"فاتورة مشتريات {b['code']} - {sup['name'] if sup else ''}" + \
               (f" (فاتورة المورد {b['supplier_ref']})" if b["supplier_ref"] else "")
        self._new_entry(b["date"], memo, entry, "bill", bid, b["code"])

    # ---------- supplier payments ----------
    def supplier_payments(self, search="", date_from=None, date_to=None, supplier_id=None):
        s = f"%{search.strip()}%"
        sql = """SELECT p.*, s.name AS supplier_name, b.code AS bill_code FROM supplier_payments p
                 JOIN suppliers s ON s.id=p.supplier_id
                 LEFT JOIN purchase_bills b ON b.id=p.bill_id
                 WHERE (p.code LIKE ? OR s.name LIKE ? OR p.note LIKE ?)"""
        args = [s, s, s]
        if date_from:
            sql += " AND p.date>=?"
            args.append(date_from)
        if date_to:
            sql += " AND p.date<=?"
            args.append(date_to)
        if supplier_id:
            sql += " AND p.supplier_id=?"
            args.append(supplier_id)
        return self.q(sql + " ORDER BY p.date DESC, p.id DESC", args)

    def supplier_payment(self, pid):
        return self.one("""SELECT p.*, s.name AS supplier_name, b.code AS bill_code
                           FROM supplier_payments p JOIN suppliers s ON s.id=p.supplier_id
                           LEFT JOIN purchase_bills b ON b.id=p.bill_id WHERE p.id=?""", (pid,))

    def save_supplier_payment(self, data, pid=None):
        d = dict(data)
        if not d.get("supplier_id") or _num(d.get("amount")) <= 0:
            raise ValueError("payment_missing")
        d["bill_id"] = d.get("bill_id") or None
        d["paid_from_id"] = d.get("paid_from_id") or self.money_account_for_method(d.get("method"))
        fields = ("supplier_id", "bill_id", "date", "amount", "method", "paid_from_id", "note")
        vals = [d.get(f) if f in ("bill_id",) else (d.get(f) if d.get(f) is not None else "")
                for f in fields]
        if pid:
            self.x(f"UPDATE supplier_payments SET {', '.join(f + '=?' for f in fields)} WHERE id=?",
                   (*vals, pid))
        else:
            pid = self.x(f"INSERT INTO supplier_payments({', '.join(fields)}, code, created) "
                         f"VALUES({', '.join('?' * len(fields))}, ?, ?)",
                         (*vals, self.next_code("supplier_payments"), _now()))
        self.post_spay(pid)
        return pid

    def delete_supplier_payment(self, pid):
        self._clear_source("spay", pid)
        self.x("DELETE FROM supplier_payments WHERE id=?", (pid,))

    def post_spay(self, pid, commit=True):
        self._post("spay", pid, lambda: self._build_spay(pid), commit)

    def _build_spay(self, pid):
        p = self.one("SELECT * FROM supplier_payments WHERE id=?", (pid,))
        if not p or not r2(p["amount"]):
            return
        sup = self.supplier(p["supplier_id"])
        memo = f"سداد مورد {p['code']} - {sup['name'] if sup else ''}" + (f" - {p['note']}" if p["note"] else "")
        self._new_entry(p["date"], memo, [
            {"account_id": self.sys_account("ap"), "debit": r2(p["amount"]), "supplier_id": p["supplier_id"]},
            {"account_id": p["paid_from_id"] or self.sys_account("cash"), "credit": r2(p["amount"])}],
            "spay", pid, p["code"])

    # ---------- sales invoices ----------
    def sales_invoices(self, search="", date_from=None, date_to=None, client_id=None):
        s = f"%{search.strip()}%"
        sql = """SELECT i.*, c.name AS client_name, c.code AS client_code FROM sales_invoices i
                 JOIN clients c ON c.id=i.client_id
                 WHERE (i.code LIKE ? OR c.name LIKE ? OR c.code LIKE ?)"""
        args = [s, s, s]
        if date_from:
            sql += " AND i.date>=?"
            args.append(date_from)
        if date_to:
            sql += " AND i.date<=?"
            args.append(date_to)
        if client_id:
            sql += " AND i.client_id=?"
            args.append(client_id)
        return self.q(sql + " ORDER BY i.date DESC, i.id DESC", args)

    def sales_invoice(self, sid):
        return self.one("""SELECT i.*, c.name AS client_name, c.code AS client_code
                           FROM sales_invoices i JOIN clients c ON c.id=i.client_id WHERE i.id=?""", (sid,))

    def invoice_totals(self, inv):
        return doc_totals(doc_lines(inv), inv["discount"], inv["vat_rate"])

    def invoice_received(self, sid):
        r = self.one("SELECT COALESCE(SUM(amount),0) s FROM sales_receipts WHERE invoice_id=?", (sid,))
        return r2(r["s"])

    def save_sales_invoice(self, data, sid=None):
        d = dict(data)
        if not d.get("client_id"):
            raise ValueError("client_missing")
        lines = [l for l in (d.get("lines") or []) if _num(l.get("qty")) or _num(l.get("price"))]
        if not lines:
            raise ValueError("lines_missing")
        d["lines"] = json.dumps(lines, ensure_ascii=False)
        d.setdefault("status", "posted")
        fields = ("client_id", "date", "due_date", "lines", "discount", "vat_rate", "notes", "status")
        vals = [d.get(f) if d.get(f) is not None else "" for f in fields]
        if sid:
            old = self.sales_invoice(sid)
            self._check_lock(old["date"] if old else None, d.get("date"))
            self.x(f"UPDATE sales_invoices SET {', '.join(f + '=?' for f in fields)} WHERE id=?",
                   (*vals, sid))
        else:
            self._check_lock(d.get("date"))
            sid = self.x(f"INSERT INTO sales_invoices({', '.join(fields)}, code, created) "
                         f"VALUES({', '.join('?' * len(fields))}, ?, ?)",
                         (*vals, self.next_code("sales_invoices"), _now()))
        self.repost_stock_costs(also_sale=sid)
        return sid

    def delete_sales_invoice(self, sid):
        if self.one("SELECT 1 FROM sales_receipts WHERE invoice_id=? LIMIT 1", (sid,)):
            raise ValueError("invoice_paid")
        self._clear_source("sale", sid)
        self.x("DELETE FROM sales_invoices WHERE id=?", (sid,))
        self.repost_stock_costs()

    def post_sale(self, sid, cost=0.0, commit=True):
        self._post("sale", sid, lambda: self._build_sale(sid, cost), commit)

    def _build_sale(self, sid, cost):
        s = self.one("SELECT * FROM sales_invoices WHERE id=?", (sid,))
        if not s or s["status"] == "cancelled":
            return
        sub, disc, net, vat, total = doc_totals(doc_lines(s), s["discount"], s["vat_rate"])
        if not total and not cost:
            return
        c = self.one("SELECT name FROM clients WHERE id=?", (s["client_id"],))
        lines = [{"account_id": self.sys_account("ar"), "debit": total, "client_id": s["client_id"]},
                 {"account_id": self.sys_account("sales"), "credit": net, "client_id": s["client_id"]}]
        if vat:
            lines.append({"account_id": self.sys_account("vat_out"), "credit": vat})
        if r2(cost):
            lines += [{"account_id": self.sys_account("cogs"), "debit": r2(cost)},
                      {"account_id": self.sys_account("inventory"), "credit": r2(cost)}]
        self._new_entry(s["date"], f"فاتورة بيع {s['code']} - {c['name'] if c else ''}", lines,
                        "sale", sid, s["code"])

    def repost_stock_costs(self, also_sale=None):
        """Costs depend on the order of all purchases and sales: recompute and re-post
        every sales invoice and stock adjustment whose entry changed."""
        _, costs, _ = self.stock_run()
        for (sid,) in self.con.execute("SELECT id FROM sales_invoices").fetchall():
            self.post_sale(sid, costs.get(("sale", sid), 0.0), commit=False)
        for (aid,) in self.con.execute("SELECT id FROM stock_adjustments").fetchall():
            self.post_adjustment(aid, costs.get(("adj", aid), 0.0), commit=False)
        self._commit()

    # ---------- customer receipts ----------
    def sales_receipts(self, search="", date_from=None, date_to=None, client_id=None):
        s = f"%{search.strip()}%"
        sql = """SELECT r.*, c.name AS client_name, i.code AS invoice_code FROM sales_receipts r
                 JOIN clients c ON c.id=r.client_id
                 LEFT JOIN sales_invoices i ON i.id=r.invoice_id
                 WHERE (r.code LIKE ? OR c.name LIKE ? OR r.note LIKE ?)"""
        args = [s, s, s]
        if date_from:
            sql += " AND r.date>=?"
            args.append(date_from)
        if date_to:
            sql += " AND r.date<=?"
            args.append(date_to)
        if client_id:
            sql += " AND r.client_id=?"
            args.append(client_id)
        return self.q(sql + " ORDER BY r.date DESC, r.id DESC", args)

    def sales_receipt(self, rid):
        return self.one("""SELECT r.*, c.name AS client_name, c.code AS client_code, i.code AS invoice_code
                           FROM sales_receipts r JOIN clients c ON c.id=r.client_id
                           LEFT JOIN sales_invoices i ON i.id=r.invoice_id WHERE r.id=?""", (rid,))

    def save_sales_receipt(self, data, rid=None):
        d = dict(data)
        if not d.get("client_id") or _num(d.get("amount")) <= 0:
            raise ValueError("receipt_missing")
        d["invoice_id"] = d.get("invoice_id") or None
        d["account_id"] = d.get("account_id") or self.money_account_for_method(d.get("method"))
        fields = ("client_id", "invoice_id", "date", "amount", "method", "account_id", "note")
        vals = [d.get(f) if f == "invoice_id" else (d.get(f) if d.get(f) is not None else "")
                for f in fields]
        if rid:
            self.x(f"UPDATE sales_receipts SET {', '.join(f + '=?' for f in fields)} WHERE id=?",
                   (*vals, rid))
        else:
            rid = self.x(f"INSERT INTO sales_receipts({', '.join(fields)}, code, created) "
                         f"VALUES({', '.join('?' * len(fields))}, ?, ?)",
                         (*vals, self.next_code("sales_receipts"), _now()))
        self.post_srec(rid)
        return rid

    def delete_sales_receipt(self, rid):
        self._clear_source("srec", rid)
        self.x("DELETE FROM sales_receipts WHERE id=?", (rid,))

    def post_srec(self, rid, commit=True):
        self._post("srec", rid, lambda: self._build_srec(rid), commit)

    def _build_srec(self, rid):
        r = self.one("SELECT * FROM sales_receipts WHERE id=?", (rid,))
        if not r or not r2(r["amount"]):
            return
        c = self.one("SELECT name FROM clients WHERE id=?", (r["client_id"],))
        memo = f"سند قبض {r['code']} - {c['name'] if c else ''}" + (f" - {r['note']}" if r["note"] else "")
        self._new_entry(r["date"], memo, [
            {"account_id": r["account_id"] or self.sys_account("cash"), "debit": r2(r["amount"])},
            {"account_id": self.sys_account("ar"), "credit": r2(r["amount"]), "client_id": r["client_id"]}],
            "srec", rid, r["code"])

    # ---------- stock adjustments ----------
    def stock_adjustments(self, item_id=None):
        sql = """SELECT a.*, i.name AS item_name, i.unit FROM stock_adjustments a
                 JOIN items i ON i.id=a.item_id"""
        args = []
        if item_id:
            sql += " WHERE a.item_id=?"
            args.append(item_id)
        return self.q(sql + " ORDER BY a.date DESC, a.id DESC", args)

    def save_stock_adjustment(self, data, aid=None):
        d = dict(data)
        it = self.item(d.get("item_id")) if d.get("item_id") else None
        if not it or it["kind"] != "stock" or not _num(d.get("qty")):
            raise ValueError("adjustment_missing")
        d["account_id"] = d.get("account_id") or None
        fields = ("item_id", "date", "qty", "unit_cost", "reason", "account_id", "note")
        vals = [d.get(f) if f == "account_id" else (d.get(f) if d.get(f) is not None else "")
                for f in fields]
        if aid:
            old = self.one("SELECT date FROM stock_adjustments WHERE id=?", (aid,))
            self._check_lock(old["date"] if old else None, d.get("date"))
            self.x(f"UPDATE stock_adjustments SET {', '.join(f + '=?' for f in fields)} WHERE id=?",
                   (*vals, aid))
        else:
            self._check_lock(d.get("date"))
            aid = self.x(f"INSERT INTO stock_adjustments({', '.join(fields)}, code, created) "
                         f"VALUES({', '.join('?' * len(fields))}, ?, ?)",
                         (*vals, self.next_code("stock_adjustments"), _now()))
        self.repost_stock_costs()
        return aid

    def delete_stock_adjustment(self, aid):
        self._clear_source("adj", aid)
        self.x("DELETE FROM stock_adjustments WHERE id=?", (aid,))
        self.repost_stock_costs()

    def post_adjustment(self, aid, cost=0.0, commit=True):
        self._post("adj", aid, lambda: self._build_adjustment(aid, cost), commit)

    def _build_adjustment(self, aid, cost):
        a = self.one("""SELECT a.*, i.name AS item_name FROM stock_adjustments a
                        JOIN items i ON i.id=a.item_id WHERE a.id=?""", (aid,))
        if not a or not r2(cost):
            return
        reason = {k: ar for k, ar, _ in ADJ_REASONS}.get(a["reason"], "")
        memo = f"{reason} {a['code']} - {a['item_name']} ({a['qty']:g})"
        inv = self.sys_account("inventory")
        if a["qty"] > 0:
            other = a["account_id"] or (self.sys_account("opening") if a["reason"] == "opening"
                                        else self.sys_account("shrink"))
            lines = [{"account_id": inv, "debit": r2(cost)}, {"account_id": other, "credit": r2(cost)}]
        else:
            other = a["account_id"] or self.sys_account("shrink")
            lines = [{"account_id": other, "debit": r2(cost)}, {"account_id": inv, "credit": r2(cost)}]
        self._new_entry(a["date"], memo, lines, "adj", aid, a["code"])

    # ---------- rebuild (called by rebuild_all_entries) ----------
    def _rebuild_business(self):
        if not self.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='purchase_bills'"):
            return          # first opening: the tables are made right after
        for (bid,) in self.con.execute("SELECT id FROM purchase_bills").fetchall():
            self.post_bill(bid, commit=False)
        for (pid,) in self.con.execute("SELECT id FROM supplier_payments").fetchall():
            self.post_spay(pid, commit=False)
        for (rid,) in self.con.execute("SELECT id FROM sales_receipts").fetchall():
            self.post_srec(rid, commit=False)
        _, costs, _ = self.stock_run()
        for (sid,) in self.con.execute("SELECT id FROM sales_invoices").fetchall():
            self.post_sale(sid, costs.get(("sale", sid), 0.0), commit=False)
        for (aid,) in self.con.execute("SELECT id FROM stock_adjustments").fetchall():
            self.post_adjustment(aid, costs.get(("adj", aid), 0.0), commit=False)

    # ---------- reports ----------
    def vat_report(self, date_from, date_to):
        out_rows, in_rows = [], []
        for s in self.q("""SELECT i.*, c.name AS party FROM sales_invoices i JOIN clients c ON c.id=i.client_id
                           WHERE i.status<>'cancelled' AND i.date BETWEEN ? AND ? ORDER BY i.date, i.id""",
                        (date_from, date_to)):
            _, _, net, vat, total = self.invoice_totals(s)
            out_rows.append({"date": s["date"], "code": s["code"], "party": s["party"], "net": net,
                             "vat": vat, "total": total})
        for b in self.q("""SELECT b.*, s.name AS party, s.vat_number FROM purchase_bills b
                           JOIN suppliers s ON s.id=b.supplier_id
                           WHERE b.status<>'cancelled' AND b.date BETWEEN ? AND ? ORDER BY b.date, b.id""",
                        (date_from, date_to)):
            _, _, net, vat, total = self.bill_totals(b)
            in_rows.append({"date": b["date"], "code": b["code"], "party": b["party"], "net": net,
                            "vat": vat, "total": total, "ref": b["supplier_ref"]})
        out_vat = r2(sum(r["vat"] for r in out_rows))
        in_vat = r2(sum(r["vat"] for r in in_rows))
        return {"sales": out_rows, "purchases": in_rows, "out_vat": out_vat, "in_vat": in_vat,
                "sales_net": r2(sum(r["net"] for r in out_rows)),
                "purchases_net": r2(sum(r["net"] for r in in_rows)), "net_vat": r2(out_vat - in_vat)}

    def sales_by_item(self, date_from, date_to):
        agg = {}
        for s in self.q("SELECT * FROM sales_invoices WHERE status<>'cancelled' AND date BETWEEN ? AND ?",
                        (date_from, date_to)):
            lines = doc_lines(s)
            sub = sum(_num(l.get("qty")) * _num(l.get("price")) for l in lines)
            factor = (sub - _num(s["discount"])) / sub if sub > 0 else 1.0
            for l in lines:
                key = l.get("item_id") or ("txt", l.get("desc", ""))
                a = agg.setdefault(key, {"name": l.get("desc", ""), "qty": 0.0, "value": 0.0})
                a["qty"] += _num(l.get("qty"))
                a["value"] += _num(l.get("qty")) * _num(l.get("price")) * factor
        for k, a in agg.items():
            if isinstance(k, int):
                it = self.item(k)
                if it:
                    a["name"] = it["name"]
            a["value"] = r2(a["value"])
        return sorted(agg.values(), key=lambda a: -a["value"])

    def period_totals(self, date_from, date_to):
        def tot(table, fn):
            s = 0.0
            for r in self.q(f"SELECT * FROM {table} WHERE status<>'cancelled' AND date BETWEEN ? AND ?",
                            (date_from, date_to)):
                s += fn(r)[2]
            return r2(s)
        return {"sales": tot("sales_invoices", self.invoice_totals),
                "purchases": tot("purchase_bills", self.bill_totals)}

    def money_balance(self):
        ids = [a["id"] for a in self.accounts(money_only=True)]
        return r2(sum(self.account_balance(i) for i in ids))

    def unpaid_sales_invoices(self):
        out = []
        for s in self.q("""SELECT i.*, c.name AS client_name, c.phone AS client_phone FROM sales_invoices i
                           JOIN clients c ON c.id=i.client_id WHERE i.status<>'cancelled'
                           ORDER BY COALESCE(NULLIF(i.due_date,''), i.date), i.id"""):
            total = self.invoice_totals(s)[4]
            rem = r2(total - self.invoice_received(s["id"]))
            if rem > 0.009:
                out.append({"inv": s, "total": total, "remaining": rem})
        return out

    def unpaid_bills(self):
        out = []
        for b in self.q("""SELECT b.*, s.name AS supplier_name FROM purchase_bills b
                           JOIN suppliers s ON s.id=b.supplier_id WHERE b.status<>'cancelled'
                           ORDER BY COALESCE(NULLIF(b.due_date,''), b.date), b.id"""):
            total = self.bill_totals(b)[4]
            rem = r2(total - self.bill_paid(b["id"]))
            if rem > 0.009:
                out.append({"bill": b, "total": total, "remaining": rem})
        return out

    # ---------- company documents ----------
    def company_docs_root(self) -> Path:
        p = self.files_root() / "ملفات الشركة"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def company_docs(self, search=""):
        s = f"%{search.strip()}%"
        return self.q("""SELECT * FROM company_docs WHERE title LIKE ? OR doc_number LIKE ? OR notes LIKE ?
                         ORDER BY CASE WHEN COALESCE(expiry,'')='' THEN 1 ELSE 0 END, expiry, title""",
                      (s, s, s))

    def company_doc(self, did):
        return self.one("SELECT * FROM company_docs WHERE id=?", (did,))

    def save_company_doc(self, data, did=None, src_file=None):
        if not (data.get("title") or "").strip():
            raise ValueError("doc_title")
        d = dict(data)
        if src_file:
            src = Path(src_file)
            target = self.company_docs_root() / src.name
            n = 1
            while target.exists():
                target = target.with_name(f"{src.stem} ({n}){src.suffix}")
                n += 1
            shutil.copyfile(src, target)
            d["path"], d["original_name"] = str(target), src.name
        fields = ["title", "doc_number", "issue_date", "expiry", "notes"]
        if src_file:
            fields += ["path", "original_name"]
        vals = [d.get(f) if d.get(f) is not None else "" for f in fields]
        if did:
            self.x(f"UPDATE company_docs SET {', '.join(f + '=?' for f in fields)} WHERE id=?", (*vals, did))
            return did
        return self.x(f"INSERT INTO company_docs({', '.join(fields)}, created) "
                      f"VALUES({', '.join('?' * len(fields))}, ?)", (*vals, _now()))

    def delete_company_doc(self, did):
        row = self.company_doc(did)
        self.x("DELETE FROM company_docs WHERE id=?", (did,))
        if row and row["path"]:
            p = Path(row["path"])
            try:
                if p.exists() and p.parent == self.company_docs_root():
                    trash = self.company_docs_root() / "_محذوف"
                    trash.mkdir(exist_ok=True)
                    shutil.move(str(p), str(trash / p.name))
            except OSError:
                pass

    def expiring_company_docs(self, days=None):
        days = self.get_int("alert_days", 30) if days is None else days
        limit = (dt.date.today() + dt.timedelta(days=days)).isoformat()
        return self.q("""SELECT * FROM company_docs WHERE COALESCE(expiry,'')<>'' AND expiry<=?
                         ORDER BY expiry""", (limit,))
