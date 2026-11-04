# -*- coding: utf-8 -*-
"""المستخدمين والصلاحيات وسجل النشاط - users, permissions and activity log.

Every public DB method that changes data is wrapped: it checks the logged-in
user's permission and, after it succeeds, writes one line in the activity log.
"""
import datetime as dt
import functools
import hashlib
import hmac
import json
import secrets

# (key, arabic, english)
PERMISSIONS = [
    ("clients_view", "عرض العملاء", "View clients"),
    ("clients_edit", "إضافة وتعديل العملاء والملاك والمستندات", "Add / edit clients, owners, documents"),
    ("clients_delete", "حذف العملاء", "Delete clients"),
    ("employees_view", "عرض موظفي الشركات", "View company employees"),
    ("employees_edit", "إضافة وتعديل وحذف موظفي الشركات", "Add / edit employees"),
    ("files", "رفع وحذف الملفات", "Upload / delete files"),
    ("tx_view", "عرض المعاملات", "View transactions"),
    ("tx_edit", "إضافة وتعديل المعاملات", "Add / edit transactions"),
    ("tx_delete", "حذف المعاملات", "Delete transactions"),
    ("money", "رؤية المبالغ (الأتعاب والمدفوع والمتبقي)", "See amounts (fees, paid, balance)"),
    ("salaries", "رؤية رواتب الموظفين وحساباتهم البنكية", "See employee salaries & bank details"),
    ("tasks", "المهام", "Tasks"),
    ("sales_view", "عرض عروض الأسعار والفواتير والسندات", "View quotations, invoices, receipts"),
    ("sales_edit", "إنشاء وتعديل عروض الأسعار والفواتير", "Create / edit quotations & invoices"),
    ("receipts", "تسجيل وتعديل سندات القبض", "Record / edit receipts"),
    ("sales_delete", "حذف الفواتير والسندات وعروض الأسعار", "Delete invoices, receipts, quotations"),
    ("expenses", "المصروفات", "Expenses"),
    ("finance", "المتابعة المالية", "Finance overview"),
    ("accounting", "عرض الحسابات والتقارير المحاسبية", "View accounting & reports"),
    ("accounting_edit", "القيود اليدوية وتعديل شجرة الحسابات", "Manual entries & chart of accounts"),
    ("templates", "قوالب الرسائل والخطابات", "Message & letter templates"),
    ("export", "التصدير إلى Excel", "Export to Excel"),
    ("settings", "الإعدادات والنسخ الاحتياطي", "Settings & backup"),
    ("users", "إدارة المستخدمين وسجل النشاط", "Users & activity log"),
    ("crm", "إدارة العملاء المحتملين والمتابعات (CRM)", "Leads & follow-ups (CRM)"),
    ("crm_campaigns", "الرسائل الجماعية للعملاء", "Group messages"),
    ("crm_delete", "حذف العملاء المحتملين", "Delete leads"),
    ("crm_reports", "تقارير المبيعات", "Sales reports"),
    ("inbox", "رسائل واتساب (قراءة المحادثات والرد على العملاء)", "WhatsApp inbox (read chats & reply)"),
    ("companies", "إضافة وتعديل الشركات", "Add / edit companies"),
    ("renewals", "متابعة الإقامات والتجديدات وإرسال التذكيرات", "Renewals follow-up & reminders"),
    ("purchases", "المشتريات والموردين وسداد الموردين", "Purchases, suppliers & supplier payments"),
    ("inventory", "الأصناف والمخزون وتسويات الجرد", "Items, stock & adjustments"),
    ("company_files", "ملفات الشركة (السجل والرخصة والعقود)", "Company documents"),
    ("requests", "طلبات الخدمة بين المكتب والشركات (إرسال وموافقة)", "Service requests between the office and companies"),
]
ALL = [p[0] for p in PERMISSIONS]
OWN_ONLY = "own_only"      # restriction: sees only what is assigned to him
VIEW_ONLY = "view_only"    # restriction: can look at everything he is allowed to, but change nothing

ROLES = {
    "admin": ("مدير النظام", "Administrator", ALL),
    "accountant": ("محاسب", "Accountant", [
        "clients_view", "employees_view", "tx_view", "money", "tasks", "sales_view",
        "sales_edit", "receipts", "expenses", "finance", "accounting", "accounting_edit",
        "templates", "export", "files", "salaries", "crm", "crm_reports", "renewals",
        "purchases", "inventory", "company_files", "requests"]),
    "rep": ("مندوب", "PRO / Representative", [
        "clients_view", "clients_edit", "employees_view", "employees_edit", "files",
        "tx_view", "tx_edit", "tasks", "templates", "crm", "renewals", "inbox", OWN_ONLY]),
    "custom": ("مخصص", "Custom", []),
}

