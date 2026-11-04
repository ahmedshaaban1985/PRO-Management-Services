# -*- coding: utf-8 -*-
"""
الحسابات (قيد مزدوج) - Double-entry accounting for Creative Reps.

Rules used for the automatic entries:
  * Transaction (no linked invoice):  Dr ذمم العملاء (fee + gov fees)
                                      Cr إيراد الخدمات (fee)  /  Cr رسوم حكومية مستردة (gov fees)
  * Government fees paid for a client: Dr رسوم حكومية مستردة  /  Cr الصندوق أو البنك
  * Invoice:  Dr ذمم العملاء (total) + Dr خصم مسموح به (discount)
              Cr إيراد الخدمات (services) / Cr رسوم حكومية مستردة (gov fees)
  * Receipt (سند قبض):  Dr الصندوق أو البنك  /  Cr ذمم العملاء
  * Expense:  Dr حساب المصروف  /  Cr الصندوق أو البنك
Government fees are NOT income and NOT expense: they sit on the client until billed.
Automatic entries are rebuilt every time their source document is saved.
"""
import datetime as dt

TYPES = {  # type: (arabic, english, normal side)
    "asset": ("الأصول", "Assets", "debit"),
    "liability": ("الخصوم", "Liabilities", "credit"),
    "equity": ("حقوق الملكية", "Equity", "credit"),
    "income": ("الإيرادات", "Income", "credit"),
    "expense": ("المصروفات", "Expenses", "debit"),
}

