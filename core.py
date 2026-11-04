# -*- coding: utf-8 -*-
"""
طبقة البيانات لبرنامج المناديب المبدعون - متابعة المعاملات والعملاء
Data layer (SQLite + files folder) - no GUI code here.
"""
import json
import os
import sys
from contextlib import contextmanager
import re
import shutil
import sqlite3
import zipfile
import datetime as dt
from pathlib import Path

from accounting import AccountingMixin
from users import UsersMixin
from crm import CRMMixin
from inbox import InboxMixin
from renewals import RenewalsMixin, phone_digits  # noqa: F401
from business import BusinessMixin, PREFIXES as BUSINESS_PREFIXES
from companies import CompaniesMixin, SHARED_SETTINGS, currency_info, CURRENCIES  # noqa: F401

from edition import GENERIC

if GENERIC:
    APP_NAME = "PROManagement"
    APP_TITLE_AR = "PRO Management Services"
    APP_TITLE_EN = "PRO Management Services"
else:
    APP_NAME = "CreativeReps"
    APP_TITLE_AR = "المناديب المبدعون - إدارة المعاملات والعملاء"
    APP_TITLE_EN = "Creative Reps - Clients & Transactions"

CANCELLED = "ملغاة|Cancelled"

# ----------------------------------------------------------------------
# Default settings  (every list item is "عربي|English")
# ----------------------------------------------------------------------
DEFAULTS = {
    "language": "ar",
    "alert_days": "30",
    "due_alert_days": "3",
    "company_name_ar": "",
    "company_name_en": "",
    "company_cr": "",
    "company_license": "",
    "company_phone": "",
    "company_email": "",
    "company_address": "",
    "company_country": "",
    "currency": "QAR",
    "builtin_brand": "0" if GENERIC else "1",
    "company_bank": "",
    "company_iban": "",
    "logo_path": "",
    "letterhead_pdf": "",
    "idle_minutes": "15",
    "auto_backup_days": "7",
    "ai_model": "claude-sonnet-5-5",
    "use_letterhead": "1",
    "lh_top_mm": "46",
    "lh_bottom_mm": "30",
    "lh_side_mm": "18",
    "quote_terms": "50% مقدمًا والباقي عند الإنجاز.\nالرسوم الحكومية تُدفع حسب الإيصالات الرسمية.",
    "invoice_notes": "شكرًا لتعاملكم معنا.",
    "tx_types": "\n".join([
        "تأسيس شركة|Company Formation",
        "إصدار سجل تجاري|New Commercial Registration",
        "تجديد سجل تجاري|CR Renewal",
        "تعديل سجل تجاري|CR Amendment",
        "رخصة تجارية|Trade License",
        "تجديد رخصة تجارية|Trade License Renewal",
        "بطاقة منشأة (قيد)|Establishment Card",
        "تأشيرات|Visas",
        "إصدار / تجديد إقامة|QID Issue / Renewal",
        "فحص طبي|Medical Check",
        "عقود عمل|Work Contracts",
        "ترجمة|Translation",
        "أخرى|Other",
    ]),
    "stages": "\n".join([
        "استلام المستندات|Documents Received",
        "التقديم للجهة|Submitted",
        "قيد المراجعة|Under Review",
        "مطلوب استكمال نواقص|Missing Documents",
        "تمت الموافقة|Approved",
        "التسليم للعميل|Delivered to Client",
    ]),
    "doc_types": "\n".join([
        "عقد إيجار|Lease Contract",
        "شهادة الدفاع المدني|Civil Defense Certificate",
        "شهادة تسجيل ضريبي|Tax Registration",
        "رخصة قيادة|Driving License",
        "أخرى|Other",
    ]),
    "file_cats": "\n".join([
        "سجل تجاري|Commercial Registration",
        "رخصة تجارية|Trade License",
        "قيد المنشأة|Establishment Card",
        "البطاقة الشخصية|QID",
        "جواز السفر|Passport",
        "الشهادة الصحية / الفحص الطبي|Medical Certificate",
        "صورة شخصية|Personal Photo",
        "عقد العمل|Employment Contract",
        "عقد تأسيس|Articles of Association",
        "عقد إيجار|Lease Contract",
        "فاتورة / إيصال|Invoice / Receipt",
        "أخرى|Other",
    ]),
    "entities": "\n".join([
        "وزارة التجارة والصناعة|Ministry of Commerce and Industry",
        "وزارة البلدية|Ministry of Municipality",
        "وزارة الداخلية|Ministry of Interior",
        "وزارة العمل|Ministry of Labour",
        "وزارة الصحة العامة|Ministry of Public Health",
        "أخرى|Other",
    ]),
    "pay_methods": "\n".join([
        "نقدي|Cash",
        "تحويل بنكي|Bank Transfer",
        "شيك|Cheque",
        "بطاقة|Card",
    ]),
    "expense_cats": "\n".join([
        "رسوم حكومية|Government Fees",
        "مواصلات وبنزين|Transport & Fuel",
        "رواتب|Salaries",
        "إيجار المكتب|Office Rent",
        "اشتراكات وبرامج|Subscriptions",
        "طباعة وقرطاسية|Printing & Stationery",
        "أخرى|Other",
    ]),
}

# Default message / letter templates  (title, category, channel, body)
BLANK_LETTER = ("خطاب حر (تكتبه بنفسك)", "letter", "letter",
                "التاريخ: {date}\n\nالسادة/ ........................ المحترمين،\nتحية طيبة وبعد،\n\n"
                "الموضوع: ........................\n\n\n\n\nوتفضلوا بقبول فائق الاحترام،\n{company}")

DEFAULT_TEMPLATES = [
    BLANK_LETTER,
    ("تذكير بانتهاء مستند", "reminder", "whatsapp",
     "السلام عليكم {client}،\nنود تذكيركم بأن {doc} الخاص بكم ينتهي بتاريخ {expiry}.\n"
     "يسعدنا نتولى التجديد عنكم قبل الموعد لتجنب أي غرامات أو تأخير.\n\n{company}"),
    ("طلب مستندات", "followup", "whatsapp",
     "السلام عليكم {client}،\nلإتمام معاملة {tx_code} ({tx_type})، نرجو تزويدنا بالمستندات المطلوبة "
     "في أقرب وقت حتى نستكمل الإجراءات.\n\nشكرًا لتعاونكم.\n{company}"),
    ("تحديث حالة معاملة", "followup", "whatsapp",
     "السلام عليكم {client}،\nنود إفادتكم بأن معاملة {tx_code} ({tx_type}) وصلت إلى مرحلة: {status}.\n"
     "وسنوافيكم بأي جديد أولًا بأول.\n\n{company}"),
    ("إتمام معاملة", "general", "whatsapp",
     "السلام عليكم {client}،\nيسرنا إبلاغكم بأن معاملة {tx_code} ({tx_type}) تمت بنجاح ✅.\n"
     "شكرًا لثقتكم بنا.\n\n{company}"),
    ("طلب سداد مستحقات", "payment", "whatsapp",
     "السلام عليكم {client}،\nنود تذكيركم بوجود مبلغ متبقٍ قدره {remaining} ر.ق.\n"
     "شاكرين حسن تعاونكم.\n\n{company}"),
    ("رسالة شكر", "general", "whatsapp",
     "شكرًا جزيلًا {client} على ثقتكم بنا 🌟\nنتطلع دائمًا لخدمتكم.\n\n{company}"),
    ("عرض خدمة", "general", "whatsapp",
     "السلام عليكم {client}،\nيسعدنا في {company} تقديم خدمات تأسيس الشركات، تجديد السجلات والرخص، "
     "وخدمات المندوبين والعلاقات العامة بسرعة وأمانة.\nتواصلوا معنا لعرض سعر مناسب."),
    ("خطاب لجهة حكومية", "letter", "letter",
     "السادة/ {entity} المحترمين،\nتحية طيبة وبعد،\n\nالموضوع: بخصوص معاملة {tx_type}\n\n"
     "بالإشارة إلى الموضوع أعلاه، وبصفتنا مفوضين عن {client} (سجل تجاري رقم: {cr})، "
     "نرجو التكرم بالنظر في الطلب المرفق واتخاذ اللازم بشأنه.\n\n"
     "شاكرين لكم حسن تعاونكم.\n\nوتفضلوا بقبول فائق الاحترام،\n{company}"),
    ("خطاب رسمي للعميل", "letter", "letter",
     "السادة/ {client} المحترمين،\nتحية طيبة وبعد،\n\nالموضوع: بخصوص {tx_type}\n\n"
     "نشكركم على تعاملكم مع {company}، ونود إحاطتكم علمًا بأن معاملتكم رقم {tx_code} "
     "وصلت إلى مرحلة: {status}.\n\nوتفضلوا بقبول فائق الاحترام،\n{company}"),
    ("عقد تقديم خدمة", "contract", "letter",
     "عقد تقديم خدمات\n\nإنه في يوم {date} تم الاتفاق بين:\nالطرف الأول: {company}\n"
     "الطرف الثاني: {client} (سجل تجاري رقم: {cr})\n\n"
     "على أن يقدم الطرف الأول خدمة ({tx_type}) مقابل مبلغ وقدره {amount} ر.ق.\n\n"
     "يلتزم الطرف الأول بإنجاز الخدمة المتفق عليها ضمن المدة المحددة، ويلتزم الطرف الثاني "
     "بسداد المبلغ وتقديم المستندات المطلوبة في وقتها.\n\n"
     "حُرر هذا العقد من نسختين بيد كل طرف نسخة للعمل بموجبها.\n\n"
     "الطرف الأول: ....................        الطرف الثاني: ...................."),
]