# method name -> (permission, action, entity arabic, entity english, table for code)
GUARDED = {
    "save_client": ("clients_edit", "save", "عميل", "Client", "clients"),
    "delete_client": ("clients_delete", "delete", "عميل", "Client", "clients"),
    "save_owner": ("clients_edit", "save", "مالك", "Owner", None),
    "delete_owner": ("clients_edit", "delete", "مالك", "Owner", None),
    "save_document": ("clients_edit", "save", "مستند", "Document", None),
    "delete_document": ("clients_edit", "delete", "مستند", "Document", None),
    "save_employee": ("employees_edit", "save", "موظف", "Employee", "employees"),
    "delete_employee": ("employees_edit", "delete", "موظف", "Employee", "employees"),
    "add_file": ("files", "add", "ملف", "File", None),
    "delete_file": ("files", "delete", "ملف", "File", None),
    "update_file_category": ("files", "save", "ملف", "File", None),
    "save_transaction": ("tx_edit", "save", "معاملة", "Transaction", "transactions"),
    "delete_transaction": ("tx_delete", "delete", "معاملة", "Transaction", "transactions"),
    "add_payment": ("receipts", "add", "سند قبض", "Receipt", "payments"),
    "update_payment": ("receipts", "save", "سند قبض", "Receipt", "payments"),
    "delete_payment": ("sales_delete", "delete", "سند قبض", "Receipt", "payments"),
    "save_quotation": ("sales_edit", "save", "عرض سعر", "Quotation", "quotations"),
    "delete_quotation": ("sales_delete", "delete", "عرض سعر", "Quotation", "quotations"),
    "save_invoice": ("sales_edit", "save", "فاتورة", "Invoice", "invoices"),
    "delete_invoice": ("sales_delete", "delete", "فاتورة", "Invoice", "invoices"),
    "save_expense": ("expenses", "save", "مصروف", "Expense", "expenses"),
    "delete_expense": ("expenses", "delete", "مصروف", "Expense", "expenses"),
    "save_voucher": ("receipts", "save", "سند صرف", "Payment voucher", "vouchers"),
    "delete_voucher": ("sales_delete", "delete", "سند صرف", "Payment voucher", "vouchers"),
    "save_task": ("tasks", "save", "مهمة", "Task", None),
    "set_task_done": ("tasks", "save", "مهمة", "Task", None),
    "delete_task": ("tasks", "delete", "مهمة", "Task", None),
    "save_template": ("templates", "save", "قالب", "Template", None),
    "delete_template": ("templates", "delete", "قالب", "Template", None),
    "save_manual_entry": ("accounting_edit", "save", "قيد يومية", "Journal entry", None),
    "delete_entry": ("accounting_edit", "delete", "قيد يومية", "Journal entry", None),
    "save_account": ("accounting_edit", "save", "حساب", "Account", None),
    "delete_account": ("accounting_edit", "delete", "حساب", "Account", None),
    "rebuild_all_entries": ("accounting_edit", "rebuild", "القيود التلقائية", "Automatic entries", None),
    "restore_from": ("settings", "restore", "نسخة احتياطية", "Backup", None),
    "save_lead": ("crm", "save", "عميل محتمل", "Lead", "leads"),
    "receive_lead": ("crm", "add", "عميل محتمل (إضافة سريعة)", "Lead (quick add)", None),
    "set_lead_stage": ("crm", "update", "عميل محتمل", "Lead", "leads"),
    "add_activity": ("crm", "add", "متابعة عميل محتمل", "Lead activity", None),
    "convert_lead": ("crm", "convert", "عميل محتمل", "Lead", "leads"),
    "delete_lead": ("crm_delete", "delete", "عميل محتمل", "Lead", "leads"),
    "save_campaign": ("crm_campaigns", "save", "رسالة جماعية", "Group message", None),
    "save_item": ("inventory", "save", "صنف", "Item", "items"),
    "set_followup": ("renewals", "update", "متابعة تجديد", "Renewal follow-up", None),
    "delete_item": ("inventory", "delete", "صنف", "Item", "items"),
    "save_stock_adjustment": ("inventory", "save", "تسوية مخزون", "Stock adjustment", "stock_adjustments"),
    "delete_stock_adjustment": ("inventory", "delete", "تسوية مخزون", "Stock adjustment", "stock_adjustments"),
    "save_supplier": ("purchases", "save", "مورد", "Supplier", "suppliers"),
    "delete_supplier": ("purchases", "delete", "مورد", "Supplier", "suppliers"),
    "save_purchase_bill": ("purchases", "save", "فاتورة مشتريات", "Purchase bill", "purchase_bills"),
    "delete_purchase_bill": ("purchases", "delete", "فاتورة مشتريات", "Purchase bill", "purchase_bills"),
    "save_supplier_payment": ("purchases", "save", "سداد مورد", "Supplier payment", "supplier_payments"),
    "delete_supplier_payment": ("purchases", "delete", "سداد مورد", "Supplier payment", "supplier_payments"),
    "save_sales_invoice": ("sales_edit", "save", "فاتورة بيع", "Sales invoice", "sales_invoices"),
    "delete_sales_invoice": ("sales_delete", "delete", "فاتورة بيع", "Sales invoice", "sales_invoices"),
    "save_sales_receipt": ("receipts", "save", "سند قبض", "Receipt", "sales_receipts"),
    "delete_sales_receipt": ("sales_delete", "delete", "سند قبض", "Receipt", "sales_receipts"),
    "save_company_doc": ("company_files", "save", "ملف الشركة", "Company document", None),
    "delete_company_doc": ("company_files", "delete", "ملف الشركة", "Company document", None),
}

