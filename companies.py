# -*- coding: utf-8 -*-
"""الشركات المتعددة - several companies in one program.

How it works
------------
* The main database (C:\\ProgramData\\CreativeReps\\data.db) is company number 1
  (المناديب المبدعون).  It also keeps the list of companies, the users and which
  user may open which company.
* Every other company has its own folder and its own database:
      C:\\ProgramData\\CreativeReps\\companies\\company_002\\data.db
      C:\\ProgramData\\CreativeReps\\companies\\company_002\\Files\\...
  so its clients, transactions, accounts, letterhead and currency never mix with
  another company.
* Users are managed in the main database and copied into a company database
  when it is opened, so a user logs in once and moves between the companies he
  is allowed to see.
"""
import datetime as dt
import shutil
import sqlite3
from pathlib import Path

# code: (arabic symbol, english code, arabic name, english name, arabic sub-unit, english sub-unit)
CURRENCIES = {
    "QAR": ("ر.ق", "QAR", "ريال قطري", "Qatari Riyals", "درهم", "Dirhams"),
    "SAR": ("ر.س", "SAR", "ريال سعودي", "Saudi Riyals", "هللة", "Halalas"),
    "AED": ("د.إ", "AED", "درهم إماراتي", "UAE Dirhams", "فلس", "Fils"),
    "KWD": ("د.ك", "KWD", "دينار كويتي", "Kuwaiti Dinars", "فلس", "Fils"),
    "BHD": ("د.ب", "BHD", "دينار بحريني", "Bahraini Dinars", "فلس", "Fils"),
    "OMR": ("ر.ع", "OMR", "ريال عماني", "Omani Riyals", "بيسة", "Baisa"),
    "EGP": ("ج.م", "EGP", "جنيه مصري", "Egyptian Pounds", "قرش", "Piastres"),
    "USD": ("$", "USD", "دولار أمريكي", "US Dollars", "سنت", "Cents"),
}
# country shown in the company form -> its usual currency
COUNTRIES = [("قطر|Qatar", "QAR"), ("السعودية|Saudi Arabia", "SAR"),
             ("الإمارات|United Arab Emirates", "AED"), ("الكويت|Kuwait", "KWD"),
             ("البحرين|Bahrain", "BHD"), ("عُمان|Oman", "OMR"), ("مصر|Egypt", "EGP"),
             ("أخرى|Other", "USD")]

# settings that belong to the person, not to one company (read from the main database)
SHARED_SETTINGS = {"ai_api_key", "ai_model", "language", "idle_minutes", "last_username",
                   "recovery_hash", "auto_backup_dir", "auto_backup_days", "last_full_backup",
                   # leads from the internet always land in the main company
                   "web_leads_on", "web_leads_url", "web_leads_key", "web_leads_minutes",
                   "web_leads_status", "web_leads_started",
                   # renewal reminders & the WhatsApp API: one setup for the whole office
                   "rem_days", "rem_weekly_after", "rem_types", "rem_message", "rem_auto",
                   "rem_time", "wa_api_on", "wa_token", "wa_phone_id", "wa_template",
                   "wa_template_lang", "wa_api_version", "rem_last_auto",
                   # WhatsApp inbox (Kapso): the office has one WhatsApp number
                   "inbox_on", "kapso_api_key", "kapso_phone_id", "inbox_days_back",
                   "inbox_autosave", "inbox_sound", "inbox_status"}