PREFIX = {"clients": "CUS", "employees": "EMP", "transactions": "TRX",
          "quotations": "QUO", "invoices": "INV", "payments": "RCV",
          "expenses": "EXP", "vouchers": "PAY", "leads": "LEAD"}
PREFIX.update(BUSINESS_PREFIXES)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT DEFAULT 'company',
    phone TEXT DEFAULT '', email TEXT DEFAULT '',
    id_number TEXT DEFAULT '', nationality TEXT DEFAULT '',
    notes TEXT DEFAULT '', created TEXT
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    doc_type TEXT, doc_number TEXT DEFAULT '', holder TEXT DEFAULT '',
    expiry TEXT, notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS owners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name TEXT NOT NULL, nationality TEXT DEFAULT '', phone TEXT DEFAULT '',
    qid_number TEXT DEFAULT '', qid_expiry TEXT DEFAULT '',
    passport_number TEXT DEFAULT '', passport_expiry TEXT DEFAULT '',
    share TEXT DEFAULT '', notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    code TEXT, name TEXT NOT NULL, job_title TEXT DEFAULT '',
    nationality TEXT DEFAULT '', dob TEXT DEFAULT '', phone TEXT DEFAULT '',
    qid_number TEXT DEFAULT '', qid_expiry TEXT DEFAULT '',
    passport_number TEXT DEFAULT '', passport_expiry TEXT DEFAULT '',
    health_number TEXT DEFAULT '', health_expiry TEXT DEFAULT '',
    contract_start TEXT DEFAULT '', contract_expiry TEXT DEFAULT '',
    bank_name TEXT DEFAULT '', bank_account TEXT DEFAULT '', iban TEXT DEFAULT '',
    salary REAL DEFAULT 0, status TEXT DEFAULT 'active',
    notes TEXT DEFAULT '', created TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_type TEXT NOT NULL, owner_id INTEGER NOT NULL,
    client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    category TEXT DEFAULT '', path TEXT NOT NULL,
    original_name TEXT DEFAULT '', size INTEGER DEFAULT 0,
    added TEXT, note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    tx_type TEXT, entity TEXT DEFAULT '', status TEXT,
    start_date TEXT, due_date TEXT, fee REAL DEFAULT 0,
    zoho_invoice TEXT DEFAULT '', notes TEXT DEFAULT '',
    created TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
    amount REAL NOT NULL, pay_date TEXT,
    method TEXT DEFAULT '', note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tx_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    ts TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS quotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    date TEXT, valid_days INTEGER DEFAULT 15, items TEXT DEFAULT '[]',
    gov_fees REAL DEFAULT 0, discount REAL DEFAULT 0,
    terms TEXT DEFAULT '', notes TEXT DEFAULT '', status TEXT DEFAULT 'draft',
    created TEXT
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    tx_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    quote_id INTEGER REFERENCES quotations(id) ON DELETE SET NULL,
    date TEXT, due_date TEXT, items TEXT DEFAULT '[]',
    gov_fees REAL DEFAULT 0, discount REAL DEFAULT 0,
    notes TEXT DEFAULT '', status TEXT DEFAULT 'issued', created TEXT
);
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT,
    date TEXT, category TEXT DEFAULT '', description TEXT DEFAULT '',
    amount REAL DEFAULT 0, method TEXT DEFAULT '',
    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    tx_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    note TEXT DEFAULT '', created TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
    client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    tx_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    due_date TEXT, priority TEXT DEFAULT 'medium', status TEXT DEFAULT 'todo',
    notes TEXT DEFAULT '', created TEXT, done_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
    category TEXT DEFAULT 'general', channel TEXT DEFAULT 'whatsapp',
    body TEXT DEFAULT ''
);
"""

# columns added on top of the first version (safe to run many times)
EXTRA_COLUMNS = {
    "clients": [
        ("code", "TEXT"), ("name_en", "TEXT DEFAULT ''"),
        ("phone2", "TEXT DEFAULT ''"), ("contact_person", "TEXT DEFAULT ''"),
        ("address", "TEXT DEFAULT ''"), ("id_expiry", "TEXT DEFAULT ''"),
        ("cr_number", "TEXT DEFAULT ''"), ("cr_expiry", "TEXT DEFAULT ''"),
        ("license_number", "TEXT DEFAULT ''"), ("license_expiry", "TEXT DEFAULT ''"),
        ("est_number", "TEXT DEFAULT ''"), ("est_expiry", "TEXT DEFAULT ''"),
        ("bank_name", "TEXT DEFAULT ''"), ("bank_account", "TEXT DEFAULT ''"),
        ("iban", "TEXT DEFAULT ''"), ("status", "TEXT DEFAULT 'active'"),
    ],
    "transactions": [
        ("code", "TEXT"), ("employee_id", "INTEGER"),
        ("gov_fees", "REAL DEFAULT 0"),
    ],
    "payments": [
        ("code", "TEXT"), ("client_id", "INTEGER"), ("invoice_id", "INTEGER"),
    ],
}

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_docs_expiry ON documents(expiry);
CREATE INDEX IF NOT EXISTS idx_tx_client ON transactions(client_id);
CREATE INDEX IF NOT EXISTS idx_pay_tx ON payments(tx_id);
CREATE INDEX IF NOT EXISTS idx_pay_inv ON payments(invoice_id);
CREATE INDEX IF NOT EXISTS idx_emp_client ON employees(client_id);
CREATE INDEX IF NOT EXISTS idx_own_client ON owners(client_id);
CREATE INDEX IF NOT EXISTS idx_files_owner ON files(owner_type, owner_id);
"""

CLIENT_FIELDS = ("name", "name_en", "kind", "status", "phone", "phone2", "email",
                 "contact_person", "nationality", "address", "id_number",
                 "id_expiry", "cr_number", "cr_expiry", "license_number",
                 "license_expiry", "est_number", "est_expiry", "bank_name",
                 "bank_account", "iban", "notes")
OWNER_FIELDS = ("client_id", "name", "nationality", "phone", "qid_number",
                "qid_expiry", "passport_number", "passport_expiry", "share",
                "notes")
EMPLOYEE_FIELDS = ("client_id", "name", "job_title", "nationality", "dob",
                   "phone", "qid_number", "qid_expiry", "passport_number",
                   "passport_expiry", "health_number", "health_expiry",
                   "contract_start", "contract_expiry", "bank_name",
                   "bank_account", "iban", "salary", "status", "notes")

# expiry fields that feed the alerts:  (table, field, label, number_field)
EXPIRY_SOURCES = [
    ("clients", "cr_expiry", "السجل التجاري|Commercial Registration", "cr_number"),
    ("clients", "license_expiry", "الرخصة التجارية|Trade License", "license_number"),
    ("clients", "est_expiry", "قيد المنشأة|Establishment Card", "est_number"),
    ("clients", "id_expiry", "البطاقة الشخصية|QID", "id_number"),
    ("owners", "qid_expiry", "البطاقة الشخصية (مالك)|Owner QID", "qid_number"),
    ("owners", "passport_expiry", "جواز السفر (مالك)|Owner Passport", "passport_number"),
    ("employees", "qid_expiry", "البطاقة الشخصية|QID", "qid_number"),
    ("employees", "passport_expiry", "جواز السفر|Passport", "passport_number"),
    ("employees", "health_expiry", "الشهادة الصحية|Health Certificate", "health_number"),
    ("employees", "contract_expiry", "عقد العمل|Work Contract", ""),
]


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _old_data_dir() -> Path:
    """Version 1-3 kept the data per Windows user."""
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / APP_NAME


