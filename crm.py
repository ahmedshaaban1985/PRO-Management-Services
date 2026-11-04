# -*- coding: utf-8 -*-
"""إدارة علاقات العملاء (CRM) - leads, deals pipeline, follow-ups, campaigns, sales reports.

A lead is someone who asked about a service and is not (yet) a client.  It moves
through stages: جديد → تواصلنا → عرض سعر → تفاوض → فزنا / خسرنا.  "Won" turns it
into a client (CUS-) and opens the transaction (TRX-) in one step.
"""
import datetime as dt
import json

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT,
    name TEXT NOT NULL, company TEXT DEFAULT '', kind TEXT DEFAULT 'company',
    phone TEXT DEFAULT '', email TEXT DEFAULT '', nationality TEXT DEFAULT '',
    source TEXT DEFAULT '', service TEXT DEFAULT '', entity TEXT DEFAULT '',
    value REAL DEFAULT 0, stage TEXT DEFAULT '', lost_reason TEXT DEFAULT '',
    assigned_user_id INTEGER, next_follow_up TEXT DEFAULT '',
    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    tx_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    notes TEXT DEFAULT '', created TEXT, updated TEXT, created_by INTEGER,
    closed_date TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS crm_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id) ON DELETE CASCADE,
    kind TEXT DEFAULT 'note', summary TEXT DEFAULT '', ts TEXT,
    user_id INTEGER, user_name TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS crm_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, segment TEXT DEFAULT '',
    message TEXT DEFAULT '', recipients TEXT DEFAULT '[]', created TEXT,
    user_id INTEGER, user_name TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS crm_imported (
    ext_id TEXT PRIMARY KEY, lead_id INTEGER, ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads(stage);
CREATE INDEX IF NOT EXISTS idx_leads_follow ON leads(next_follow_up);
CREATE INDEX IF NOT EXISTS idx_act_lead ON crm_activities(lead_id);
"""

DEFAULTS = {
    "crm_stages": "\n".join([
        "جديد|New", "تواصلنا|Contacted", "عرض سعر|Quoted", "تفاوض|Negotiation",
        "فزنا|Won", "خسرنا|Lost"]),
    "crm_sources": "\n".join([
        "واتساب|WhatsApp", "اتصال هاتفي|Phone call", "توصية من عميل|Referral",
        "انستغرام|Instagram", "فيسبوك|Facebook", "إعلانات فيسبوك|Facebook ads",
        "إعلانات انستغرام|Instagram ads", "فورم الموقع|Website form", "جوجل / الموقع|Google / website",
        "زيارة للمكتب|Walk-in", "عميل قديم|Existing client", "أخرى|Other"]),
    "crm_lost_reasons": "\n".join([
        "السعر غالي|Price too high", "اختار مكتب تاني|Chose a competitor",
        "مش جاهز دلوقتي|Not ready yet", "مفيش رد|No response",
        "الخدمة مش متاحة|Service not available", "أخرى|Other"]),
}

ACTIVITY_KINDS = [("call", "مكالمة", "Call"), ("whatsapp", "واتساب", "WhatsApp"),
                  ("visit", "زيارة / مقابلة", "Visit / meeting"), ("email", "إيميل", "Email"),
                  ("quote", "إرسال عرض سعر", "Quote sent"), ("note", "ملاحظة", "Note"),
                  ("stage", "تغيير مرحلة", "Stage change"), ("campaign", "رسالة جماعية", "Campaign")]

LEAD_FIELDS = ("name", "company", "kind", "phone", "email", "nationality", "source", "service",
               "entity", "value", "stage", "lost_reason", "assigned_user_id", "next_follow_up",
               "client_id", "notes")

# segments for group messages: key, arabic, english, needs a number (days / months)
SEGMENTS = [
    ("open_leads", "كل العملاء المحتملين المفتوحين", "All open leads", None),
    ("stage", "عملاء محتملين في مرحلة معيّنة", "Leads in a stage", None),
    ("lost_leads", "اللي خسرناهم (نرجع نكلمهم)", "Lost leads (win back)", None),
    ("expiring", "عملاء مستنداتهم هتنتهي خلال (يوم)", "Clients with papers expiring within (days)", 60),
    ("all_clients", "كل العملاء النشطين", "All active clients", None),
    ("inactive", "عملاء مالهمش معاملات من (شهر)", "Clients with no transaction for (months)", 6),
    ("balance", "عملاء عليهم مبالغ متبقية", "Clients with a balance due", None),
]


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _today():
    return dt.date.today().isoformat()


def T_(ar, en):
    return ar


def phone_key(p):
    d = "".join(ch for ch in str(p or "") if ch.isdigit())
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("974") and len(d) == 11:
        d = d[3:]
    return d


class CRMMixin:
    # ---------- setup ----------
    def _crm_setup(self):
        self.con.executescript(SCHEMA)
        for k, v in DEFAULTS.items():
            self.con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
        # users created before the CRM existed get the matching CRM permissions once
        if not self.con.execute("SELECT 1 FROM settings WHERE key='crm_perms_added'").fetchone():
            add = {"accountant": ["crm", "crm_reports"], "rep": ["crm"]}
            for u in self.con.execute("SELECT id, role, perms FROM users").fetchall():
                if u["role"] in add:
                    try:
                        perms = set(json.loads(u["perms"] or "[]"))
                    except ValueError:
                        perms = set()
                    perms.update(add[u["role"]])
                    self.con.execute("UPDATE users SET perms=? WHERE id=?",
                                     (json.dumps(sorted(perms)), u["id"]))
            self.con.execute("INSERT INTO settings(key, value) VALUES('crm_perms_added', '1')")
        self.con.commit()

    # ---------- stages ----------
    def crm_stages(self):
        st = self.get_list("crm_stages")
        return st if len(st) >= 3 else DEFAULTS["crm_stages"].splitlines()

    def crm_won(self):
        return self.crm_stages()[-2]

    def crm_lost(self):
        return self.crm_stages()[-1]

    def crm_open_stages(self):
        return self.crm_stages()[:-2]

    def lead_is_open(self, lead):
        return lead["stage"] not in (self.crm_won(), self.crm_lost())

    # ---------- leads ----------
    def save_lead(self, data, lid=None):
        d = dict(data)
        d["assigned_user_id"] = d.get("assigned_user_id") or None
        d["client_id"] = d.get("client_id") or None
        d["value"] = float(d.get("value") or 0)
        d["stage"] = d.get("stage") or self.crm_stages()[0]
        if not (d.get("name") or "").strip():
            raise ValueError("lead_name")
        vals = [d.get(f) if f in ("assigned_user_id", "client_id") else
                (d.get(f) if d.get(f) is not None else "") for f in LEAD_FIELDS]
        me = self.user["id"] if self.user is not None else None
        if lid:
            old = self.lead(lid)
            self.x(f"UPDATE leads SET {', '.join(f + '=?' for f in LEAD_FIELDS)}, updated=? WHERE id=?",
                   (*vals, _now(), lid))
            if old and old["stage"] != d["stage"]:
                self._stage_changed(lid, old["stage"], d["stage"])
            return lid
        lid = self.x(f"INSERT INTO leads({', '.join(LEAD_FIELDS)}, code, created, updated, created_by) "
                     f"VALUES({', '.join('?' * len(LEAD_FIELDS))}, ?, ?, ?, ?)",
                     (*vals, self.next_code("leads"), _now(), _now(), me))
        if d["stage"] in (self.crm_won(), self.crm_lost()):
            self.x("UPDATE leads SET closed_date=? WHERE id=?", (_today(), lid))
        return lid

    def _stage_changed(self, lid, old, new):
        closed = _today() if new in (self.crm_won(), self.crm_lost()) else ""
        self.x("UPDATE leads SET closed_date=?, updated=? WHERE id=?", (closed, _now(), lid))
        lang = self.get("language")
        ln = (lambda v: (v.split("|")[1] if lang == "en" and "|" in v else v.split("|")[0]))
        self._log_activity(lid, "stage", f"{ln(old)} ← {ln(new)}" if lang != "en" else f"{ln(old)} → {ln(new)}")

    def set_lead_stage(self, lid, stage, lost_reason=""):
        old = self.lead(lid)
        if not old:
            return lid
        if stage == self.crm_won() and not old["client_id"]:
            raise ValueError("convert_first")
        self.x("UPDATE leads SET stage=?, lost_reason=? WHERE id=?",
               (stage, lost_reason if stage == self.crm_lost() else "", lid))
        if old["stage"] != stage:
            self._stage_changed(lid, old["stage"], stage)
        if stage == self.crm_lost():
            self.x("UPDATE leads SET next_follow_up='' WHERE id=?", (lid,))
        return lid

    def delete_lead(self, lid):
        self.x("DELETE FROM leads WHERE id=?", (lid,))

    def lead(self, lid):
        return self.one("""SELECT l.*, COALESCE(NULLIF(u.full_name,''), u.username) AS assigned_name,
                                  c.code AS client_code, t.code AS tx_code
                           FROM leads l LEFT JOIN users u ON u.id=l.assigned_user_id
                           LEFT JOIN clients c ON c.id=l.client_id
                           LEFT JOIN transactions t ON t.id=l.tx_id WHERE l.id=?""", (lid,))

    def leads(self, search="", flt="open", stage=None, source=None, user_id=None,
              date_from=None, date_to=None):
        """flt: open | won | lost | all | follow (follow-up due today or overdue)"""
        s = f"%{(search or '').strip()}%"
        sql = """SELECT l.*, COALESCE(NULLIF(u.full_name,''), u.username) AS assigned_name,
                        c.code AS client_code, t.code AS tx_code
                 FROM leads l LEFT JOIN users u ON u.id=l.assigned_user_id
                 LEFT JOIN clients c ON c.id=l.client_id
                 LEFT JOIN transactions t ON t.id=l.tx_id
                 WHERE (l.name LIKE ? OR l.company LIKE ? OR l.phone LIKE ? OR l.code LIKE ?
                        OR l.service LIKE ? OR l.notes LIKE ?)"""
        args = [s] * 6
        won, lost = self.crm_won(), self.crm_lost()
        if flt == "open":
            sql += " AND l.stage NOT IN (?, ?)"
            args += [won, lost]
        elif flt == "won":
            sql += " AND l.stage=?"
            args.append(won)
        elif flt == "lost":
            sql += " AND l.stage=?"
            args.append(lost)
        elif flt == "follow":
            sql += " AND l.stage NOT IN (?, ?) AND l.next_follow_up<>'' AND l.next_follow_up<=?"
            args += [won, lost, _today()]
        if stage:
            sql += " AND l.stage=?"
            args.append(stage)
        if source:
            sql += " AND l.source=?"
            args.append(source)
        if user_id:
            sql += " AND l.assigned_user_id=?"
            args.append(user_id)
        if date_from:
            sql += " AND substr(l.created,1,10)>=?"
            args.append(date_from)
        if date_to:
            sql += " AND substr(l.created,1,10)<=?"
            args.append(date_to)
        if self.own_only:
            sql += " AND (l.assigned_user_id=? OR l.created_by=?)"
            args += [self.user["id"]] * 2
        order = (" ORDER BY CASE WHEN l.next_follow_up='' THEN 1 ELSE 0 END, l.next_follow_up, l.id DESC"
                 if flt in ("open", "follow") else " ORDER BY l.updated DESC")
        return self.q(sql + order, args)

    def follow_ups_due(self):
        return self.leads(flt="follow")

    def find_lead_duplicates(self, phone, exclude=None):
        """Leads or clients with the same phone number."""
        k = phone_key(phone)
        if len(k) < 7:
            return []
        out = []
        for r in self.q("SELECT id, code, name, phone, stage FROM leads WHERE phone<>''"):
            if r["id"] != exclude and phone_key(r["phone"]) == k:
                out.append({"type": "lead", "id": r["id"], "code": r["code"], "name": r["name"],
                            "stage": r["stage"]})
        for r in self.q("SELECT id, code, name, phone, phone2 FROM clients"):
            if k in (phone_key(r["phone"]), phone_key(r["phone2"])):
                out.append({"type": "client", "id": r["id"], "code": r["code"], "name": r["name"]})
        return out

    # ---------- leads that arrive by themselves (quick add / internet forms & ads) ----------
    def _next_rep(self):
        """Round-robin over active reps (users with the rep role)."""
        reps = [r["id"] for r in self.q("SELECT id FROM users WHERE active=1 AND role='rep' ORDER BY id")]
        if not reps:
            return None
        last = self.get_int("crm_last_rep", 0)
        nxt = next((r for r in reps if r > last), reps[0])
        self.set("crm_last_rep", nxt)
        return nxt

    def receive_lead(self, data, kind="whatsapp", message="", ext_id=None):
        """Add an incoming inquiry without duplicating people.
        -> (status, lead_id): new | client (existing client asking for something new) |
           existing (open lead with the same phone: message added to its history) | skipped"""
        d = dict(data)
        if ext_id and self.one("SELECT 1 FROM crm_imported WHERE ext_id=?", (ext_id,)):
            return "skipped", None
        phone = (d.get("phone") or "").strip()
        dups = self.find_lead_duplicates(phone) if phone else []
        open_lead = next((x for x in dups if x["type"] == "lead" and
                          x["stage"] not in (self.crm_won(), self.crm_lost())), None)
        today = _today()
        if open_lead:
            lid = open_lead["id"]
            self._log_activity(lid, kind, (message or T_("رسالة جديدة", "New message")).strip())
            r = self.lead(lid)
            if not r["next_follow_up"] or r["next_follow_up"] > today:
                self.x("UPDATE leads SET next_follow_up=?, updated=? WHERE id=?", (today, _now(), lid))
            status = "existing"
        else:
            client = next((x for x in dups if x["type"] == "client"), None)
            if client:
                d["client_id"] = client["id"]
            if not d.get("assigned_user_id") and self.get("crm_auto_assign") == "1":
                d["assigned_user_id"] = self._next_rep()
            d.setdefault("next_follow_up", today)
            d.setdefault("stage", self.crm_stages()[0])
            if not (d.get("name") or "").strip():
                d["name"] = phone or "—"
            lid = self.save_lead.__wrapped__(self, d) if hasattr(self.save_lead, "__wrapped__") \
                else self.save_lead(d)
            if message:
                self._log_activity(lid, kind, message.strip())
            status = "client" if client else "new"
        if ext_id:
            self.x("INSERT OR IGNORE INTO crm_imported(ext_id, lead_id, ts) VALUES(?,?,?)", (ext_id, lid, _now()))
        return status, lid

    # ---------- activities ----------
    def _log_activity(self, lid, kind, summary):
        u = self.user
        return self.x("INSERT INTO crm_activities(lead_id, kind, summary, ts, user_id, user_name) "
                      "VALUES(?,?,?,?,?,?)",
                      (lid, kind, summary, _now(), u["id"] if u is not None else None,
                       (u["full_name"] or u["username"]) if u is not None else ""))

    def add_activity(self, lead_id, kind, summary, next_follow_up=None):
        """Log a call / WhatsApp / visit ... and optionally set the next follow-up date."""
        aid = self._log_activity(lead_id, kind, (summary or "").strip())
        lead = self.lead(lead_id)
        if lead and lead["stage"] == self.crm_stages()[0] and kind in ("call", "whatsapp", "visit", "email"):
            # first real contact moves a new lead to "contacted"
            self.x("UPDATE leads SET stage=? WHERE id=?", (self.crm_stages()[1], lead_id))
            self._stage_changed(lead_id, lead["stage"], self.crm_stages()[1])
        if next_follow_up is not None:
            self.x("UPDATE leads SET next_follow_up=?, updated=? WHERE id=?",
                   (next_follow_up, _now(), lead_id))
        else:
            self.x("UPDATE leads SET updated=? WHERE id=?", (_now(), lead_id))
        return aid

    def activities(self, lead_id=None, limit=500):
        sql = """SELECT a.*, l.name AS lead_name, l.code AS lead_code FROM crm_activities a
                 LEFT JOIN leads l ON l.id=a.lead_id"""
        args = []
        if lead_id:
            sql += " WHERE a.lead_id=?"
            args.append(lead_id)
        elif self.own_only:
            sql += " WHERE (l.assigned_user_id=? OR l.created_by=? OR a.user_id=?)"
            args += [self.user["id"]] * 3
        return self.q(sql + f" ORDER BY a.id DESC LIMIT {int(limit)}", args)

    # ---------- won: lead -> client + transaction ----------
    def convert_lead(self, lid, tx_data=None, client_data=None):
        """Turn a lead into a client and (optionally) open its transaction.
        tx_data None = no transaction.  Returns the lead id."""
        lead = self.lead(lid)
        if not lead:
            raise ValueError("lead_missing")
        cid = lead["client_id"]
        if not cid:
            cd = {"name": lead["company"] or lead["name"], "kind": lead["kind"] or "company",
                  "status": "active", "phone": lead["phone"], "email": lead["email"],
                  "contact_person": lead["name"] if lead["company"] else "",
                  "nationality": lead["nationality"],
                  "notes": f"من العميل المحتمل {lead['code']}" + (f" — {lead['notes']}" if lead["notes"] else "")}
            cd.update(client_data or {})
            cid = self.save_client(cd)
        tid = lead["tx_id"]
        if tx_data is not None and not tid:
            stages = self.get_list("stages")
            td = {"client_id": cid, "tx_type": lead["service"], "entity": lead["entity"],
                  "status": stages[0] if stages else "", "start_date": _today(), "due_date": "",
                  "fee": lead["value"] or 0, "gov_fees": 0,
                  "assigned_user_id": lead["assigned_user_id"],
                  "notes": f"من العميل المحتمل {lead['code']}"}
            td.update(tx_data)
            tid = self.save_transaction(td)
        won = self.crm_won()
        self.x("UPDATE leads SET client_id=?, tx_id=?, stage=?, next_follow_up='', updated=? WHERE id=?",
               (cid, tid, won, _now(), lid))
        if lead["stage"] != won:
            self._stage_changed(lid, lead["stage"], won)
        c = self.client(cid)
        t = self.transaction(tid) if tid else None
        self._log_activity(lid, "note", f"اتحوّل لعميل {c['code']}" + (f" ومعاملة {t['code']}" if t else ""))
        return lid

    # ---------- group messages ----------
    def segment_recipients(self, key, param=None):
        """-> list of {name, phone, ref_type, ref_id, extra} for a segment."""
        out, seen = [], set()

        def add(name, phone, rtype, rid, extra=""):
            k = phone_key(phone)
            if not k or k in seen:
                return
            seen.add(k)
            out.append({"name": name, "phone": phone, "ref_type": rtype, "ref_id": rid, "extra": extra})

        if key in ("open_leads", "stage", "lost_leads"):
            flt = {"open_leads": "open", "stage": "all", "lost_leads": "lost"}[key]
            for r in self.leads(flt=flt, stage=param if key == "stage" else None):
                add(r["name"], r["phone"], "lead", r["id"], r["service"])
        elif key == "expiring":
            for it in self.expiring_items(days=int(param or 60)):
                add(it["client_name"], it["phone"], "client", it["client_id"],
                    f"{it['doc_type'].split('|')[0]} {it['expiry']}")
        elif key == "all_clients":
            for c in self.clients():
                if (c["status"] or "active") == "active":
                    add(c["name"], c["phone"], "client", c["id"])
        elif key == "inactive":
            months = int(param or 6)
            limit = (dt.date.today() - dt.timedelta(days=30 * months)).isoformat()
            last = {r["client_id"]: r["d"] for r in self.q(
                "SELECT client_id, MAX(substr(created,1,10)) d FROM transactions GROUP BY client_id")}
            for c in self.clients():
                if (c["status"] or "active") == "active" and last.get(c["id"], "") < limit:
                    add(c["name"], c["phone"], "client", c["id"],
                        last.get(c["id"]) or "")
        elif key == "balance":
            if self.can("money"):
                for b in self.outstanding_by_client():
                    add(b["name"], b["phone"], "client", b["client_id"], f"{b['amount']:,.2f}")
        return out

    def save_campaign(self, data, cid=None):
        u = self.user
        rec = json.dumps(data.get("recipients", []), ensure_ascii=False)
        if cid:
            self.x("UPDATE crm_campaigns SET recipients=? WHERE id=?", (rec, cid))
            return cid
        return self.x("INSERT INTO crm_campaigns(title, segment, message, recipients, created, user_id, "
                      "user_name) VALUES(?,?,?,?,?,?,?)",
                      (data.get("title", ""), data.get("segment", ""), data.get("message", ""), rec, _now(),
                       u["id"] if u is not None else None,
                       (u["full_name"] or u["username"]) if u is not None else ""))

    def campaigns(self):
        return self.q("SELECT * FROM crm_campaigns ORDER BY id DESC")

    def campaign(self, cid):
        return self.one("SELECT * FROM crm_campaigns WHERE id=?", (cid,))

    # ---------- reports ----------
    def crm_report(self, date_from, date_to):
        """Leads created in the period, and deals closed in the period."""
        stages = self.crm_stages()
        won, lost = self.crm_won(), self.crm_lost()
        created = self.leads(flt="all", date_from=date_from, date_to=date_to)
        all_leads = self.leads(flt="all")
        closed = [r for r in all_leads if r["closed_date"] and date_from <= r["closed_date"] <= date_to]
        w = [r for r in closed if r["stage"] == won]
        lo = [r for r in closed if r["stage"] == lost]
        open_now = [r for r in all_leads if r["stage"] not in (won, lost)]

        def rate(a, b):
            return round(100.0 * a / (a + b), 1) if (a + b) else 0.0

        days = []
        for r in w:
            try:
                days.append((dt.date.fromisoformat(r["closed_date"]) -
                             dt.date.fromisoformat(r["created"][:10])).days)
            except (TypeError, ValueError):
                pass
        pipeline = [{"stage": s, "count": sum(1 for r in open_now if r["stage"] == s),
                     "value": sum(r["value"] or 0 for r in open_now if r["stage"] == s)}
                    for s in stages[:-2]]

        created_ids = {r["id"] for r in created}
        won_ids = {r["id"] for r in w}
        lost_ids = {r["id"] for r in lo}

        def by(key, label_none):
            groups = {}
            for r in created + [x for x in closed if x["id"] not in created_ids]:
                g = groups.setdefault(r[key] or label_none, {"leads": 0, "won": 0, "lost": 0, "value": 0.0})
                if r["id"] in created_ids:
                    g["leads"] += 1
                if r["id"] in won_ids:
                    g["won"] += 1
                    g["value"] += r["value"] or 0
                if r["id"] in lost_ids:
                    g["lost"] += 1
            rows = []
            for k, g in groups.items():
                g["rate"] = rate(g["won"], g["lost"])
                rows.append((k, g))
            return sorted(rows, key=lambda kv: (-kv[1]["won"], -kv[1]["leads"]))

        reps = by("assigned_name", "— غير مسند —|— unassigned —")
        acts = {}
        for a in self.q("SELECT user_name, COUNT(*) n FROM crm_activities WHERE kind<>'stage' "
                        "AND substr(ts,1,10) BETWEEN ? AND ? GROUP BY user_name", (date_from, date_to)):
            acts[a["user_name"]] = a["n"]
        reasons = {}
        for r in lo:
            reasons[r["lost_reason"] or "—"] = reasons.get(r["lost_reason"] or "—", 0) + 1
        monthly = {}
        for r in w:
            m = r["closed_date"][:7]
            monthly.setdefault(m, [0, 0.0])
            monthly[m][0] += 1
            monthly[m][1] += r["value"] or 0
        overdue = [r for r in open_now if r["next_follow_up"] and r["next_follow_up"] < _today()]
        return {
            "new_leads": len(created), "won": len(w), "lost": len(lo), "won_value": sum(r["value"] or 0 for r in w),
            "rate": rate(len(w), len(lo)), "avg_days": round(sum(days) / len(days), 1) if days else 0,
            "open": len(open_now), "open_value": sum(r["value"] or 0 for r in open_now),
            "overdue": len(overdue), "pipeline": pipeline,
            "by_source": by("source", "—"), "by_rep": reps, "activities_by_user": acts,
            "lost_reasons": sorted(reasons.items(), key=lambda kv: -kv[1]),
            "monthly_won": sorted(monthly.items()),
        }