ACTIONS = {"add": ("إضافة", "Add"), "save": ("حفظ", "Save"), "create": ("إضافة", "Add"),
           "update": ("تعديل", "Edit"), "delete": ("حذف", "Delete"), "login": ("دخول", "Login"),
           "logout": ("خروج", "Logout"), "login_failed": ("محاولة دخول فاشلة", "Failed login"),
           "rebuild": ("إعادة ترحيل", "Rebuild"), "restore": ("استرجاع", "Restore"),
           "backup": ("نسخ احتياطي", "Backup"), "settings": ("تعديل الإعدادات", "Settings"),
           "password": ("تغيير كلمة السر", "Password change"),
           "convert": ("تحويل لعميل", "Convert to client")}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    full_name TEXT DEFAULT '', role TEXT DEFAULT 'custom', perms TEXT DEFAULT '[]',
    pw_hash TEXT NOT NULL, salt TEXT NOT NULL, active INTEGER DEFAULT 1,
    phone TEXT DEFAULT '', created TEXT, last_login TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, user_id INTEGER, username TEXT,
    action TEXT, entity TEXT, entity_id INTEGER, ref TEXT DEFAULT '', details TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""

EXTRA_COLUMNS = {
    "transactions": [("assigned_user_id", "INTEGER")],
    "tasks": [("assigned_user_id", "INTEGER"), ("created_by", "INTEGER")],
    "clients": [("created_by", "INTEGER")],
}


class NotAllowed(PermissionError):
    pass


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return h.hex(), salt


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _guard(name, fn):
    perm, action, ent_ar, ent_en, table = GUARDED[name]

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if self.user is not None and not self.can(perm):
            raise NotAllowed(perm)
        if self.user is not None and VIEW_ONLY in self._perms and self.user["role"] != "admin":
            raise NotAllowed(VIEW_ONLY)
        ref_id = None
        existing = False
        if name == "save_manual_entry":
            eid = args[3] if len(args) > 3 else kwargs.get("eid")
            if eid:
                ref_id, existing = eid, True
        elif name.startswith(("save_", "update_")) and len(args) >= 2 and args[1]:
            ref_id, existing = args[1], True
        elif name.startswith(("delete_", "set_task")) and args:
            ref_id = args[0]
        ref = self._audit_ref(table, ref_id) if (ref_id and table and name.startswith("delete_")) else ""
        if name == "restore_from":          # replaces the whole database file
            result = fn(self, *args, **kwargs)
            _after(self, name, args, kwargs, result, ref_id, ref, existing, action, ent_ar, ent_en, table)
            return result
        with self.atomic():
            result = fn(self, *args, **kwargs)
            _after(self, name, args, kwargs, result, ref_id, ref, existing, action, ent_ar, ent_en, table)
        return result
    return wrapper


def _after(self, name, args, kwargs, result, ref_id, ref, existing, action, ent_ar, ent_en, table):
        if self.user is not None:
            if ref_id is None and isinstance(result, int):
                ref_id = result
            if not ref and table and ref_id:
                ref = self._audit_ref(table, ref_id)
            act = action
            if action == "save":
                act = "update" if existing else "create"
            detail = ""
            if name == "add_file" and len(args) >= 3:
                detail = str(args[2]).replace("\\", "/").rsplit("/", 1)[-1]
            elif name == "save_client" and args and isinstance(args[0], dict):
                detail = args[0].get("name", "")
            elif name == "save_employee" and args and isinstance(args[0], dict):
                detail = args[0].get("name", "")
            elif name in ("add_payment",) and len(args) >= 2:
                detail = f"{float(args[1]):,.2f}"
            self.audit(act, f"{ent_ar}|{ent_en}", ref_id, ref, detail)