# company fields kept in the company's own settings
PROFILE_KEYS = ("company_name_ar", "company_name_en", "company_cr", "company_license",
                "company_phone", "company_email", "company_address", "company_country",
                "company_bank", "company_iban", "currency", "vat_number", "vat_rate")

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name_ar TEXT NOT NULL, name_en TEXT DEFAULT '',
    folder TEXT DEFAULT '', currency TEXT DEFAULT 'QAR', country TEXT DEFAULT '',
    active INTEGER DEFAULT 1, created TEXT
);
CREATE TABLE IF NOT EXISTS user_companies (
    user_id INTEGER NOT NULL, company_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, company_id)
);
"""

USER_COLS = ("id", "username", "full_name", "role", "perms", "pw_hash", "salt", "active",
             "phone", "created", "last_login")


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def currency_info(code):
    return CURRENCIES.get((code or "QAR").upper(), CURRENCIES["QAR"])


class CompaniesMixin:
    """Mixed into core.DB.  `master` is the main database when this one is another company."""
    master = None

    # ---------- setup ----------
    def _companies_setup(self):
        if self.is_sub_company:
            return
        self.con.executescript(SCHEMA)
        if "kind" not in {r[1] for r in self.con.execute("PRAGMA table_info(companies)")}:
            self.con.execute("ALTER TABLE companies ADD COLUMN kind TEXT DEFAULT 'clearing'")
        if not self.con.execute("SELECT 1 FROM companies WHERE id=1").fetchone():
            if self.get("company_name_ar") and not self.get("company_country") \
                    and (self.get("currency") or "QAR") == "QAR":
                self.set("company_country", "قطر|Qatar")
            self.con.execute(
                "INSERT INTO companies(id, name_ar, name_en, folder, currency, country, created) "
                "VALUES(1,?,?,'',?,?,?)",
                (self.get("company_name_ar") or "الشركة الرئيسية", self.get("company_name_en"),
                 self.get("currency") or "QAR", self.get("company_country"), _now()))
            # everybody who already works in the program keeps access to the main company
            self.con.execute("INSERT OR IGNORE INTO user_companies(user_id, company_id) "
                             "SELECT id, 1 FROM users")
        self.con.commit()

    @property
    def is_sub_company(self):
        r = self.con.execute("SELECT value FROM settings WHERE key='company_id'").fetchone()
        return bool(r and r[0] and r[0] != "1")

    @property
    def company_id(self):
        r = self.con.execute("SELECT value FROM settings WHERE key='company_id'").fetchone()
        try:
            return int(r[0]) if r and r[0] else 1
        except ValueError:
            return 1

    @property
    def main_db(self):
        return self.master or self

    # ---------- currency ----------
    def currency(self):
        return (self.get("currency") or "QAR").upper()

    def cur_sym(self, lang="ar"):
        c = currency_info(self.currency())
        return c[1] if lang == "en" else c[0]

    def cur_name(self, lang="ar"):
        c = currency_info(self.currency())
        return c[3] if lang == "en" else c[2]

    # ---------- the list of companies (main database only) ----------
    def companies(self, active_only=False):
        m = self.main_db
        sql = "SELECT * FROM companies" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
        return m.q(sql)

    def company_row(self, cid):
        return self.main_db.one("SELECT * FROM companies WHERE id=?", (cid,))

    def companies_for(self, user):
        """Companies the user may open (admins: all active ones)."""
        m = self.main_db
        if user is None or user["role"] == "admin":
            return m.q("SELECT * FROM companies WHERE active=1 ORDER BY id")
        return m.q("""SELECT c.* FROM companies c JOIN user_companies u ON u.company_id=c.id
                      WHERE c.active=1 AND u.user_id=? ORDER BY c.id""", (user["id"],))

    def company_users(self, cid):
        return {r[0] for r in self.main_db.con.execute(
            "SELECT user_id FROM user_companies WHERE company_id=?", (cid,))}

    def set_company_users(self, cid, user_ids):
        m = self.main_db
        with m.atomic():
            m.con.execute("DELETE FROM user_companies WHERE company_id=?", (cid,))
            for uid in set(user_ids or []):
                m.con.execute("INSERT OR IGNORE INTO user_companies(user_id, company_id) VALUES(?,?)",
                              (uid, cid))

    def grant_company(self, uid, cid):
        self.main_db.x("INSERT OR IGNORE INTO user_companies(user_id, company_id) VALUES(?,?)",
                       (uid, cid))

    def _need_companies_perm(self):
        if self.user is not None and not self.can("companies"):
            from users import NotAllowed
            raise NotAllowed("companies")

    def companies_root(self) -> Path:
        return self.main_db.path.parent / "companies"

    def company_db_path(self, cid) -> Path:
        row = self.company_row(cid)
        if row is None or not row["folder"]:
            return self.main_db.path
        return self.companies_root() / row["folder"] / "data.db"

    def create_company(self, profile, user_ids=None, logo=None):
        """Make a new company with its own empty database. Returns the new id."""
        m = self.main_db
        m._need_companies_perm()
        name = (profile.get("company_name_ar") or "").strip()
        if not name:
            raise ValueError("company_name")
        cid = m.x("INSERT INTO companies(name_ar, name_en, currency, country, created) "
                  "VALUES(?,?,?,?,?)",
                  (name, profile.get("company_name_en", ""), profile.get("currency") or "QAR",
                   profile.get("company_country", ""), _now()))
        folder = f"company_{cid:03d}"
        m.x("UPDATE companies SET folder=? WHERE id=?", (folder, cid))
        path = m.companies_root() / folder / "data.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            sub = type(m).new_company_db(path, cid)
            sub.master = m
            try:
                sub.save_profile(profile, logo=logo)
                # the new company starts with the same lists the user already set up
                for key in ("tx_types", "stages", "file_cats", "doc_types", "entities",
                            "pay_methods", "crm_stages", "crm_sources", "crm_lost_reasons"):
                    if m.get(key):
                        sub.set(key, m.get(key))
            finally:
                sub.close()
        except Exception:
            m.x("DELETE FROM companies WHERE id=?", (cid,))
            shutil.rmtree(path.parent, ignore_errors=True)
            raise
        ids = set(user_ids or [])
        if m.user is not None:
            ids.add(m.user["id"])
        m.set_company_users(cid, ids)
        m.audit("create", "شركة|Company", cid, folder, name)
        return cid

    @classmethod
    def new_company_db(cls, path, cid):
        """A fresh database for company `cid` (no Al Manadeeb data or branding)."""
        first = not Path(path).exists()
        con = sqlite3.connect(str(path))
        con.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        if first:
            # empty company details, own logo/letterhead only
            for k in PROFILE_KEYS:
                con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, '')", (k,))
            con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('builtin_brand', '0')")
            con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('use_letterhead', '0')")
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('company_id', ?)", (str(cid),))
        con.commit()
        con.close()
        return cls(path)

    def save_profile(self, profile, logo=None):
        """Company details in this company's own settings (+ the list in the main db)."""
        old_cur = self.currency()
        for k in PROFILE_KEYS:
            if k in profile:
                self.set(k, str(profile.get(k) or "").strip())
        if profile.get("company_kind"):
            from edition import GENERIC
            if self.company_id != 1 or GENERIC:
                self.set_company_kind(profile["company_kind"])
        new_cur = self.currency()
        if new_cur != old_cur or not self.get("currency_templates_done"):
            # message templates written with the old currency sign
            old_sym, new_sym = currency_info(old_cur)[0], currency_info(new_cur)[0]
            if old_sym != new_sym:
                self.x("UPDATE templates SET body=REPLACE(body, ?, ?)", (old_sym, new_sym))
            self.set("currency_templates_done", "1")
        if logo:
            src = Path(logo)
            if src.exists():
                target = self.path.parent / ("logo" + src.suffix.lower())
                if src.resolve() != target.resolve():
                    shutil.copyfile(src, target)
                self.set("logo_path", str(target))
        m = self.main_db
        m.x("UPDATE companies SET name_ar=?, name_en=?, currency=?, country=?, kind=? WHERE id=?",
            (self.get("company_name_ar"), self.get("company_name_en"), self.currency(),
             self.get("company_country"), self.company_kind, self.company_id))

    def set_company_active(self, cid, active):
        m = self.main_db
        m._need_companies_perm()
        if cid == 1 and not active:
            raise ValueError("main_company")
        m.x("UPDATE companies SET active=? WHERE id=?", (int(bool(active)), cid))
        row = m.company_row(cid)
        m.audit("update", "شركة|Company", cid, row["folder"] if row else "",
                "تفعيل" if active else "إيقاف")

    def has_financial_records(self):
        for t in ("transactions", "invoices", "payments", "expenses", "journal"):
            if self.one(f"SELECT 1 FROM {t} LIMIT 1"):
                return True
        return False

    # ---------- opening a company ----------
    def open_company(self, cid):
        """The DB object of company `cid` (the main db itself for company 1)."""
        m = self.main_db
        row = m.company_row(cid)
        if row is None:
            raise ValueError("no_company")
        if not row["folder"]:
            return m
        path = m.companies_root() / row["folder"] / "data.db"
        if not path.exists():
            raise FileNotFoundError(str(path))
        sub = type(m)(path)
        sub.master = m
        sub.sync_users()
        return sub

    def sync_users(self):
        """Copy the users from the main database into this company's database."""
        if self.master is None:
            return
        rows = self.master.q(f"SELECT {', '.join(USER_COLS)} FROM users")
        ids = [r["id"] for r in rows]
        with self.atomic():
            for r in rows:
                self.con.execute(
                    f"INSERT OR REPLACE INTO users({', '.join(USER_COLS)}) "
                    f"VALUES({', '.join('?' * len(USER_COLS))})", tuple(r))
            if ids:
                marks = ",".join("?" * len(ids))
                gone = [r[0] for r in self.con.execute(
                    f"SELECT id FROM users WHERE id NOT IN ({marks})", ids)]
            else:
                gone = [r[0] for r in self.con.execute("SELECT id FROM users")]
            for uid in gone:
                self.con.execute("DELETE FROM users WHERE id=?", (uid,))
                for t in ("transactions", "tasks", "leads"):
                    self.con.execute(f"UPDATE {t} SET assigned_user_id=NULL WHERE assigned_user_id=?",
                                     (uid,))

    # ---------- backups that cover every company ----------
    def other_company_dbs(self):
        """(row, path) of the other companies' databases that exist on disk."""
        out = []
        if self.is_sub_company:
            return out
        for row in self.q("SELECT * FROM companies WHERE folder<>'' ORDER BY id"):
            p = self.companies_root() / row["folder"] / "data.db"
            if p.exists():
                out.append((row, p))
        return out

    def auto_backup_companies(self, keep=14):
        """Daily copy of every other company's database in its own auto_backups folder."""
        done = []
        for row, p in self.other_company_dbs():
            folder = p.parent / "auto_backups"
            folder.mkdir(exist_ok=True)
            target = folder / f"data_{dt.date.today().isoformat()}.db"
            if not target.exists():
                src = sqlite3.connect(str(p))
                dst = sqlite3.connect(str(target))
                try:
                    with dst:
                        src.backup(dst)
                finally:
                    dst.close()
                    src.close()
            for f in sorted(folder.glob("data_*.db"))[:-keep]:
                try:
                    f.unlink()
                except OSError:
                    pass
            done.append(target)
        return done
