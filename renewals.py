# -*- coding: utf-8 -*-
"""متابعة الإقامات والتجديدات وتذكيرات العملاء - renewals follow-up & client reminders.

* Every expiry date of the clients' papers is an "item": employees (QID, passport, health
  certificate, work contract), the company papers (C.R., license, establishment card),
  owners and other documents.  Which types are followed is chosen in the reminder settings.
* Each item has a renewal status (new / contacted / in progress / renewed / not renewing)
  kept per expiry date: when the new expiry is typed in, the item starts again as "new".
* Reminders go to the client (one message listing all his items) at the chosen number of
  days before expiry (e.g. 60, 30, 7) and, after expiry, once a week until renewed.
  Every reminder is written in the log so nothing is sent twice.
"""
import datetime as dt
import re

# key, table, field, arabic, english, number field, default on
TYPES = [
    ("emp_qid", "employees", "qid_expiry", "البطاقة الشخصية (الإقامة)", "QID (residence)", "qid_number", True),
    ("emp_passport", "employees", "passport_expiry", "جواز السفر", "Passport", "passport_number", True),
    ("emp_health", "employees", "health_expiry", "الشهادة الصحية", "Health certificate", "health_number", True),
    ("emp_contract", "employees", "contract_expiry", "عقد العمل", "Work contract", "", True),
    ("cr", "clients", "cr_expiry", "السجل التجاري", "Commercial registration", "cr_number", True),
    ("license", "clients", "license_expiry", "الرخصة التجارية", "Trade license", "license_number", True),
    ("est", "clients", "est_expiry", "قيد المنشأة", "Establishment card", "est_number", True),
    ("doc", "documents", "expiry", "مستندات أخرى", "Other documents", "doc_number", True),
    ("client_qid", "clients", "id_expiry", "البطاقة الشخصية للعميل", "Client QID", "id_number", False),
    ("owner_qid", "owners", "qid_expiry", "البطاقة الشخصية للمالك", "Owner QID", "qid_number", False),
    ("owner_passport", "owners", "passport_expiry", "جواز سفر المالك", "Owner passport", "passport_number", False),
]
TYPE_BY_KEY = {t[0]: t for t in TYPES}

STATUSES = [("new", "لسه", "Not started"), ("contacted", "تم التواصل", "Contacted"),
            ("in_progress", "جاري التجديد", "Renewal in progress"), ("renewed", "اتجدد", "Renewed"),
            ("not_renewing", "مش هيجدد", "Not renewing")]
STATUS_LABEL = {k: (a, e) for k, a, e in STATUSES}
CLOSED = ("renewed", "not_renewing")

CHANNELS = {"api": ("واتساب أوتوماتيك", "WhatsApp (automatic)"),
            "manual": ("واتساب (ضغطة واحدة)", "WhatsApp (one click)"),
            "letter": ("خطاب PDF", "PDF letter")}

DEFAULT_MESSAGE = ("السلام عليكم {client}،\n"
                   "نود تذكيركم بالمستندات التالية الخاصة بكم:\n{list}\n\n"
                   "يسعدنا نتولى التجديد عنكم، ردّ علينا بكلمة «تمام» ونبدأ فورًا.\n{company}\n{phone}")