class UsersMixin:
    """Mixed into core.DB (before AccountingMixin so its wrappers see everything)."""
    user = None           # sqlite Row of the logged-in user, None = no checks (tests)
    _perms = frozenset()

    # ---------- setup ----------
    def _users_setup(self):
        self.con.executescript(SCHEMA)
        for table, cols in EXTRA_COLUMNS.items():
            have = {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}
            for n, decl in cols:
                if n not in have:
                    self.con.execute(f"ALTER TABLE {table} ADD COLUMN {n} {decl}")
        self._commit()

    @classmethod
    def install_guards(cls):
        for name in GUARDED:
            fn = getattr(cls, name, None)
            if fn and not getattr(fn, "_guarded", False):
                w = _guard(name, fn)
                w._guarded = True
                setattr(cls, name, w)

    # ---------- users ----------
    def has_users(self):
        return bool(self.one("SELECT 1 FROM users LIMIT 1"))

    def users(self):
        return self.q("SELECT * FROM users ORDER BY active DESC, role, username")

    def user_row(self, uid):
        return self.one("SELECT * FROM users WHERE id=?", (uid,))

    def reps(self):
        """Active users that transactions / tasks can be assigned to."""
        return self.q("SELECT id, username, full_name, role FROM users WHERE active=1 "
                      "ORDER BY full_name, username")

    def perms_of(self, row):
        if not row:
            return set()
        if row["role"] == "admin":
            return set(ALL)
        try:
            return set(json.loads(row["perms"] or "[]"))
        except ValueError:
            return set()

    def create_user(self, username, password, full_name="", role="custom", perms=None,
                    phone="", active=True, company_id=None):
        if self.master is not None:         # users live in the main database
            uid = self.master.create_user(username, password, full_name, role, perms, phone,
                                          active, company_id=self.company_id)
            self.sync_users()
            return uid
        self._need_users_perm()
        h, s = hash_password(password)
        perms = list(ROLES[role][2]) if perms is None and role in ROLES else list(perms or [])
        uid = self.x("INSERT INTO users(username, full_name, role, perms, pw_hash, salt, active, "
                     "phone, created) VALUES(?,?,?,?,?,?,?,?,?)",
                     (username.strip(), full_name.strip(), role, json.dumps(perms), h, s,
                      int(active), phone, _now()))
        self.audit("create", "مستخدم|User", uid, username, ROLES.get(role, ("",))[0])
        if hasattr(self, "grant_company"):
            self.grant_company(uid, company_id or 1)
        return uid

    def update_user(self, uid, full_name, role, perms, active, phone=""):
        if self.master is not None:
            self.master.update_user(uid, full_name, role, perms, active, phone)
            self.sync_users()
            if self.user is not None and self.user["id"] == uid:
                self.login_as(self.user_row(uid), log=False)
            return
        self._need_users_perm()
        old = self.user_row(uid)
        if old["role"] == "admin" and (role != "admin" or not active) and self._admin_count() <= 1:
            raise ValueError("last_admin")
        self.x("UPDATE users SET full_name=?, role=?, perms=?, active=?, phone=? WHERE id=?",
               (full_name.strip(), role, json.dumps(sorted(set(perms))), int(active), phone, uid))
        self.audit("update", "مستخدم|User", uid, old["username"],
                   ROLES.get(role, ("",))[0] + ("" if active else " - موقوف"))
        if self.user is not None and self.user["id"] == uid:
            self.login_as(self.user_row(uid), log=False)

    def set_password(self, uid, password):
        if self.master is not None:
            self.master.set_password(uid, password)
            self.sync_users()
            return
        if self.user is not None and self.user["id"] != uid:
            self._need_users_perm()
        h, s = hash_password(password)
        self.x("UPDATE users SET pw_hash=?, salt=? WHERE id=?", (h, s, uid))
        self.audit("password", "مستخدم|User", uid, self.user_row(uid)["username"])

    def delete_user(self, uid):
        if self.master is not None:
            self.master.delete_user(uid)
            self.sync_users()
            return
        self._need_users_perm()
        row = self.user_row(uid)
        if row["role"] == "admin" and self._admin_count() <= 1:
            raise ValueError("last_admin")
        if self.user is not None and self.user["id"] == uid:
            raise ValueError("self")
        self.x("DELETE FROM users WHERE id=?", (uid,))
        self.x("UPDATE transactions SET assigned_user_id=NULL WHERE assigned_user_id=?", (uid,))
        self.x("UPDATE tasks SET assigned_user_id=NULL WHERE assigned_user_id=?", (uid,))
        self.x("UPDATE leads SET assigned_user_id=NULL WHERE assigned_user_id=?", (uid,))
        if hasattr(self, "grant_company"):
            self.x("DELETE FROM user_companies WHERE user_id=?", (uid,))
        self.audit("delete", "مستخدم|User", uid, row["username"])

    def _admin_count(self):
        return self.one("SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1")["n"]

    def _need_users_perm(self):
        if self.user is not None and not self.can("users"):
            raise NotAllowed("users")

    # ---------- login ----------
    def check_login(self, username, password):
        row = self.one("SELECT * FROM users WHERE username=?", (username.strip(),))
        if row and row["active"]:
            h, _ = hash_password(password, row["salt"])
            if hmac.compare_digest(h, row["pw_hash"]):
                return row
        self.x("INSERT INTO audit_log(ts, user_id, username, action, entity, entity_id, ref, details) "
               "VALUES(?,?,?,?,?,?,?,?)", (_now(), None, username.strip(), "login_failed",
                                           "مستخدم|User", None, "", ""))
        return None

    def login_as(self, row, log=True):
        self.user = row
        self._perms = frozenset(self.perms_of(row))
        if log:
            self.x("UPDATE users SET last_login=? WHERE id=?", (_now(), row["id"]))
            self.audit("login", "مستخدم|User", row["id"], row["username"])

    def logout(self):
        if self.user is not None:
            self.audit("logout", "مستخدم|User", self.user["id"], self.user["username"])
        self.user = None
        self._perms = frozenset()

    def can(self, perm):
        if self.user is None:
            return True
        return perm in self._perms

    @property
    def own_only(self):
        return self.user is not None and OWN_ONLY in self._perms

    @property
    def view_only(self):
        return self.user is not None and VIEW_ONLY in self._perms and self.user["role"] != "admin"

    def make_recovery_code(self):
        code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        h, salt = hash_password(code)
        self.set("recovery_hash", f"{salt}:{h}")
        return code

    def check_recovery_code(self, code):
        stored = self.get("recovery_hash")
        if not stored or ":" not in stored:
            return False
        salt, h = stored.split(":", 1)
        return hmac.compare_digest(hash_password(code.strip().upper(), salt)[0], h)

    def admins(self):
        return self.q("SELECT * FROM users WHERE role='admin' ORDER BY id")

    # ---------- visibility for "own only" users ----------
    def visible_client_ids(self):
        """None = everything; otherwise the set of client ids this user may see."""
        if not self.own_only:
            return None
        uid = self.user["id"]
        return {r[0] for r in self.con.execute(
            """SELECT client_id FROM transactions WHERE assigned_user_id=?
               UNION SELECT id FROM clients WHERE created_by=?
               UNION SELECT client_id FROM tasks WHERE assigned_user_id=? AND client_id IS NOT NULL""",
            (uid, uid, uid)).fetchall()}

    # ---------- activity log ----------
    def audit(self, action, entity, entity_id=None, ref="", details=""):
        u = self.user
        self.con.execute("INSERT INTO audit_log(ts, user_id, username, action, entity, entity_id, "
                         "ref, details) VALUES(?,?,?,?,?,?,?,?)",
                         (_now(), u["id"] if u else None,
                          (u["full_name"] or u["username"]) if u else "", action, entity,
                          entity_id, ref or "", details or ""))
        self._commit()

    def _audit_ref(self, table, rid):
        try:
            r = self.one(f"SELECT code FROM {table} WHERE id=?", (rid,))
            return r["code"] if r and r["code"] else ""
        except Exception:        # noqa: BLE001
            return ""

    def audit_rows(self, date_from=None, date_to=None, user_id=None, search=""):
        sql = "SELECT * FROM audit_log WHERE 1=1"
        args = []
        if date_from:
            sql += " AND ts>=?"
            args.append(date_from)
        if date_to:
            sql += " AND ts<=?"
            args.append(date_to + " 23:59:59")
        if user_id:
            sql += " AND user_id=?"
            args.append(user_id)
        if search:
            s = f"%{search.strip()}%"
            sql += " AND (username LIKE ? OR entity LIKE ? OR ref LIKE ? OR details LIKE ?)"
            args += [s] * 4
        return self.q(sql + " ORDER BY id DESC LIMIT 5000", args)