def data_dir() -> Path:
    """Shared by every Windows user of this computer (C:\\ProgramData\\CreativeReps)."""
    override = os.environ.get("PROMS_DATA") or os.environ.get("CREATIVEREPS_DATA")
    if override:
        d = Path(override)
    elif sys.platform == "win32" and os.environ.get("PROGRAMDATA"):
        d = Path(os.environ["PROGRAMDATA"]) / APP_NAME
    else:
        d = _old_data_dir()
    first = not d.exists()
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32" and (first or not (d / ".shared").exists()):
        _share_folder(d)
    _move_old_data(d)
    return d


def _share_folder(d: Path):
    """Let every Windows user on this PC read and write the data folder."""
    try:
        import subprocess
        subprocess.run(["icacls", str(d), "/grant", "*S-1-5-32-545:(OI)(CI)M", "/T", "/Q"],
                       capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        (d / ".shared").write_text("ok", encoding="utf-8")
    except Exception:       # noqa: BLE001
        pass


def _move_old_data(d: Path):
    """Copy the data of the older per-user location to the shared one (once)."""
    old = _old_data_dir()
    if old.resolve() == d.resolve() or not (old / "data.db").exists() or (d / "data.db").exists():
        return
    for item in old.iterdir():
        if item.name.startswith("_tmp") or item.name.endswith(".lock"):
            continue
        target = d / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        except OSError:
            pass
    try:
        (old / "تم نقل البيانات - MOVED.txt").write_text(
            f"البيانات اتنقلت إلى / Data moved to:\n{d}\n", encoding="utf-8")
    except OSError:
        pass


def today() -> dt.date:
    return dt.date.today()


def iso(d: dt.date) -> str:
    return d.isoformat()


def parse_date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def days_left(s):
    d = parse_date(s)
    return (d - today()).days if d else None


def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def split_label(value: str, lang: str) -> str:
    """'عربي|English' -> the part for the current language."""
    if not value:
        return ""
    parts = str(value).split("|", 1)
    if lang == "en" and len(parts) == 2 and parts[1].strip():
        return parts[1].strip()
    return parts[0].strip()


def safe_name(s: str, limit=60) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", str(s or "")).strip(" .")
    s = re.sub(r"\s+", " ", s)
    return (s[:limit].strip() or "file")


def items_total(items):
    total = 0.0
    for it in items or []:
        try:
            total += float(it.get("qty", 1) or 0) * float(it.get("price", 0) or 0)
        except (TypeError, ValueError):
            pass
    return round(total, 2)


def doc_total(row):
    items = json.loads(row["items"] or "[]")
    return round(items_total(items) + float(row["gov_fees"] or 0)
                 - float(row["discount"] or 0), 2)


class DB(UsersMixin, CompaniesMixin, RenewalsMixin, BusinessMixin, CRMMixin, InboxMixin, AccountingMixin):
    def __init__(self, path=None):
        self.path = Path(path) if path else data_dir() / "data.db"
        self.con = None
        self.open()

    # ---------- connection & migration ----------
    def open(self):
        self.con = sqlite3.connect(str(self.path))
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.executescript(SCHEMA)
        self._migrate()
        self.con.executescript(INDEXES)
        for k, v in DEFAULTS.items():
            self.con.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
        # new list items added in this version for users of version 1
        if self.con.execute("SELECT COUNT(*) FROM templates").fetchone()[0] == 0:
            self.con.executemany(
                "INSERT INTO templates(title, category, channel, body) VALUES(?,?,?,?)",
                DEFAULT_TEMPLATES)
        if not self.con.execute("SELECT 1 FROM templates WHERE title=?", (BLANK_LETTER[0],)).fetchone():
            self.con.execute("INSERT INTO templates(title, category, channel, body) VALUES(?,?,?,?)",
                             BLANK_LETTER)
        self.con.commit()
        self._users_setup()
        self._companies_setup()
        self._assign_missing_codes()
        self._acc_setup()
        self._business_setup()
        self._renewals_setup()
        self._crm_setup()
        self._inbox_setup()
        self._assign_missing_codes()
        self._fix_moved_paths()

    def _fix_moved_paths(self):
        """Settings that point into the old per-user folder are pointed at the new one."""
        old = str(_old_data_dir())
        new = str(self.path.parent)
        if old == new:
            return
        for key in ("logo_path", "letterhead_pdf", "files_root"):
            v = self.get(key)
            if v and v.startswith(old):
                self.set(key, new + v[len(old):])

    # ---------- atomic changes ----------
    _depth = 0

    def _commit(self):
        if not self._depth:
            self.con.commit()

    @contextmanager
    def atomic(self):
        """Everything inside either fully happens or not at all."""
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            if not self._depth:
                self.con.rollback()
            raise
        self._depth -= 1
        if not self._depth:
            self.con.commit()

    def _columns(self, table):
        return {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}

    def _migrate(self):
        # payments.tx_id was NOT NULL in version 1 -> rebuild table once
        info = {r[1]: r for r in self.con.execute("PRAGMA table_info(payments)")}
        if info.get("tx_id") and info["tx_id"][3] == 1:
            self.con.execute("PRAGMA foreign_keys = OFF")
            self.con.executescript("""
                ALTER TABLE payments RENAME TO payments_v1;
                CREATE TABLE payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tx_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
                    amount REAL NOT NULL, pay_date TEXT,
                    method TEXT DEFAULT '', note TEXT DEFAULT '');
                INSERT INTO payments(id, tx_id, amount, pay_date, method, note)
                    SELECT id, tx_id, amount, pay_date, method, note FROM payments_v1;
                DROP TABLE payments_v1;
            """)
            self.con.execute("PRAGMA foreign_keys = ON")
        for table, cols in EXTRA_COLUMNS.items():
            have = self._columns(table)
            for name, decl in cols:
                if name not in have:
                    self.con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        # fill client_id on payments from their transaction
        self.con.execute("""UPDATE payments SET client_id =
            (SELECT client_id FROM transactions t WHERE t.id = payments.tx_id)
            WHERE client_id IS NULL AND tx_id IS NOT NULL""")
        # version 1 kept the CR number of companies in id_number
        self.con.execute("""UPDATE clients SET cr_number = id_number, id_number = ''
            WHERE kind = 'company' AND COALESCE(cr_number,'') = ''
              AND COALESCE(id_number,'') <> ''""")
        self.con.commit()

    def _assign_missing_codes(self):
        have = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("clients", "employees", "transactions", "payments",
                      "quotations", "invoices", "expenses", "vouchers", "leads"):
            if table not in have:
                continue
            for r in self.q(f"SELECT id FROM {table} WHERE code IS NULL OR code='' "
                            f"ORDER BY id"):
                self.x(f"UPDATE {table} SET code=? WHERE id=?",
                       (self.next_code(table), r["id"]))

    def next_code(self, table):
        """Serial numbers that never repeat, e.g. CUS-00001."""
        prefix = PREFIX[table]
        key = f"seq_{table}"
        cur = self.get_int(key, 0)
        # never go below the highest number already used
        mx = 0
        for r in self.q(f"SELECT code FROM {table} WHERE code LIKE ?", (prefix + "-%",)):
            m = re.search(r"(\d+)$", r["code"] or "")
            if m:
                mx = max(mx, int(m.group(1)))
        n = max(cur, mx) + 1
        self.set(key, n)
        return f"{prefix}-{n:05d}"

    def close(self):
        if self.con:
            self.con.close()
            self.con = None

    def q(self, sql, args=()):
        return self.con.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        return self.con.execute(sql, args).fetchone()

    def x(self, sql, args=()):
        cur = self.con.execute(sql, args)
        self._commit()
        return cur.lastrowid

    def _save(self, table, fields, data, rid=None, code=False, stamp=None):
        vals = [data.get(f, "") if data.get(f) is not None else "" for f in fields]
        if rid:
            self.x(f"UPDATE {table} SET {', '.join(f + '=?' for f in fields)} "
                   f"WHERE id=?", (*vals, rid))
            return rid
        cols, args = list(fields), list(vals)
        if code:
            cols.append("code")
            args.append(self.next_code(table))
        if stamp:
            cols.append(stamp)
            args.append(now_str())
        return self.x(f"INSERT INTO {table}({', '.join(cols)}) "
                      f"VALUES({', '.join('?' * len(cols))})", args)

    # ---------- settings ----------
    def get(self, key):
        if self.master is not None and key in SHARED_SETTINGS:
            return self.master.get(key)
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return r["value"] if r and r["value"] is not None else DEFAULTS.get(key, "")

    def set(self, key, value):
        if self.master is not None and key in SHARED_SETTINGS:
            return self.master.set(key, value)
        self.x("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
               (key, str(value)))

    def get_list(self, key):
        return [ln.strip() for ln in self.get(key).splitlines() if ln.strip()]

    def get_int(self, key, default=0):
        try:
            return int(self.get(key))
        except (TypeError, ValueError):
            return default

    def company_name(self, lang="ar"):
        if lang == "en":
            return self.get("company_name_en") or self.get("company_name_ar")
        return self.get("company_name_ar") or self.get("company_name_en")

    # ---------- stages ----------
    def final_stage(self):
        st = self.get_list("stages")
        return st[-1] if st else ""

    def is_open_status(self, status):
        return status not in (self.final_stage(), CANCELLED)

    # ---------- clients ----------
    def save_client(self, data, cid=None):
        new = self._save("clients", CLIENT_FIELDS, data, cid, code=True, stamp="created")
        if not cid and self.user is not None:
            self.x("UPDATE clients SET created_by=? WHERE id=?", (self.user["id"], new))
        return new

    def _visible(self, rows, key="id"):
        ids = self.visible_client_ids()
        if ids is None:
            return rows
        return [r for r in rows if r[key] in ids]

    def delete_client(self, cid):
        if self.client_financial_count(cid):
            raise ValueError("has_financials")
        folder = self.client_folder(cid, create=False)
        for table, src in (("transactions", "transaction"), ("invoices", "invoice"),
                           ("payments", "payment")):
            for r in self.q(f"SELECT id FROM {table} WHERE client_id=?", (cid,)):
                self._clear_source(src, r["id"])
        self.con.execute("UPDATE jlines SET client_id=NULL WHERE client_id=?", (cid,))
        self.x("DELETE FROM clients WHERE id=?", (cid,))
        for r in self.q("SELECT id FROM expenses WHERE client_id IS NULL"):
            self.post_expense(r["id"])
        if folder and folder.exists():
            trash = self.files_root() / "_محذوفات_Deleted"
            trash.mkdir(exist_ok=True)
            target = trash / f"{folder.name} ({dt.datetime.now():%Y%m%d_%H%M%S})"
            try:
                shutil.move(str(folder), str(target))
            except OSError:
                pass

    def find_duplicate_client(self, cr_number="", id_number="", exclude=None):
        """Another client with the same CR or QID number (or None)."""
        for field, value in (("cr_number", cr_number), ("id_number", id_number)):
            v = (value or "").strip()
            if v:
                r = self.one(f"SELECT id, code, name FROM clients WHERE TRIM({field})=? AND id<>?",
                             (v, exclude or 0))
                if r:
                    return r
        return None

    def find_duplicate_employee(self, qid="", passport="", exclude=None):
        for field, value in (("qid_number", qid), ("passport_number", passport)):
            v = (value or "").strip()
            if v:
                r = self.one(f"""SELECT e.id, e.code, e.name, c.name AS client_name FROM employees e
                                 JOIN clients c ON c.id=e.client_id
                                 WHERE TRIM(e.{field})=? AND e.id<>?""", (v, exclude or 0))
                if r:
                    return r
        return None

    def client_financial_count(self, cid):
        """Transactions with money, invoices, quotations and receipts of a client."""
        r = self.one("""SELECT
            (SELECT COUNT(*) FROM transactions WHERE client_id=? AND (fee>0 OR gov_fees>0)) +
            (SELECT COUNT(*) FROM invoices WHERE client_id=?) +
            (SELECT COUNT(*) FROM payments WHERE client_id=?) +
            (SELECT COUNT(*) FROM vouchers WHERE client_id=?) AS n""", (cid, cid, cid, cid))
        return r["n"]

    def clients(self, search="", kind=None):
        s = f"%{search.strip()}%"
        final = self.final_stage()
        sql = """
            SELECT c.*,
              (SELECT COUNT(*) FROM employees e WHERE e.client_id=c.id
                 AND e.status='active') AS emp_count,
              (SELECT COUNT(*) FROM files f WHERE f.client_id=c.id) AS file_count,
              (SELECT COUNT(*) FROM transactions t WHERE t.client_id=c.id
                 AND t.status NOT IN (?, ?)) AS open_tx
            FROM clients c
            WHERE (c.name LIKE ? OR c.code LIKE ? OR c.phone LIKE ? OR c.email LIKE ?
               OR c.id_number LIKE ? OR c.cr_number LIKE ? OR c.license_number LIKE ?
               OR c.est_number LIKE ? OR c.name_en LIKE ? OR c.contact_person LIKE ?)
        """
        args = [final, CANCELLED] + [s] * 10
        if kind:
            sql += " AND c.kind=?"
            args.append(kind)
        return self._visible(self.q(sql + " ORDER BY c.code", args))

    def client(self, cid):
        return self.one("SELECT * FROM clients WHERE id=?", (cid,))

    def client_choices(self):
        return self._visible(self.q("SELECT id, code, name, phone FROM clients "
                                    "ORDER BY name COLLATE NOCASE"))

    # ---------- owners ----------
    def save_owner(self, data, oid=None):
        return self._save("owners", OWNER_FIELDS, data, oid)

    def owners(self, client_id):
        return self.q("SELECT * FROM owners WHERE client_id=? ORDER BY id", (client_id,))

    def owner(self, oid):
        return self.one("SELECT * FROM owners WHERE id=?", (oid,))

    def delete_owner(self, oid):
        self._delete_owned_files("owner", oid)
        self.x("DELETE FROM owners WHERE id=?", (oid,))

    # ---------- employees ----------
    def save_employee(self, data, eid=None):
        d = dict(data)
        try:
            d["salary"] = float(d.get("salary") or 0)
        except (TypeError, ValueError):
            d["salary"] = 0.0
        d["status"] = d.get("status") or "active"
        return self._save("employees", EMPLOYEE_FIELDS, d, eid, code=True,
                          stamp="created")

    def employees(self, client_id=None, search="", status="active"):
        s = f"%{search.strip()}%"
        sql = """SELECT e.*, c.name AS client_name, c.code AS client_code,
                   (SELECT COUNT(*) FROM files f WHERE f.owner_type='employee'
                      AND f.owner_id=e.id) AS file_count
                 FROM employees e JOIN clients c ON c.id=e.client_id
                 WHERE (e.name LIKE ? OR e.code LIKE ? OR e.qid_number LIKE ?
                    OR e.passport_number LIKE ? OR e.phone LIKE ? OR c.name LIKE ?
                    OR e.job_title LIKE ?)"""
        args = [s] * 7
        if client_id:
            sql += " AND e.client_id=?"
            args.append(client_id)
        if status in ("active", "left"):
            sql += " AND e.status=?"
            args.append(status)
        return self._visible(self.q(sql + " ORDER BY c.code, e.code", args), "client_id")

    def employee(self, eid):
        return self.one("SELECT * FROM employees WHERE id=?", (eid,))

    def delete_employee(self, eid):
        self._delete_owned_files("employee", eid)
        self.x("DELETE FROM employees WHERE id=?", (eid,))

    # ---------- documents (other documents with expiry) ----------
    def save_document(self, data, did=None):
        return self._save("documents", ("client_id", "doc_type", "doc_number",
                                         "holder", "expiry", "notes"), data, did)

    def delete_document(self, did):
        self.x("DELETE FROM documents WHERE id=?", (did,))

    def documents(self, client_id):
        return self.q("SELECT * FROM documents WHERE client_id=? ORDER BY expiry",
                      (client_id,))

    def document(self, did):
        return self.one("SELECT * FROM documents WHERE id=?", (did,))

    # ---------- expiry alerts (everything in one list) ----------
    def expiring_items(self, days=None, client_id=None):
        """Every expiry date (client papers, owners, employees, other documents)
        that is expired or ends within `days` days."""
        days = self.get_int("alert_days", 30) if days is None else days
        limit = iso(today() + dt.timedelta(days=days))
        out = []
        for table, field, label, numf in EXPIRY_SOURCES:
            num_sql = f"x.{numf}" if numf else "''"
            if table == "clients":
                sql = (f"SELECT x.id AS ref_id, x.id AS client_id, x.name AS client_name,"
                       f" x.phone AS phone, x.name AS holder, {num_sql} AS number,"
                       f" x.{field} AS expiry FROM clients x WHERE 1=1")
            else:
                extra = " AND x.status='active'" if table == "employees" else ""
                sql = (f"SELECT x.id AS ref_id, c.id AS client_id, c.name AS client_name,"
                       f" c.phone AS phone, x.name AS holder, {num_sql} AS number,"
                       f" x.{field} AS expiry FROM {table} x JOIN clients c"
                       f" ON c.id=x.client_id WHERE 1=1{extra}")
            sql += f" AND x.{field} IS NOT NULL AND x.{field}<>'' AND x.{field}<=?"
            args = [limit]
            if client_id:
                sql += " AND x.id=?" if table == "clients" else " AND c.id=?"
                args.append(client_id)
            source = {"clients": "client", "owners": "owner",
                      "employees": "employee"}[table]
            for r in self.q(sql, args):
                d = dict(r)
                d.update(source=source, doc_type=label)
                out.append(d)
        sql = """SELECT d.id AS ref_id, c.id AS client_id, c.name AS client_name,
                   c.phone AS phone, COALESCE(NULLIF(d.holder,''), c.name) AS holder,
                   d.doc_number AS number, d.expiry AS expiry, d.doc_type AS doc_type
                 FROM documents d JOIN clients c ON c.id=d.client_id
                 WHERE d.expiry IS NOT NULL AND d.expiry<>'' AND d.expiry<=?"""
        args = [limit]
        if client_id:
            sql += " AND c.id=?"
            args.append(client_id)
        for r in self.q(sql, args):
            d = dict(r)
            d["source"] = "document"
            out.append(d)
        out.sort(key=lambda d: d["expiry"])
        return self._visible(out, "client_id")

    # ---------- files ----------
    def files_root(self) -> Path:
        custom = self.get("files_root")
        root = Path(custom) if custom else self.path.parent / "Files"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def client_folder(self, cid, create=True):
        c = self.client(cid)
        if not c:
            return None
        root = self.files_root()
        code = c["code"] or f"CUS-{cid}"
        for p in root.iterdir() if root.exists() else []:
            if p.is_dir() and (p.name == code or p.name.startswith(code + " ")):
                return p
        folder = root / f"{code} - {safe_name(c['name'])}"
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        return folder

    def owner_folder(self, owner_type, owner_id, create=True):
        """Folder where the files of a client / owner / employee / transaction live."""
        if owner_type == "client":
            return self.client_folder(owner_id, create)
        if owner_type == "employee":
            e = self.employee(owner_id)
            base = self.client_folder(e["client_id"], create) / "الموظفين - Employees"
            folder = base / f"{e['code']} - {safe_name(e['name'])}"
            if base.exists():
                for p in base.iterdir():
                    if p.is_dir() and p.name.startswith(e["code"] + " "):
                        folder = p
                        break
        elif owner_type == "owner":
            o = self.owner(owner_id)
            base = self.client_folder(o["client_id"], create) / "الملاك - Owners"
            folder = base / safe_name(o["name"])
        elif owner_type == "transaction":
            tx = self.transaction(owner_id)
            base = self.client_folder(tx["client_id"], create) / "المعاملات - Transactions"
            folder = base / f"{tx['code']} - {safe_name(tx['tx_type'].split('|')[0])}"
        else:
            raise ValueError(owner_type)
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        return folder

    def owner_client_id(self, owner_type, owner_id):
        if owner_type == "client":
            return owner_id
        table = {"employee": "employees", "owner": "owners",
                 "transaction": "transactions"}[owner_type]
        r = self.one(f"SELECT client_id FROM {table} WHERE id=?", (owner_id,))
        return r["client_id"] if r else None

    def add_file(self, owner_type, owner_id, source, category="", note=""):
        """Copy a file into the owner's folder and register it."""
        source = Path(source)
        folder = self.owner_folder(owner_type, owner_id)
        cat = safe_name(category.split("|")[0], 40) if category else ""
        base = f"{cat} - {safe_name(source.stem)}" if cat else safe_name(source.stem)
        target = folder / f"{base}{source.suffix.lower()}"
        n = 2
        while target.exists():
            target = folder / f"{base} ({n}){source.suffix.lower()}"
            n += 1
        shutil.copy2(str(source), str(target))
        rel = target.relative_to(self.files_root()).as_posix()
        return self.x(
            "INSERT INTO files(owner_type, owner_id, client_id, category, path, "
            "original_name, size, added, note) VALUES(?,?,?,?,?,?,?,?,?)",
            (owner_type, owner_id, self.owner_client_id(owner_type, owner_id),
             category, rel, source.name, target.stat().st_size, now_str(), note))

    def files(self, owner_type, owner_id):
        return self.q("SELECT * FROM files WHERE owner_type=? AND owner_id=? "
                      "ORDER BY category, id", (owner_type, owner_id))

    def file(self, fid):
        return self.one("SELECT * FROM files WHERE id=?", (fid,))

    def file_path(self, row) -> Path:
        return self.files_root() / row["path"]

    def update_file_category(self, fid, category):
        self.x("UPDATE files SET category=? WHERE id=?", (category, fid))

    def delete_file(self, fid, remove_from_disk=True):
        return self._delete_file_raw(fid, remove_from_disk)

    def _delete_file_raw(self, fid, remove_from_disk=True):
        r = self.file(fid)
        if not r:
            return
        self.x("DELETE FROM files WHERE id=?", (fid,))
        if remove_from_disk:
            p = self.file_path(r)
            try:
                if p.exists():
                    trash = self.files_root() / "_محذوفات_Deleted"
                    trash.mkdir(exist_ok=True)
                    shutil.move(str(p), str(trash / f"{dt.datetime.now():%Y%m%d%H%M%S}_{p.name}"))
            except OSError:
                pass

    def _delete_owned_files(self, owner_type, owner_id):
        for r in self.files(owner_type, owner_id):
            self._delete_file_raw(r["id"])

    def photo_of(self, owner_type, owner_id):
        """The personal photo file (if one was uploaded)."""
        for r in self.files(owner_type, owner_id):
            if r["category"].startswith("صورة شخصية") and \
                    Path(r["path"]).suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
                p = self.file_path(r)
                if p.exists():
                    return p
        return None

    # ---------- transactions ----------
    TX_FIELDS = ("client_id", "employee_id", "tx_type", "entity", "status",
                 "start_date", "due_date", "fee", "gov_fees", "zoho_invoice", "notes",
                 "revenue_account_id", "gov_account_id", "assigned_user_id")
    ID_FIELDS = ("employee_id", "revenue_account_id", "gov_account_id", "client_id",
                 "assigned_user_id")

    def save_transaction(self, data, tid=None):
        tid = self._save_transaction(data, tid)
        self.post_transaction(tid)
        return tid

    def _save_transaction(self, data, tid=None):
        d = dict(data)
        vals = [(d.get(f) or None) if f in self.ID_FIELDS else
                (d.get(f) if d.get(f) is not None else "") for f in self.TX_FIELDS]
        if tid:
            old = self.transaction(tid)
            new_client = d.get("client_id")
            if old and new_client and old["client_id"] != new_client:
                if self.one("SELECT 1 FROM invoices WHERE tx_id=? AND status<>'cancelled' LIMIT 1", (tid,)):
                    raise ValueError("tx_invoiced_client")
                # receipts follow the transaction to its new client
                self.x("UPDATE payments SET client_id=? WHERE tx_id=?", (new_client, tid))
                for p in self.q("SELECT id FROM payments WHERE tx_id=?", (tid,)):
                    self.post_payment(p["id"], commit=False)
            self.x(f"UPDATE transactions SET "
                   f"{', '.join(f + '=?' for f in self.TX_FIELDS)}, updated=? "
                   f"WHERE id=?", (*vals, now_str(), tid))
            if old and old["status"] != d.get("status"):
                self.add_log(tid, "status", old["status"], d.get("status"))
            return tid
        tid = self.x(
            f"INSERT INTO transactions({', '.join(self.TX_FIELDS)}, code, created, updated) "
            f"VALUES({', '.join('?' * len(self.TX_FIELDS))}, ?, ?, ?)",
            (*vals, self.next_code("transactions"), now_str(), now_str()))
        self.add_log(tid, "created", "", d.get("status"))
        return tid

    def delete_transaction(self, tid):
        if self.one("SELECT 1 FROM payments WHERE tx_id=? LIMIT 1", (tid,)):
            raise ValueError("has_payments")
        if self.one("SELECT 1 FROM invoices WHERE tx_id=? AND status<>'cancelled' LIMIT 1", (tid,)):
            raise ValueError("has_invoice")
        self._delete_owned_files("transaction", tid)
        self._clear_source("transaction", tid)
        for r in self.q("SELECT id FROM payments WHERE tx_id=?", (tid,)):
            self._clear_source("payment", r["id"])
        self.x("DELETE FROM transactions WHERE id=?", (tid,))

    def transaction(self, tid):
        return self.one("SELECT * FROM transactions WHERE id=?", (tid,))

    def transactions(self, search="", flt="all", client_id=None):
        s = f"%{search.strip()}%"
        sql = """
            SELECT t.*, c.name AS client_name, c.phone AS client_phone,
              c.code AS client_code, e.name AS employee_name,
              COALESCE(NULLIF(u.full_name,''), u.username) AS assigned_name,
              COALESCE((SELECT SUM(p.amount) FROM payments p
                        WHERE p.tx_id=t.id), 0) AS paid
            FROM transactions t JOIN clients c ON c.id = t.client_id
            LEFT JOIN employees e ON e.id = t.employee_id
            LEFT JOIN users u ON u.id = t.assigned_user_id
            WHERE (c.name LIKE ? OR t.tx_type LIKE ? OR t.entity LIKE ?
                   OR t.zoho_invoice LIKE ? OR t.notes LIKE ? OR t.code LIKE ?
                   OR c.code LIKE ? OR e.name LIKE ?)
        """
        args = [s] * 8
        if client_id:
            sql += " AND t.client_id = ?"
            args.append(client_id)
        if self.own_only:
            sql += " AND t.assigned_user_id = ?"
            args.append(self.user["id"])
        rows = self.q(sql + " ORDER BY t.due_date, t.id DESC", args)
        invoiced = self.invoiced_totals()
        t0 = iso(today())
        out = []
        for r in rows:
            r = dict(r)
            is_open = self.is_open_status(r["status"])
            # what the client owes: the invoice(s) when the transaction was invoiced,
            # otherwise fee + government fees
            r["due"] = round(invoiced.get(r["id"], (r["fee"] or 0) + (r["gov_fees"] or 0)), 2)
            if r["status"] == CANCELLED and r["id"] not in invoiced:
                r["due"] = 0.0
            remaining = round(r["due"] - (r["paid"] or 0), 2)
            r["remaining"] = remaining
            if flt == "open" and not is_open:
                continue
            if flt == "done" and is_open:
                continue
            if flt == "overdue" and not (is_open and r["due_date"]
                                         and r["due_date"] < t0):
                continue
            if flt == "unpaid" and not (remaining > 0.005):
                continue
            out.append(r)
        return out

    def invoiced_totals(self):
        """tx id -> total of its non-cancelled invoices."""
        out = {}
        for i in self.q("SELECT * FROM invoices WHERE tx_id IS NOT NULL AND status<>'cancelled'"):
            out[i["tx_id"]] = out.get(i["tx_id"], 0.0) + doc_total(i)
        return out

    def tx_due(self, tid):
        t = self.transaction(tid)
        if not t:
            return 0.0
        inv = self.invoiced_totals()
        return round(inv.get(tid, (t["fee"] or 0) + (t["gov_fees"] or 0)), 2)

    def action_needed(self, days=None):
        days = self.get_int("due_alert_days", 3) if days is None else days
        limit = iso(today() + dt.timedelta(days=days))
        return [r for r in self.transactions(flt="open")
                if r["due_date"] and r["due_date"] <= limit]

    # ---------- payments / receipts (سندات القبض) ----------
    def add_payment(self, tx_id, amount, pay_date, method="", note="",
                    client_id=None, invoice_id=None, account_id=None):
        if tx_id and not client_id:
            client_id = self.transaction(tx_id)["client_id"]
        if invoice_id and not client_id:
            client_id = self.invoice(invoice_id)["client_id"]
        if invoice_id and not tx_id:
            inv = self.invoice(invoice_id)
            tx_id = inv["tx_id"] if inv else None
        pid = self.x("INSERT INTO payments(code, tx_id, client_id, invoice_id, amount, "
                     "pay_date, method, note, account_id) VALUES(?,?,?,?,?,?,?,?,?)",
                     (self.next_code("payments"), tx_id or None, client_id,
                      invoice_id or None, float(amount), pay_date, method, note,
                      account_id or self.money_account_for_method(method)))
        self.post_payment(pid)
        if tx_id:
            self.add_log(tx_id, "payment", "", f"{float(amount):,.2f}")
        return pid

    def update_payment(self, pid, data):
        """Edit a receipt; its accounting entry is rebuilt."""
        fields = ("amount", "pay_date", "method", "note", "account_id", "tx_id",
                  "invoice_id", "client_id")
        vals = [data.get(f) for f in fields]
        self.x(f"UPDATE payments SET {', '.join(f + '=?' for f in fields)} WHERE id=?",
               (*vals, pid))
        self.post_payment(pid)

    def delete_payment(self, pid):
        self._clear_source("payment", pid)
        self.x("DELETE FROM payments WHERE id=?", (pid,))

    def payment(self, pid):
        return self.one("""SELECT p.*, c.name AS client_name, c.code AS client_code,
                             c.phone AS client_phone, t.code AS tx_code,
                             t.tx_type AS tx_type, i.code AS invoice_code
                           FROM payments p LEFT JOIN clients c ON c.id=p.client_id
                           LEFT JOIN transactions t ON t.id=p.tx_id
                           LEFT JOIN invoices i ON i.id=p.invoice_id
                           WHERE p.id=?""", (pid,))

    def payments(self, tx_id=None, invoice_id=None, client_id=None, search=""):
        sql = """SELECT p.*, c.name AS client_name, c.code AS client_code,
                   t.code AS tx_code, i.code AS invoice_code
                 FROM payments p LEFT JOIN clients c ON c.id=p.client_id
                 LEFT JOIN transactions t ON t.id=p.tx_id
                 LEFT JOIN invoices i ON i.id=p.invoice_id WHERE 1=1"""
        args = []
        if tx_id:
            sql += " AND p.tx_id=?"
            args.append(tx_id)
        if invoice_id:
            sql += " AND p.invoice_id=?"
            args.append(invoice_id)
        if client_id:
            sql += " AND p.client_id=?"
            args.append(client_id)
        if search:
            s = f"%{search.strip()}%"
            sql += " AND (c.name LIKE ? OR p.code LIKE ? OR t.code LIKE ? OR i.code LIKE ?)"
            args += [s] * 4
        return self.q(sql + " ORDER BY p.pay_date DESC, p.id DESC", args)

    def paid_total(self, tx_id):
        r = self.one("SELECT COALESCE(SUM(amount),0) AS s FROM payments WHERE tx_id=?",
                     (tx_id,))
        return r["s"]

    # ---------- log ----------
    def add_log(self, tx_id, kind, old, new):
        self.x("INSERT INTO tx_log(tx_id, ts, text) VALUES(?,?,?)",
               (tx_id, now_str(), f"{kind}\t{old or ''}\t{new or ''}"))

    def log(self, tx_id):
        return self.q("SELECT * FROM tx_log WHERE tx_id=? ORDER BY id DESC", (tx_id,))

    # ---------- quotations ----------
    def save_quotation(self, data, qid=None):
        d = dict(data)
        d["items"] = json.dumps(d.get("items") or [], ensure_ascii=False)
        return self._save("quotations", ("client_id", "date", "valid_days", "items",
                                         "gov_fees", "discount", "terms", "notes",
                                         "status"), d, qid, code=True, stamp="created")

    def quotations(self, search=""):
        s = f"%{search.strip()}%"
        return self.q("""SELECT q.*, c.name AS client_name, c.code AS client_code
                         FROM quotations q JOIN clients c ON c.id=q.client_id
                         WHERE c.name LIKE ? OR q.code LIKE ? OR c.code LIKE ?
                         ORDER BY q.id DESC""", (s, s, s))

    def quotation(self, qid):
        return self.one("SELECT * FROM quotations WHERE id=?", (qid,))

    def delete_quotation(self, qid):
        self.x("DELETE FROM quotations WHERE id=?", (qid,))

    # ---------- invoices ----------
    def save_invoice(self, data, iid=None):
        d = dict(data)
        d["items"] = json.dumps(d.get("items") or [], ensure_ascii=False)
        d["tx_id"] = d.get("tx_id") or None
        d["quote_id"] = d.get("quote_id") or None
        d["revenue_account_id"] = d.get("revenue_account_id") or None
        fields = ("client_id", "tx_id", "quote_id", "date", "due_date", "items",
                  "gov_fees", "discount", "notes", "status", "revenue_account_id")
        ids = ("tx_id", "quote_id", "revenue_account_id")
        vals = [d.get(f) if f in ids else d.get(f, "") for f in fields]
        old_tx = None
        if iid:
            old = self.invoice(iid)
            old_tx = old["tx_id"] if old else None
            self.x(f"UPDATE invoices SET {', '.join(f + '=?' for f in fields)} WHERE id=?",
                   (*vals, iid))
        else:
            iid = self.x(f"INSERT INTO invoices({', '.join(fields)}, code, created) "
                         f"VALUES({', '.join('?' * len(fields))}, ?, ?)",
                         (*vals, self.next_code("invoices"), now_str()))
        self.post_invoice(iid)
        for t in {old_tx, d["tx_id"]} - {None}:
            self.post_transaction(t)
        return iid

    def invoices(self, search="", client_id=None):
        s = f"%{search.strip()}%"
        sql = """SELECT i.*, c.name AS client_name, c.code AS client_code,
                   c.phone AS client_phone, t.code AS tx_code,
                   COALESCE((SELECT SUM(p.amount) FROM payments p
                             WHERE p.invoice_id=i.id), 0) AS paid
                 FROM invoices i JOIN clients c ON c.id=i.client_id
                 LEFT JOIN transactions t ON t.id=i.tx_id
                 WHERE (c.name LIKE ? OR i.code LIKE ? OR c.code LIKE ?)"""
        args = [s, s, s]
        if client_id:
            sql += " AND i.client_id=?"
            args.append(client_id)
        return self.q(sql + " ORDER BY i.id DESC", args)

    def invoice(self, iid):
        return self.one("SELECT * FROM invoices WHERE id=?", (iid,))

    def invoice_paid(self, iid):
        return self.one("SELECT COALESCE(SUM(amount),0) AS s FROM payments "
                        "WHERE invoice_id=?", (iid,))["s"]

    def delete_invoice(self, iid):
        inv = self.invoice(iid)
        self._clear_source("invoice", iid)
        self.x("UPDATE payments SET invoice_id=NULL WHERE invoice_id=?", (iid,))
        self.x("DELETE FROM invoices WHERE id=?", (iid,))
        if inv and inv["tx_id"]:
            self.post_transaction(inv["tx_id"])

    # ---------- expenses ----------
    def save_expense(self, data, eid=None):
        d = dict(data)
        ids = ("client_id", "tx_id", "account_id", "paid_from_id")
        for f in ids:
            d[f] = d.get(f) or None
        if d["account_id"] and not d.get("category"):
            a = self.account(d["account_id"])
            d["category"] = f"{a['name_ar']}|{a['name_en']}" if a else ""
        fields = ("date", "category", "description", "amount", "method",
                  "client_id", "tx_id", "note", "account_id", "paid_from_id")
        vals = [d.get(f) if f in ids else d.get(f, "") for f in fields]
        if eid:
            self.x(f"UPDATE expenses SET {', '.join(f + '=?' for f in fields)} WHERE id=?",
                   (*vals, eid))
        else:
            eid = self.x(f"INSERT INTO expenses({', '.join(fields)}, code, created) "
                         f"VALUES({', '.join('?' * len(fields))}, ?, ?)",
                         (*vals, self.next_code("expenses"), now_str()))
        self.post_expense(eid)
        return eid

    def expenses(self, search="", date_from=None, date_to=None):
        s = f"%{search.strip()}%"
        sql = """SELECT x.*, c.name AS client_name, t.code AS tx_code
                 FROM expenses x LEFT JOIN clients c ON c.id=x.client_id
                 LEFT JOIN transactions t ON t.id=x.tx_id
                 WHERE (x.description LIKE ? OR x.category LIKE ? OR x.code LIKE ?
                        OR COALESCE(c.name,'') LIKE ?)"""
        args = [s] * 4
        if date_from:
            sql += " AND x.date>=?"
            args.append(date_from)
        if date_to:
            sql += " AND x.date<=?"
            args.append(date_to)
        return self.q(sql + " ORDER BY x.date DESC, x.id DESC", args)

    def expense(self, eid):
        return self.one("SELECT * FROM expenses WHERE id=?", (eid,))

    def delete_expense(self, eid):
        self._clear_source("expense", eid)
        self.x("DELETE FROM expenses WHERE id=?", (eid,))

    # ---------- tasks ----------
    def save_task(self, data, tid=None):
        d = dict(data)
        d["client_id"] = d.get("client_id") or None
        d["tx_id"] = d.get("tx_id") or None
        d["assigned_user_id"] = d.get("assigned_user_id") or None
        fields = ("title", "client_id", "tx_id", "due_date", "priority", "status", "notes",
                  "assigned_user_id")
        ids = ("client_id", "tx_id", "assigned_user_id")
        vals = [d.get(f) if f in ids else d.get(f, "") for f in fields]
        if tid:
            self.x(f"UPDATE tasks SET {', '.join(f + '=?' for f in fields)} WHERE id=?",
                   (*vals, tid))
            return tid
        return self.x(f"INSERT INTO tasks({', '.join(fields)}, created, created_by) "
                      f"VALUES({', '.join('?' * len(fields))}, ?, ?)",
                      (*vals, now_str(), self.user["id"] if self.user is not None else None))

    def tasks(self, flt="open", search=""):
        s = f"%{search.strip()}%"
        sql = """SELECT k.*, c.name AS client_name, c.phone AS client_phone,
                   t.code AS tx_code, COALESCE(NULLIF(u.full_name,''), u.username) AS assigned_name
                 FROM tasks k LEFT JOIN clients c ON c.id=k.client_id
                 LEFT JOIN transactions t ON t.id=k.tx_id
                 LEFT JOIN users u ON u.id=k.assigned_user_id
                 WHERE (k.title LIKE ? OR COALESCE(c.name,'') LIKE ? OR k.notes LIKE ?)"""
        args = [s, s, s]
        if self.own_only:
            sql += " AND (k.assigned_user_id=? OR k.created_by=?)"
            args += [self.user["id"]] * 2
        t0 = iso(today())
        if flt == "open":
            sql += " AND k.status='todo'"
        elif flt == "today":
            sql += " AND k.status='todo' AND k.due_date<>'' AND k.due_date<=?"
            args.append(t0)
        elif flt == "done":
            sql += " AND k.status='done'"
        order = (" ORDER BY CASE WHEN k.due_date='' THEN 1 ELSE 0 END, k.due_date,"
                 " CASE k.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END")
        return self.q(sql + order, args)

    def task(self, tid):
        return self.one("SELECT * FROM tasks WHERE id=?", (tid,))

    def set_task_done(self, tid, done=True):
        self.x("UPDATE tasks SET status=?, done_at=? WHERE id=?",
               ("done" if done else "todo", now_str() if done else "", tid))

    def delete_task(self, tid):
        self.x("DELETE FROM tasks WHERE id=?", (tid,))

    # ---------- templates ----------
    def templates(self, channel=None):
        if channel:
            return self.q("SELECT * FROM templates WHERE channel=? ORDER BY id", (channel,))
        return self.q("SELECT * FROM templates ORDER BY channel DESC, id")

    def template(self, tid):
        return self.one("SELECT * FROM templates WHERE id=?", (tid,))

    def save_template(self, data, tid=None):
        return self._save("templates", ("title", "category", "channel", "body"), data, tid)

    def delete_template(self, tid):
        self.x("DELETE FROM templates WHERE id=?", (tid,))

    # ---------- finance ----------
    def finance_summary(self, date_from=None, date_to=None):
        rows = [r for r in self.transactions() if r["status"] != CANCELLED]
        fees = sum(r["due"] for r in rows)
        paid = sum(r["paid"] or 0 for r in rows)
        month_start = iso(today().replace(day=1))
        month = self.one("SELECT COALESCE(SUM(amount),0) AS s FROM payments "
                         "WHERE pay_date >= ?", (month_start,))["s"]
        pl = self.income_statement(month_start, iso(today()))
        outstanding = sum(b for b in self.ar_balances().values() if b > 0)
        return {"fees": fees, "paid": paid, "outstanding": outstanding,
                "month": month, "month_income": pl["total_income"],
                "month_exp": pl["total_expenses"], "month_profit": pl["net"]}

    def outstanding_by_client(self):
        """Balances from the accounts receivable ledger (single source of truth)."""
        counts = {}
        for r in self.transactions(flt="unpaid"):
            counts[r["client_id"]] = counts.get(r["client_id"], 0) + 1
        out = []
        for cid, bal in self.ar_balances().items():
            if bal <= 0.005:
                continue
            c = self.client(cid)
            if not c:
                continue
            out.append({"client_id": cid, "name": c["name"], "code": c["code"],
                        "phone": c["phone"], "count": counts.get(cid, 0), "amount": bal})
        return sorted(out, key=lambda a: -a["amount"])

    def monthly_report(self, months=12):
        """Income (receipts), expenses and profit for the last N months."""
        out = []
        d = today().replace(day=1)
        for _ in range(months):
            start = d
            nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            inc = self.one("SELECT COALESCE(SUM(amount),0) s FROM payments "
                           "WHERE pay_date>=? AND pay_date<?", (iso(start), iso(nxt)))["s"]
            exp = self.one("SELECT COALESCE(SUM(amount),0) s FROM expenses "
                           "WHERE date>=? AND date<?", (iso(start), iso(nxt)))["s"]
            cnt = self.one("SELECT COUNT(*) s FROM transactions "
                           "WHERE start_date>=? AND start_date<?", (iso(start), iso(nxt)))["s"]
            out.append({"month": start.strftime("%Y-%m"), "income": inc,
                        "expenses": exp, "profit": inc - exp, "tx_count": cnt})
            d = (d - dt.timedelta(days=1)).replace(day=1)
        return out

    def expenses_by_category(self, date_from, date_to):
        return self.q("""SELECT category, COALESCE(SUM(amount),0) AS total, COUNT(*) AS n
                         FROM expenses WHERE date>=? AND date<=?
                         GROUP BY category ORDER BY total DESC""", (date_from, date_to))

    def client_statement(self, cid):
        """Chronological charges and payments of a client (from the ledger)."""
        return [{"date": ln["date"], "ref": ln["ref"] or ln["entry_code"],
                 "desc": ln["memo"], "debit": ln["debit"], "credit": ln["credit"],
                 "balance": ln["balance"]} for ln in self.client_ledger(cid)]

    # ---------- backup ----------
    def backup_to(self, target: Path, with_files=True) -> Path:
        """Full backup = one .zip containing the database and all uploaded files."""
        target = Path(target)
        stamp = f"{dt.datetime.now():%Y-%m-%d_%H%M}"
        if target.is_dir():
            target = target / (f"{APP_NAME}_backup_{stamp}.zip" if with_files
                               else f"{APP_NAME}_backup_{stamp}.db")
        if target.suffix.lower() == ".db":
            dst = sqlite3.connect(str(target))
            with dst:
                self.con.backup(dst)
            dst.close()
            return target
        tmp = self.path.parent / f"_tmp_backup_{stamp}.db"
        dst = sqlite3.connect(str(tmp))
        with dst:
            self.con.backup(dst)
        dst.close()
        try:
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(tmp, "data.db")
                root = self.files_root()
                if with_files:
                    for p in root.rglob("*"):
                        if p.is_file():
                            z.write(p, "Files/" + p.relative_to(root).as_posix())
                    wa = self.path.parent / "WhatsApp"      # photos / files of the WhatsApp inbox
                    if self.master is None and wa.exists():
                        for p in wa.rglob("*"):
                            if p.is_file() and not p.name.endswith(".part"):
                                z.write(p, "WhatsApp/" + p.relative_to(wa).as_posix())
                # the other companies (each with its own database and files)
                for row, cdb in self.other_company_dbs():
                    base = f"companies/{row['folder']}/"
                    ctmp = self.path.parent / f"_tmp_backup_{stamp}_{row['id']}.db"
                    src = sqlite3.connect(str(cdb))
                    dst = sqlite3.connect(str(ctmp))
                    try:
                        with dst:
                            src.backup(dst)
                    finally:
                        dst.close()
                        src.close()
                    try:
                        z.write(ctmp, base + "data.db")
                    finally:
                        try:
                            ctmp.unlink()
                        except OSError:
                            pass
                    if with_files:
                        for p in cdb.parent.rglob("*"):
                            rel = p.relative_to(cdb.parent).as_posix()
                            if (p.is_file() and rel != "data.db" and not rel.startswith("auto_backups/")
                                    and not p.name.startswith("_tmp")):
                                z.write(p, base + rel)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        return target

    def auto_full_backup(self, force=False):
        """Full backup (data + files) to the folder chosen in Settings (e.g. a Google Drive
        or OneDrive folder) every N days. Keeps the newest 8. Returns the path or None."""
        folder = self.get("auto_backup_dir")
        if not folder or not Path(folder).exists():
            return None
        days = self.get_int("auto_backup_days", 7)
        last = parse_date(self.get("last_full_backup"))
        if not force and last and (today() - last).days < days:
            return None
        p = self.backup_to(Path(folder))
        self.set("last_full_backup", iso(today()))
        olds = sorted(Path(folder).glob(f"{APP_NAME}_backup_*.zip"))
        for f in olds[:-8]:
            try:
                f.unlink()
            except OSError:
                pass
        return p

    def auto_backup(self, keep=14):
        folder = self.path.parent / "auto_backups"
        folder.mkdir(exist_ok=True)
        target = folder / f"data_{iso(today())}.db"
        if not target.exists():
            self.backup_to(target, with_files=False)
        files = sorted(folder.glob("data_*.db"))
        for f in files[:-keep]:
            try:
                f.unlink()
            except OSError:
                pass
        return target

    def restore_from(self, source: Path):
        source = Path(source)
        stamp = f"{dt.datetime.now():%Y%m%d_%H%M%S}"
        tmpdir = None
        db_file = source
        if source.suffix.lower() == ".zip":
            tmpdir = self.path.parent / f"_restore_{stamp}"
            with zipfile.ZipFile(source) as z:
                z.extractall(tmpdir)
            db_file = tmpdir / "data.db"
        test = sqlite3.connect(str(db_file))
        try:
            names = {r[0] for r in test.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            test.close()
        if not {"clients", "transactions"} <= names:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)
            raise ValueError("not a valid backup file")
        self.backup_to(self.path.parent / f"before_restore_{stamp}.db")
        files_root = self.files_root()
        self.close()
        shutil.copyfile(db_file, self.path)
        if tmpdir and (tmpdir / "Files").exists():
            if files_root.exists():
                shutil.move(str(files_root), str(files_root.parent / f"Files_before_restore_{stamp}"))
            shutil.move(str(tmpdir / "Files"), str(files_root))
        if tmpdir and (tmpdir / "WhatsApp").exists() and self.master is None:
            wroot = self.path.parent / "WhatsApp"
            if wroot.exists():
                shutil.move(str(wroot), str(self.path.parent / f"WhatsApp_before_restore_{stamp}"))
            shutil.move(str(tmpdir / "WhatsApp"), str(wroot))
        if tmpdir and (tmpdir / "companies").exists() and self.master is None:
            croot = self.path.parent / "companies"
            if croot.exists():
                shutil.move(str(croot), str(self.path.parent / f"companies_before_restore_{stamp}"))
            shutil.move(str(tmpdir / "companies"), str(croot))
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        self.open()


DB.install_guards()


WIPE_MARKER = "WIPE_ACCOUNTS.request"


def apply_pending_wipe(db):
    """Empty the accounts of the main company once, when the owner asked for it
    (a file named WIPE_ACCOUNTS.request in the data folder). A backup is kept first."""
    marker = db.path.parent / WIPE_MARKER
    if not marker.exists() or db.company_id != 1:
        return False
    stamp = f"{dt.datetime.now():%Y-%m-%d_%H%M%S}"
    folder = db.path.parent / "auto_backups"
    folder.mkdir(exist_ok=True)
    db.backup_to(folder / f"before_wipe_accounts_{stamp}.db", with_files=False)
    with db.atomic():
        for t in ("jlines", "journal", "expenses", "vouchers"):
            db.con.execute(f"DELETE FROM {t}")
            db.con.execute("DELETE FROM sqlite_sequence WHERE name=?", (t,))
        for k in ("seq_expenses", "seq_vouchers", "ai_api_key", "ai_model"):
            db.con.execute("DELETE FROM settings WHERE key=?", (k,))
        db.con.execute("INSERT INTO audit_log(ts, user_id, username, action, entity, entity_id, ref, details) "
                       "VALUES(?,?,?,?,?,?,?,?)", (now_str(), None, "النظام", "delete",
                                                   "الحسابات|Accounts", None, "",
                                                   "مسح كل القيود والمصروفات بطلب المالك"))
    try:
        marker.rename(marker.with_name(f"WIPE_ACCOUNTS.done_{stamp}.txt"))
    except OSError:
        marker.unlink(missing_ok=True)
    return True