# code, arabic, english, type, parent code, is_group, is_money, sys_key
DEFAULT_CHART = [
    ("1", "الأصول", "Assets", "asset", None, 1, 0, ""),
    ("11", "الأصول المتداولة", "Current Assets", "asset", "1", 1, 0, ""),
    ("1101", "الصندوق (النقدية)", "Cash on Hand", "asset", "11", 0, 1, "cash"),
    ("1102", "البنك", "Bank", "asset", "11", 0, 1, "bank"),
    ("1103", "ذمم العملاء (المدينون)", "Accounts Receivable", "asset", "11", 0, 0, "ar"),
    ("1104", "رسوم حكومية مستردة من العملاء", "Government Fees Recoverable", "asset", "11", 0, 0, "disb"),
    ("1105", "سلف وعُهد الموظفين", "Employee Advances", "asset", "11", 0, 0, "emp_adv"),
    ("1106", "مصروفات مدفوعة مقدمًا", "Prepaid Expenses", "asset", "11", 0, 0, ""),
    ("12", "الأصول الثابتة", "Fixed Assets", "asset", "1", 1, 0, ""),
    ("1201", "أثاث ومعدات مكتبية", "Furniture & Office Equipment", "asset", "12", 0, 0, ""),
    ("1202", "أجهزة كمبيوتر", "Computers", "asset", "12", 0, 0, ""),
    ("1203", "سيارات", "Vehicles", "asset", "12", 0, 0, ""),
    ("1209", "مجمع الإهلاك", "Accumulated Depreciation", "asset", "12", 0, 0, ""),
    ("2", "الخصوم", "Liabilities", "liability", None, 1, 0, ""),
    ("21", "الخصوم المتداولة", "Current Liabilities", "liability", "2", 1, 0, ""),
    ("2101", "الموردون (الدائنون)", "Accounts Payable", "liability", "21", 0, 0, "ap"),
    ("2102", "دفعات مقدمة من العملاء", "Customer Advances", "liability", "21", 0, 0, ""),
    ("2103", "رواتب مستحقة", "Salaries Payable", "liability", "21", 0, 0, ""),
    ("2104", "مصروفات مستحقة", "Accrued Expenses", "liability", "21", 0, 0, ""),
    ("22", "الخصوم طويلة الأجل", "Long-term Liabilities", "liability", "2", 1, 0, ""),
    ("2201", "قروض", "Loans", "liability", "22", 0, 0, ""),
    ("3", "حقوق الملكية", "Equity", "equity", None, 1, 0, ""),
    ("3101", "رأس المال", "Capital", "equity", "3", 0, 0, "capital"),
    ("3102", "جاري المالك (المسحوبات)", "Owner's Drawings", "equity", "3", 0, 0, "drawings"),
    ("3103", "الأرباح المحتجزة", "Retained Earnings", "equity", "3", 0, 0, "retained"),
    ("3104", "أرصدة افتتاحية", "Opening Balances", "equity", "3", 0, 0, "opening"),
    ("4", "الإيرادات", "Income", "income", None, 1, 0, ""),
    ("4101", "إيرادات خدمات تخليص المعاملات", "Transaction Clearing Services", "income", "4", 0, 0, "revenue"),
    ("4102", "إيرادات تأسيس الشركات", "Company Formation Services", "income", "4", 0, 0, ""),
    ("4103", "إيرادات خدمات الترجمة", "Translation Services", "income", "4", 0, 0, ""),
    ("4104", "إيرادات أخرى", "Other Income", "income", "4", 0, 0, ""),
    ("5", "المصروفات", "Expenses", "expense", None, 1, 0, ""),
    ("5101", "رواتب وأجور", "Salaries & Wages", "expense", "5", 0, 0, ""),
    ("5102", "إيجار المكتب", "Office Rent", "expense", "5", 0, 0, ""),
    ("5103", "مواصلات وبنزين", "Transport & Fuel", "expense", "5", 0, 0, ""),
    ("5104", "اتصالات وإنترنت", "Telephone & Internet", "expense", "5", 0, 0, ""),
    ("5105", "اشتراكات وبرامج", "Subscriptions & Software", "expense", "5", 0, 0, ""),
    ("5106", "طباعة وقرطاسية", "Printing & Stationery", "expense", "5", 0, 0, ""),
    ("5107", "رسوم حكومية على الشركة نفسها", "Company's Own Government Fees", "expense", "5", 0, 0, "gov_own"),
    ("5108", "خصم مسموح به", "Discounts Allowed", "expense", "5", 0, 0, "discount"),
    ("5109", "عمولات ومصروفات بنكية", "Bank Charges", "expense", "5", 0, 0, ""),
    ("5110", "إهلاك", "Depreciation", "expense", "5", 0, 0, ""),
    ("5111", "دعاية وتسويق", "Marketing", "expense", "5", 0, 0, ""),
    ("5199", "مصروفات أخرى", "Other Expenses", "expense", "5", 0, 0, "other_exp"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL, name_ar TEXT NOT NULL, name_en TEXT DEFAULT '',
    type TEXT NOT NULL, parent_id INTEGER REFERENCES accounts(id),
    is_group INTEGER DEFAULT 0, is_money INTEGER DEFAULT 0,
    sys_key TEXT DEFAULT '', active INTEGER DEFAULT 1, notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, date TEXT NOT NULL,
    memo TEXT DEFAULT '', ref TEXT DEFAULT '',
    source_type TEXT DEFAULT 'manual', source_id INTEGER, created TEXT
);
CREATE TABLE IF NOT EXISTS jlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES journal(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    debit REAL DEFAULT 0, credit REAL DEFAULT 0,
    client_id INTEGER, memo TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS vouchers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, date TEXT NOT NULL,
    kind TEXT DEFAULT 'refund', client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    account_id INTEGER, paid_from_id INTEGER, amount REAL DEFAULT 0,
    method TEXT DEFAULT '', payee TEXT DEFAULT '', note TEXT DEFAULT '', created TEXT
);
CREATE INDEX IF NOT EXISTS idx_jl_acc ON jlines(account_id);
CREATE INDEX IF NOT EXISTS idx_jl_entry ON jlines(entry_id);
CREATE INDEX IF NOT EXISTS idx_jl_client ON jlines(client_id);
CREATE INDEX IF NOT EXISTS idx_j_source ON journal(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_j_date ON journal(date);
"""

EXTRA_COLUMNS = {
    "transactions": [("revenue_account_id", "INTEGER"), ("gov_account_id", "INTEGER")],
    "invoices": [("revenue_account_id", "INTEGER")],
    "payments": [("account_id", "INTEGER")],
    "expenses": [("account_id", "INTEGER"), ("paid_from_id", "INTEGER")],
    "jlines": [("supplier_id", "INTEGER")],
}

CANCELLED = "ملغاة|Cancelled"


class PeriodLocked(ValueError):
    """Raised when a change would alter the accounts of a closed (locked) period."""

    def __init__(self, date, lock):
        super().__init__("period_locked")
        self.date, self.lock = date, lock


def r2(v):
    return round(float(v or 0) + 1e-9, 2)


class AccountingMixin:
    """Mixed into core.DB."""

    # ---------------- setup ----------------
    def _acc_setup(self):
        self.con.executescript(SCHEMA)
        for table, cols in EXTRA_COLUMNS.items():
            have = {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    self.con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        first_time = self.con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
        if first_time:
            ids = {}
            for code, ar, en, typ, parent, grp, money, key in DEFAULT_CHART:
                cur = self.con.execute(
                    "INSERT INTO accounts(code, name_ar, name_en, type, parent_id, is_group, "
                    "is_money, sys_key) VALUES(?,?,?,?,?,?,?,?)",
                    (code, ar, en, typ, ids.get(parent), grp, money, key))
                ids[code] = cur.lastrowid
        self._commit()
        # give old expenses an account based on their category text
        for x in self.con.execute("SELECT id, category FROM expenses WHERE account_id IS NULL "
                                  "OR paid_from_id IS NULL").fetchall():
            self.con.execute("UPDATE expenses SET account_id=COALESCE(account_id, ?), "
                             "paid_from_id=COALESCE(paid_from_id, ?) WHERE id=?",
                             (self.guess_expense_account(x[1]), self.sys_account("cash"), x[0]))
        self._commit()
        if first_time:
            self.rebuild_all_entries()

    def guess_expense_account(self, category):
        word = (category or "").split("|")[0].strip()
        if word:
            for a in self.con.execute("SELECT id, name_ar FROM accounts WHERE type='expense' "
                                      "AND is_group=0").fetchall():
                if word[:5] and (word[:5] in a[1] or a[1][:5] in word):
                    return a[0]
            if "حكوم" in word:
                return self.sys_account("gov_own")
        return self.sys_account("other_exp")

    # ---------------- accounts ----------------
    def sys_account(self, key):
        r = self.con.execute("SELECT id FROM accounts WHERE sys_key=?", (key,)).fetchone()
        return r[0] if r else None

    def account(self, aid):
        return self.one("SELECT * FROM accounts WHERE id=?", (aid,))

    def accounts(self, types=None, leaf_only=False, money_only=False, active_only=True):
        sql = "SELECT * FROM accounts WHERE 1=1"
        args = []
        if types:
            sql += f" AND type IN ({','.join('?' * len(types))})"
            args += list(types)
        if leaf_only:
            sql += " AND is_group=0"
        if money_only:
            sql += " AND is_money=1"
        if active_only:
            sql += " AND active=1"
        return self.q(sql + " ORDER BY code", args)

    def save_account(self, data, aid=None):
        fields = ("code", "name_ar", "name_en", "type", "parent_id", "is_group", "is_money",
                  "active", "notes")
        vals = [data.get(f) for f in fields]
        if aid:
            self.x(f"UPDATE accounts SET {', '.join(f + '=?' for f in fields)} WHERE id=?",
                   (*vals, aid))
            return aid
        return self.x(f"INSERT INTO accounts({', '.join(fields)}) "
                      f"VALUES({', '.join('?' * len(fields))})", vals)

    def account_in_use(self, aid):
        return bool(self.one("SELECT 1 FROM jlines WHERE account_id=? LIMIT 1", (aid,))
                    or self.one("SELECT 1 FROM accounts WHERE parent_id=? LIMIT 1", (aid,)))

    def delete_account(self, aid):
        self.x("DELETE FROM accounts WHERE id=?", (aid,))

    def next_child_code(self, parent_id):
        p = self.account(parent_id)
        kids = [r["code"] for r in self.q("SELECT code FROM accounts WHERE parent_id=?", (parent_id,))]
        width = 4 if len(p["code"]) <= 2 else len(p["code"]) + 2
        n = 1
        while True:
            code = p["code"] + str(n).zfill(width - len(p["code"]))
            if code not in kids and not self.one("SELECT 1 FROM accounts WHERE code=?", (code,)):
                return code
            n += 1

    def account_label(self, aid, lang="ar"):
        a = self.account(aid)
        if not a:
            return ""
        name = a["name_en"] if lang == "en" and a["name_en"] else a["name_ar"]
        return f"{a['code']} - {name}"

    def money_account_for_method(self, method):
        m = (method or "").split("|")[0]
        return self.sys_account("cash") if ("نقد" in m or not m) else self.sys_account("bank")

    # ---------------- journal ----------------
    _collect = None
    _bypass_lock = False

    def lock_date(self):
        return self.get("lock_date") or ""

    def _check_lock(self, *dates):
        lock = self.lock_date()
        if not lock or self._bypass_lock:
            return
        for d in dates:
            if d and d <= lock:
                raise PeriodLocked(d, lock)

    def _new_entry(self, date, memo, lines, source_type="manual", source_id=None, ref=""):
        lines = [l for l in lines if r2(l.get("debit")) or r2(l.get("credit"))]
        if not lines:
            return None
        dr = r2(sum(r2(l.get("debit")) for l in lines))
        cr = r2(sum(r2(l.get("credit")) for l in lines))
        if abs(dr - cr) > 0.009:
            raise ValueError(f"unbalanced entry {dr} != {cr}")
        date = date or dt.date.today().isoformat()
        if self._collect is not None:
            self._collect.append((date, memo, lines, source_type, source_id, ref))
            return None
        cur = self.con.execute(
            "INSERT INTO journal(code, date, memo, ref, source_type, source_id, created) "
            "VALUES(?,?,?,?,?,?,?)",
            (None, date, memo, ref, source_type, source_id,
             dt.datetime.now().strftime("%Y-%m-%d %H:%M")))
        eid = cur.lastrowid
        self.con.execute("UPDATE journal SET code=? WHERE id=?", (f"JV-{eid:05d}", eid))
        for l in lines:
            if l.get("supplier_id"):
                self.con.execute("INSERT INTO jlines(entry_id, account_id, debit, credit, client_id, "
                                 "memo, supplier_id) VALUES(?,?,?,?,?,?,?)",
                                 (eid, l["account_id"], r2(l.get("debit")), r2(l.get("credit")),
                                  l.get("client_id"), l.get("memo", ""), l["supplier_id"]))
            else:
                self.con.execute("INSERT INTO jlines(entry_id, account_id, debit, credit, client_id, memo) "
                                 "VALUES(?,?,?,?,?,?)",
                                 (eid, l["account_id"], r2(l.get("debit")), r2(l.get("credit")),
                                  l.get("client_id"), l.get("memo", "")))
        return eid

    def _clear_source(self, source_type, source_id):
        dates = [r["date"] for r in self.q("SELECT date FROM journal WHERE source_type=? AND source_id=?",
                                            (source_type, source_id))]
        self._check_lock(*dates)
        self.con.execute("DELETE FROM journal WHERE source_type=? AND source_id=?",
                         (source_type, source_id))

    @staticmethod
    def _signature(entries):
        out = []
        for date, _memo, lines, *_ in entries:
            out.append((date, tuple(sorted((l["account_id"] or 0, r2(l.get("debit")), r2(l.get("credit")),
                                            l.get("client_id") or 0, l.get("supplier_id") or 0) for l in lines
                                           if r2(l.get("debit")) or r2(l.get("credit"))))))
        return sorted(out)

    def _existing_signature(self, source_type, source_id):
        entries = []
        for j in self.q("SELECT * FROM journal WHERE source_type=? AND source_id=?",
                        (source_type, source_id)):
            lines = [dict(account_id=l["account_id"], debit=l["debit"], credit=l["credit"],
                          client_id=l["client_id"],
                          supplier_id=l["supplier_id"] if "supplier_id" in l.keys() else None) for l in
                     self.q("SELECT * FROM jlines WHERE entry_id=?", (j["id"],))]
            entries.append((j["date"], j["memo"], lines))
        return self._signature(entries)

    def _post(self, source_type, source_id, build, commit=True):
        """Build the entries a document should have; rewrite them only when they changed
        (so editing e.g. the stage of an old transaction never touches a locked period)."""
        self._collect = []
        try:
            build()
            new = self._collect
        finally:
            self._collect = None
        old_sig = self._existing_signature(source_type, source_id)
        if old_sig == self._signature(new):        # same money: nothing to rewrite
            if commit:
                self._commit()
            return
        self._check_lock(*[e[0] for e in new])
        self._clear_source(source_type, source_id)
        for e in new:
            self._new_entry(*e)
        if commit:
            self._commit()

    def save_manual_entry(self, date, memo, lines, eid=None):
        if eid:
            old = self.entry(eid)
            self._check_lock(old["date"] if old else None)
            self.con.execute("DELETE FROM journal WHERE id=? AND source_type='manual'", (eid,))
        self._check_lock(date)
        new = self._new_entry(date, memo, lines, "manual", None)
        self._commit()
        return new

    def delete_entry(self, eid):
        old = self.entry(eid)
        self._check_lock(old["date"] if old else None)
        self.x("DELETE FROM journal WHERE id=? AND source_type='manual'", (eid,))

    def entry(self, eid):
        return self.one("SELECT * FROM journal WHERE id=?", (eid,))

    def entry_lines(self, eid):
        return self.q("""SELECT l.*, a.code AS acc_code, a.name_ar, a.name_en, c.name AS client_name
                         FROM jlines l JOIN accounts a ON a.id=l.account_id
                         LEFT JOIN clients c ON c.id=l.client_id
                         WHERE l.entry_id=? ORDER BY l.id""", (eid,))

    def entries(self, date_from=None, date_to=None, search="", source=None):
        sql = """SELECT j.*, (SELECT SUM(debit) FROM jlines WHERE entry_id=j.id) AS total
                 FROM journal j WHERE 1=1"""
        args = []
        if date_from:
            sql += " AND j.date>=?"
            args.append(date_from)
        if date_to:
            sql += " AND j.date<=?"
            args.append(date_to)
        if search:
            s = f"%{search.strip()}%"
            sql += " AND (j.memo LIKE ? OR j.ref LIKE ? OR j.code LIKE ?)"
            args += [s, s, s]
        if source == "manual":
            sql += " AND j.source_type='manual'"
        elif source == "auto":
            sql += " AND j.source_type<>'manual'"
        return self.q(sql + " ORDER BY j.date DESC, j.id DESC", args)

    # ---------------- automatic posting ----------------
    def post_transaction(self, tid, commit=True):
        self._post("transaction", tid, lambda: self._build_transaction(tid), commit)

    def _build_transaction(self, tid):
        t = self.one("SELECT * FROM transactions WHERE id=?", (tid,))
        if t:
            c = t["client_id"]
            label = f"{t['code']} - {(t['tx_type'] or '').split('|')[0]}"
            fee, gov = r2(t["fee"]), r2(t["gov_fees"])
            if gov > 0:
                self._new_entry(t["start_date"], f"رسوم حكومية مدفوعة عن العميل: {label}", [
                    {"account_id": self.sys_account("disb"), "debit": gov, "client_id": c},
                    {"account_id": t["gov_account_id"] or self.sys_account("cash"), "credit": gov}],
                    "transaction", tid, t["code"])
            invoiced = self.one("SELECT 1 FROM invoices WHERE tx_id=? AND status<>'cancelled' LIMIT 1",
                                (tid,))
            if t["status"] != CANCELLED and not invoiced and (fee or gov):
                self._new_entry(t["start_date"], f"استحقاق معاملة: {label}", [
                    {"account_id": self.sys_account("ar"), "debit": fee + gov, "client_id": c},
                    {"account_id": t["revenue_account_id"] or self.sys_account("revenue"),
                     "credit": fee, "client_id": c},
                    {"account_id": self.sys_account("disb"), "credit": gov, "client_id": c}],
                    "transaction", tid, t["code"])

    def post_invoice(self, iid, commit=True):
        self._post("invoice", iid, lambda: self._build_invoice(iid), commit)

    def _build_invoice(self, iid):
        import json
        i = self.one("SELECT * FROM invoices WHERE id=?", (iid,))
        if i and i["status"] != "cancelled":
            items = json.loads(i["items"] or "[]")
            sub = r2(sum(float(x.get("qty", 1) or 0) * float(x.get("price", 0) or 0) for x in items))
            gov, disc = r2(i["gov_fees"]), r2(i["discount"])
            c = i["client_id"]
            self._new_entry(i["date"], f"فاتورة {i['code']}", [
                {"account_id": self.sys_account("ar"), "debit": sub + gov - disc, "client_id": c},
                {"account_id": self.sys_account("discount"), "debit": disc, "client_id": c},
                {"account_id": i["revenue_account_id"] or self.sys_account("revenue"),
                 "credit": sub, "client_id": c},
                {"account_id": self.sys_account("disb"), "credit": gov, "client_id": c}],
                "invoice", iid, i["code"])

    def post_payment(self, pid, commit=True):
        self._post("payment", pid, lambda: self._build_payment(pid), commit)

    def _build_payment(self, pid):
        p = self.one("SELECT * FROM payments WHERE id=?", (pid,))
        if p and r2(p["amount"]):
            acc = p["account_id"] or self.money_account_for_method(p["method"])
            self._new_entry(p["pay_date"], f"سند قبض {p['code']}" + (f" - {p['note']}" if p["note"] else ""), [
                {"account_id": acc, "debit": r2(p["amount"])},
                {"account_id": self.sys_account("ar"), "credit": r2(p["amount"]),
                 "client_id": p["client_id"]}], "payment", pid, p["code"])

    def post_expense(self, xid, commit=True):
        self._post("expense", xid, lambda: self._build_expense(xid), commit)

    def _build_expense(self, xid):
        x = self.one("SELECT * FROM expenses WHERE id=?", (xid,))
        if x and r2(x["amount"]):
            memo = f"مصروف {x['code']}: {x['description'] or (x['category'] or '').split('|')[0]}"
            self._new_entry(x["date"], memo, [
                {"account_id": x["account_id"] or self.sys_account("other_exp"),
                 "debit": r2(x["amount"]), "client_id": x["client_id"]},
                {"account_id": x["paid_from_id"] or self.sys_account("cash"),
                 "credit": r2(x["amount"])}], "expense", xid, x["code"])

    # ---------------- payment vouchers (سندات الصرف) ----------------
    def save_voucher(self, data, vid=None):
        """kind 'refund' = money given back to a client (Dr client account);
        kind 'payment' = paid to anyone else against any account (supplier, rent...)."""
        d = dict(data)
        for f in ("client_id", "account_id", "paid_from_id"):
            d[f] = d.get(f) or None
        fields = ("date", "kind", "client_id", "account_id", "paid_from_id", "amount", "method",
                  "payee", "note")
        vals = [d.get(f) if d.get(f) is not None else ("" if f not in ("client_id", "account_id", "paid_from_id") else None)
                for f in fields]
        if vid:
            self.x(f"UPDATE vouchers SET {', '.join(f + '=?' for f in fields)} WHERE id=?", (*vals, vid))
        else:
            code = self._receipt_code() if d.get("kind") == "receipt" else self.next_code("vouchers")
            vid = self.x(f"INSERT INTO vouchers({', '.join(fields)}, code, created) "
                         f"VALUES({', '.join('?' * len(fields))}, ?, ?)",
                         (*vals, code, dt.datetime.now().strftime("%Y-%m-%d %H:%M")))
        self.post_voucher(vid)
        return vid

    def _receipt_code(self):
        """External receipts (money in, not tied to an invoice) share the vouchers table: REC-00001."""
        import re as _re
        n = self.get_int("seq_ext_receipts", 0)
        for r in self.q("SELECT code FROM vouchers WHERE code LIKE 'REC-%'"):
            m = _re.search(r"(\d+)$", r["code"] or "")
            if m:
                n = max(n, int(m.group(1)))
        n += 1
        self.set("seq_ext_receipts", n)
        return f"REC-{n:05d}"

    def voucher(self, vid):
        return self.one("""SELECT v.*, c.name AS client_name, c.code AS client_code
                           FROM vouchers v LEFT JOIN clients c ON c.id=v.client_id WHERE v.id=?""", (vid,))

    def vouchers(self, search="", direction=None):
        """direction 'in' = external receipts only, 'out' = payment vouchers only, None = all."""
        s = f"%{search.strip()}%"
        cond = ""
        if direction == "in":
            cond = " AND v.kind='receipt'"
        elif direction == "out":
            cond = " AND v.kind<>'receipt'"
        return self.q(f"""SELECT v.*, c.name AS client_name FROM vouchers v
                         LEFT JOIN clients c ON c.id=v.client_id
                         WHERE (v.code LIKE ? OR COALESCE(c.name,'') LIKE ? OR v.payee LIKE ? OR v.note LIKE ?){cond}
                         ORDER BY v.date DESC, v.id DESC""", (s, s, s, s))

    def delete_voucher(self, vid):
        self._clear_source("voucher", vid)
        self.x("DELETE FROM vouchers WHERE id=?", (vid,))

    def post_voucher(self, vid, commit=True):
        self._post("voucher", vid, lambda: self._build_voucher(vid), commit)

    def _build_voucher(self, vid):
        v = self.one("SELECT * FROM vouchers WHERE id=?", (vid,))
        if not v or not r2(v["amount"]):
            return
        if v["kind"] == "receipt":
            # money in from anyone (owner, manager, other income...): Dr cash/bank, Cr the chosen account
            memo = f"سند قبض {v['code']}" + (f" - {v['payee']}" if v["payee"] else "") + \
                (f" - {v['note']}" if v["note"] else "")
            self._new_entry(v["date"], memo, [
                {"account_id": v["paid_from_id"] or self.sys_account("cash"), "debit": r2(v["amount"])},
                {"account_id": v["account_id"] or self.sys_account("other_income") or self.sys_account("revenue"),
                 "credit": r2(v["amount"]), "client_id": v["client_id"]}],
                "voucher", vid, v["code"])
            return
        if v["kind"] == "refund":
            debit = {"account_id": self.sys_account("ar"), "debit": r2(v["amount"]),
                     "client_id": v["client_id"]}
        else:
            debit = {"account_id": v["account_id"] or self.sys_account("other_exp"),
                     "debit": r2(v["amount"]), "client_id": v["client_id"]}
        memo = f"سند صرف {v['code']}" + (f" - {v['payee']}" if v["payee"] else "") + \
               (f" - {v['note']}" if v["note"] else "")
        self._new_entry(v["date"], memo, [debit, {
            "account_id": v["paid_from_id"] or self.sys_account("cash"), "credit": r2(v["amount"])}],
            "voucher", vid, v["code"])

    def rebuild_all_entries(self):
        """Delete and re-create every automatic entry from the documents."""
        self._bypass_lock = True
        try:
            self._rebuild_all()
        finally:
            self._bypass_lock = False

    def _rebuild_all(self):
        self.con.execute("DELETE FROM journal WHERE source_type<>'manual'")
        for (tid,) in self.con.execute("SELECT id FROM transactions").fetchall():
            self.post_transaction(tid, commit=False)
        for (iid,) in self.con.execute("SELECT id FROM invoices").fetchall():
            self.post_invoice(iid, commit=False)
        for (pid,) in self.con.execute("SELECT id FROM payments").fetchall():
            self.post_payment(pid, commit=False)
        for (xid,) in self.con.execute("SELECT id FROM expenses").fetchall():
            self.post_expense(xid, commit=False)
        for (vid,) in self.con.execute("SELECT id FROM vouchers").fetchall():
            self.post_voucher(vid, commit=False)
        if hasattr(self, "_rebuild_business"):
            self._rebuild_business()
        self._commit()

    # ---------------- balances & reports ----------------
    def _sums(self, date_from=None, date_to=None):
        sql = """SELECT l.account_id, COALESCE(SUM(l.debit),0) d, COALESCE(SUM(l.credit),0) c
                 FROM jlines l JOIN journal j ON j.id=l.entry_id WHERE 1=1"""
        args = []
        if date_from:
            sql += " AND j.date>=?"
            args.append(date_from)
        if date_to:
            sql += " AND j.date<=?"
            args.append(date_to)
        return {r["account_id"]: (r["d"], r["c"]) for r in self.q(sql + " GROUP BY l.account_id", args)}

    def account_tree(self, date_to=None):
        """All accounts with their own balance and the rolled-up balance of groups.
        balance is signed by the normal side (positive = normal)."""
        sums = self._sums(None, date_to)
        rows = [dict(a) for a in self.q("SELECT * FROM accounts ORDER BY code")]
        by_id = {a["id"]: a for a in rows}
        for a in rows:
            d, c = sums.get(a["id"], (0, 0))
            a["debit"], a["credit"] = d, c
            a["balance"] = r2(d - c) if TYPES[a["type"]][2] == "debit" else r2(c - d)
            a["children"] = []
        for a in rows:
            if a["parent_id"] in by_id:
                by_id[a["parent_id"]]["children"].append(a)

        def roll(a):
            tot = a["balance"]
            for ch in a["children"]:
                tot += roll(ch)
            a["total"] = r2(tot)
            return tot
        roots = [a for a in rows if a["parent_id"] not in by_id]
        for a in roots:
            roll(a)
        return roots, by_id

    def account_balance(self, aid, date_to=None):
        _, by_id = self.account_tree(date_to)
        return by_id[aid]["total"] if aid in by_id else 0.0

    def ledger(self, aid, date_from=None, date_to=None, client_id=None):
        """Lines of one account (and its sub-accounts) with running balance.
        Returns (opening_balance, lines)."""
        _, by_id = self.account_tree()
        ids = []

        def collect(a):
            ids.append(a["id"])
            for ch in a["children"]:
                collect(ch)
        collect(by_id[aid])
        normal = TYPES[by_id[aid]["type"]][2]
        ph = ",".join("?" * len(ids))
        extra, eargs = "", []
        if client_id:
            extra = " AND l.client_id=?"
            eargs = [client_id]
        opening = 0.0
        if date_from:
            r = self.one(f"""SELECT COALESCE(SUM(l.debit),0) d, COALESCE(SUM(l.credit),0) c
                             FROM jlines l JOIN journal j ON j.id=l.entry_id
                             WHERE l.account_id IN ({ph}) AND j.date<?{extra}""",
                         (*ids, date_from, *eargs))
            opening = r2(r["d"] - r["c"]) if normal == "debit" else r2(r["c"] - r["d"])
        sql = f"""SELECT j.date, j.code AS entry_code, j.ref, j.memo, j.id AS entry_id,
                    j.source_type, j.source_id, l.debit, l.credit, l.memo AS line_memo,
                    c.name AS client_name, a.code AS acc_code
                  FROM jlines l JOIN journal j ON j.id=l.entry_id
                  JOIN accounts a ON a.id=l.account_id
                  LEFT JOIN clients c ON c.id=l.client_id
                  WHERE l.account_id IN ({ph}){extra}"""
        args = [*ids, *eargs]
        if date_from:
            sql += " AND j.date>=?"
            args.append(date_from)
        if date_to:
            sql += " AND j.date<=?"
            args.append(date_to)
        out, bal = [], opening
        for r in self.q(sql + " ORDER BY j.date, j.id, l.id", args):
            d = dict(r)
            bal += (d["debit"] - d["credit"]) if normal == "debit" else (d["credit"] - d["debit"])
            d["balance"] = r2(bal)
            out.append(d)
        return opening, out

    def trial_balance(self, date_from=None, date_to=None):
        """Per leaf account: opening, period debit/credit, closing (as debit/credit columns)."""
        before = self._sums(None, (dt.date.fromisoformat(date_from) - dt.timedelta(days=1)).isoformat()) \
            if date_from else {}
        period = self._sums(date_from, date_to)
        rows = []
        for a in self.q("SELECT * FROM accounts WHERE is_group=0 ORDER BY code"):
            od, oc = before.get(a["id"], (0, 0))
            pd, pc = period.get(a["id"], (0, 0))
            if not (od or oc or pd or pc):
                continue
            ob = r2(od - oc)
            cb = r2(ob + pd - pc)
            rows.append({"id": a["id"], "code": a["code"], "name_ar": a["name_ar"],
                         "name_en": a["name_en"], "type": a["type"],
                         "open_dr": max(ob, 0) + 0.0, "open_cr": max(-ob, 0) + 0.0,
                         "debit": r2(pd), "credit": r2(pc),
                         "close_dr": max(cb, 0) + 0.0, "close_cr": max(-cb, 0) + 0.0})
        return rows

    def income_statement(self, date_from, date_to):
        sums = self._sums(date_from, date_to)
        inc, exp = [], []
        for a in self.q("SELECT * FROM accounts WHERE is_group=0 AND type IN ('income','expense') "
                        "ORDER BY code"):
            d, c = sums.get(a["id"], (0, 0))
            amt = r2(c - d) if a["type"] == "income" else r2(d - c)
            if amt:
                (inc if a["type"] == "income" else exp).append(
                    {"id": a["id"], "code": a["code"], "name_ar": a["name_ar"],
                     "name_en": a["name_en"], "amount": amt})
        ti = r2(sum(x["amount"] for x in inc))
        te = r2(sum(x["amount"] for x in exp))
        return {"income": inc, "expenses": exp, "total_income": ti, "total_expenses": te,
                "net": r2(ti - te)}

    def balance_sheet(self, date_to):
        sums = self._sums(None, date_to)
        year_start = date_to[:4] + "-01-01"
        before_year = (dt.date.fromisoformat(year_start) - dt.timedelta(days=1)).isoformat()
        out = {"asset": [], "liability": [], "equity": []}
        for a in self.q("SELECT * FROM accounts WHERE is_group=0 AND type IN ('asset','liability','equity') "
                        "ORDER BY code"):
            d, c = sums.get(a["id"], (0, 0))
            amt = r2(d - c) if a["type"] == "asset" else r2(c - d)
            if amt:
                out[a["type"]].append({"id": a["id"], "code": a["code"], "name_ar": a["name_ar"],
                                       "name_en": a["name_en"], "amount": amt})
        prev = self.income_statement("0000-01-01", before_year)["net"]
        cur = self.income_statement(year_start, date_to)["net"]
        if prev:
            out["equity"].append({"id": None, "code": "", "name_ar": "أرباح (خسائر) السنوات السابقة",
                                  "name_en": "Prior years' profit (loss)", "amount": prev})
        out["equity"].append({"id": None, "code": "", "name_ar": "صافي ربح (خسارة) السنة الحالية",
                              "name_en": "Current year profit (loss)", "amount": cur})
        tot = {k: r2(sum(x["amount"] for x in v)) for k, v in out.items()}
        return out, tot

    # ---------------- client balances from the ledger ----------------
    def ar_balances(self):
        return {r["client_id"]: r2(r["b"]) for r in self.q(
            """SELECT l.client_id, SUM(l.debit - l.credit) b FROM jlines l
               WHERE l.account_id=? AND l.client_id IS NOT NULL GROUP BY l.client_id""",
            (self.sys_account("ar"),))}

    def client_ledger(self, cid):
        _, lines = self.ledger(self.sys_account("ar"), client_id=cid)
        return lines