# the text to submit to Meta as a UTILITY template (Arabic). Parameters cannot contain new lines.
API_TEMPLATE_TEXT = ("السلام عليكم {{1}}، نود تذكيركم بقرب انتهاء المستندات التالية: {{2}}. "
                     "يسعدنا نتولى التجديد عنكم، ردّوا على الرسالة دي ونبدأ فورًا. {{3}}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_followups (
    item_key TEXT PRIMARY KEY, status TEXT DEFAULT 'new', note TEXT DEFAULT '',
    tx_id INTEGER, updated TEXT, updated_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS reminder_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, client_id INTEGER, phone TEXT DEFAULT '',
    channel TEXT, stage INTEGER, status TEXT, error TEXT DEFAULT '', message TEXT DEFAULT '',
    wa_id TEXT DEFAULT '', username TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS reminder_items (
    log_id INTEGER NOT NULL REFERENCES reminder_log(id) ON DELETE CASCADE,
    item_key TEXT NOT NULL, stage INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ri_key ON reminder_items(item_key);
CREATE INDEX IF NOT EXISTS idx_rl_client ON reminder_log(client_id);
"""

# reminder settings live in the main database (one WhatsApp number for the whole office)
SHARED_KEYS = {"rem_days", "rem_weekly_after", "rem_types", "rem_message", "rem_auto",
               "rem_time", "wa_api_on", "wa_token", "wa_phone_id", "wa_template",
               "wa_template_lang", "wa_api_version", "rem_last_auto"}


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def phone_digits(phone):
    """International digits for WhatsApp (Qatar 8-digit numbers get 974)."""
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("00"):
        d = d[2:]
    if len(d) == 8:
        d = "974" + d
    return d


class RenewalsMixin:
    """Mixed into core.DB."""

    def _renewals_setup(self):
        self.con.executescript(SCHEMA)
        self.con.commit()

    # ---------- settings ----------
    def rem_days(self):
        out = []
        for p in re.split(r"[,\s،]+", self.get("rem_days") or "60,30,7"):
            try:
                v = int(p)
                if v >= 0:
                    out.append(v)
            except ValueError:
                pass
        return sorted(set(out), reverse=True) or [60, 30, 7]

    def rem_types(self):
        raw = self.get("rem_types")
        if not raw:
            return [t[0] for t in TYPES if t[6]]
        return [k for k in raw.split(",") if k in TYPE_BY_KEY]

    def rem_weekly_after(self):
        return self.get("rem_weekly_after") != "0"

    # ---------- items ----------
    def renewal_items(self, days=None, client_id=None, types=None, include_closed=True):
        """Items expiring within `days` (None = everything with a date)."""
        types = types or self.rem_types()
        limit = (dt.date.today() + dt.timedelta(days=days)).isoformat() if days is not None else None
        vis = self.visible_client_ids() if hasattr(self, "visible_client_ids") else None
        out = []
        for key in types:
            _, table, field, ar, en, numf, _d = TYPE_BY_KEY[key]
            num = f"x.{numf}" if numf else "''"
            if table == "clients":
                sql = (f"SELECT x.id AS ref_id, x.id AS client_id, x.name AS client_name, x.phone, "
                       f"x.name AS holder, {num} AS number, x.{field} AS expiry, '' AS doc_name "
                       f"FROM clients x WHERE 1=1")
            elif table == "documents":
                sql = ("SELECT x.id AS ref_id, c.id AS client_id, c.name AS client_name, c.phone, "
                       "COALESCE(NULLIF(x.holder,''), c.name) AS holder, x.doc_number AS number, "
                       "x.expiry AS expiry, x.doc_type AS doc_name "
                       "FROM documents x JOIN clients c ON c.id=x.client_id WHERE 1=1")
            else:
                extra = " AND x.status='active'" if table == "employees" else ""
                sql = (f"SELECT x.id AS ref_id, c.id AS client_id, c.name AS client_name, c.phone, "
                       f"x.name AS holder, {num} AS number, x.{field} AS expiry, '' AS doc_name "
                       f"FROM {table} x JOIN clients c ON c.id=x.client_id WHERE 1=1{extra}")
            sql += f" AND COALESCE(x.{field},'')<>''"
            args = []
            if limit:
                sql += f" AND x.{field}<=?"
                args.append(limit)
            if client_id:
                sql += " AND c.id=?" if table != "clients" else " AND x.id=?"
                args.append(client_id)
            for r in self.q(sql, args):
                if vis is not None and r["client_id"] not in vis:
                    continue
                d = dict(r)
                d["type"] = key
                d["type_ar"], d["type_en"] = ar, en
                if d["doc_name"]:
                    d["type_ar"] = d["doc_name"].split("|")[0]
                    d["type_en"] = d["doc_name"].split("|")[-1]
                d["key"] = f"{key}:{d['ref_id']}:{d['expiry']}"
                out.append(d)
        follow = {r["item_key"]: r for r in self.q("SELECT * FROM doc_followups")}
        last = {r["item_key"]: r for r in self.q(
            """SELECT ri.item_key, MAX(l.ts) AS ts, MIN(ri.stage) AS stage FROM reminder_items ri
               JOIN reminder_log l ON l.id=ri.log_id WHERE l.status IN ('sent','opened')
               GROUP BY ri.item_key""")}
        today = dt.date.today()
        res = []
        for d in out:
            f = follow.get(d["key"])
            d["status"] = f["status"] if f else "new"
            d["note"] = f["note"] if f else ""
            d["tx_id"] = f["tx_id"] if f else None
            lr = last.get(d["key"])
            d["last_reminder"] = lr["ts"] if lr else ""
            d["last_stage"] = lr["stage"] if lr else None
            try:
                d["days_left"] = (dt.date.fromisoformat(d["expiry"][:10]) - today).days
            except ValueError:
                continue
            if not include_closed and d["status"] in CLOSED:
                continue
            res.append(d)
        res.sort(key=lambda x: (x["expiry"], x["client_name"] or "", x["holder"] or ""))
        return res

    def set_followup(self, item_key, status=None, note=None, tx_id=None):
        old = self.one("SELECT * FROM doc_followups WHERE item_key=?", (item_key,))
        st = status if status is not None else (old["status"] if old else "new")
        nt = note if note is not None else (old["note"] if old else "")
        tx = tx_id if tx_id is not None else (old["tx_id"] if old else None)
        who = (self.user["full_name"] or self.user["username"]) if self.user is not None else ""
        self.x("INSERT OR REPLACE INTO doc_followups(item_key, status, note, tx_id, updated, updated_by) "
               "VALUES(?,?,?,?,?,?)", (item_key, st, nt, tx, _now(), who))

    # ---------- what is due today ----------
    def due_stage(self, item):
        """The reminder stage due for this item today, or None."""
        if item["status"] in CLOSED or item["status"] == "in_progress":
            return None               # done, refused, or we are already renewing it
        left = item["days_left"]
        if left < 0:
            reminded_after = item["last_stage"] is not None and item["last_stage"] <= 0
            if not reminded_after:
                return 0
            if not self.rem_weekly_after():
                return None
            try:
                last = dt.datetime.strptime(item["last_reminder"][:10], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                return 0
            return 0 if (dt.date.today() - last).days >= 7 else None
        stages = [s for s in self.rem_days() if left <= s]
        if not stages:
            return None
        stage = min(stages)           # the closest stage already reached
        if item["last_stage"] is not None and item["last_stage"] <= stage:
            return None               # already reminded at this stage (or a later one)
        return stage

    def due_reminders(self):
        """[{client_id, client_name, phone, stage, items:[...]}] - one entry per client."""
        horizon = max(self.rem_days())
        groups = {}
        for it in self.renewal_items(days=horizon, include_closed=False):
            st = self.due_stage(it)
            if st is None:
                continue
            g = groups.setdefault(it["client_id"], {"client_id": it["client_id"],
                                                    "client_name": it["client_name"],
                                                    "phone": it["phone"] or "", "items": [],
                                                    "stage": st})
            g["items"].append(dict(it, due_stage=st))
            g["stage"] = min(g["stage"], st)
        return sorted(groups.values(), key=lambda g: (g["stage"], g["client_name"] or ""))

    # ---------- message text ----------
    def item_line(self, it, lang="ar"):
        name = it["type_en"] if lang == "en" else it["type_ar"]
        who = it["holder"] if it["holder"] and it["holder"] != it["client_name"] else ""
        num = f" {'no.' if lang == 'en' else 'رقم'} {it['number']}" if it.get("number") else ""
        left = it["days_left"]
        if lang == "en":
            when = f"expired {-left} days ago" if left < 0 else (f"expires {it['expiry']} ({left} days left)")
        else:
            when = f"منتهية من {-left} يوم" if left < 0 else f"تنتهي {it['expiry']} (باقي {left} يوم)"
        return f"{who + ' - ' if who else ''}{name}{num} - {when}"

    def reminder_text(self, group):
        body = self.get("rem_message") or DEFAULT_MESSAGE
        lines = "\n".join("• " + self.item_line(it) for it in group["items"])
        vals = {"client": group["client_name"] or "", "list": lines,
                "company": self.company_name("ar"), "phone": self.get("company_phone"),
                "count": str(len(group["items"]))}
        return re.sub(r"\{(\w+)\}", lambda m: vals.get(m.group(1), m.group(0)), body)

    def api_params(self, group):
        """The three template parameters (no new lines allowed by WhatsApp)."""
        items = "؛ ".join(self.item_line(it) for it in group["items"])
        if len(items) > 900:
            items = items[:880] + f" ... (+{len(group['items'])})"
        sig = self.company_name("ar")
        if self.get("company_phone"):
            sig += " " + self.get("company_phone")
        return [group["client_name"] or "", items, sig]

    # ---------- log ----------
    def log_reminder(self, group, channel, status, message="", error="", wa_id=""):
        who = (self.user["full_name"] or self.user["username"]) if self.user is not None else "تلقائي"
        with self.atomic():
            cur = self.con.execute(
                "INSERT INTO reminder_log(ts, client_id, phone, channel, stage, status, error, message, "
                "wa_id, username) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (_now(), group["client_id"], group.get("phone", ""), channel, group.get("stage"), status,
                 error, message, wa_id, who))
            lid = cur.lastrowid
            for it in group["items"]:
                self.con.execute("INSERT INTO reminder_items(log_id, item_key, stage) VALUES(?,?,?)",
                                 (lid, it["key"], it.get("due_stage", group.get("stage"))))
                if status in ("sent", "opened"):
                    old = self.one("SELECT status FROM doc_followups WHERE item_key=?", (it["key"],))
                    if not old or old["status"] == "new":
                        self.con.execute(
                            "INSERT OR REPLACE INTO doc_followups(item_key, status, note, tx_id, updated, updated_by) "
                            "VALUES(?, 'contacted', COALESCE((SELECT note FROM doc_followups WHERE item_key=?),''), "
                            "(SELECT tx_id FROM doc_followups WHERE item_key=?), ?, ?)",
                            (it["key"], it["key"], it["key"], _now(), who))
        return lid

    def reminder_log(self, date_from=None, date_to=None, client_id=None):
        sql = """SELECT l.*, c.name AS client_name, (SELECT COUNT(*) FROM reminder_items ri WHERE ri.log_id=l.id) AS n
                 FROM reminder_log l LEFT JOIN clients c ON c.id=l.client_id WHERE 1=1"""
        args = []
        if date_from:
            sql += " AND l.ts>=?"
            args.append(date_from)
        if date_to:
            sql += " AND l.ts<=?"
            args.append(date_to + " 23:59")
        if client_id:
            sql += " AND l.client_id=?"
            args.append(client_id)
        return self.q(sql + " ORDER BY l.id DESC LIMIT 3000", args)

    def wa_ready(self):
        return (self.get("wa_api_on") == "1" and bool(self.get("wa_token"))
                and bool(self.get("wa_phone_id")) and bool(self.get("wa_template")))
