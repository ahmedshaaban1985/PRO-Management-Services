# -*- coding: utf-8 -*-
"""Server-side helpers the browser calls through /api/call (name starts with "op_")."""
import json

import core
import users as users_mod
from companies import PROFILE_KEYS, CURRENCIES

LIST_KEYS = ("tx_types", "stages", "doc_types", "file_cats", "entities", "pay_methods",
             "crm_stages", "crm_sources", "crm_lost_reasons")
# key -> permission needed to change it; "main" = only in the main company's settings
SETTINGS = {
    "alert_days": "settings", "due_alert_days": "settings", "idle_minutes": "settings",
    "language": "settings", "quote_terms": "settings", "invoice_notes": "settings",
    "use_letterhead": "settings", "lh_top_mm": "settings", "lh_bottom_mm": "settings", "lh_side_mm": "settings",
    "lock_date": "accounting_edit", "auto_backup_days": "settings",
    "crm_auto_assign": "settings",
    # renewals / WhatsApp API (main)
    "rem_days": "settings", "rem_types": "settings", "rem_weekly_after": "settings", "rem_message": "settings",
    "rem_auto": "main", "rem_time": "main",
    "wa_api_on": "main", "wa_token": "main", "wa_phone_id": "main", "wa_template": "main",
    "wa_template_lang": "main", "wa_api_version": "main",
    # WhatsApp inbox (Kapso) - main
    "inbox_on": "main", "kapso_api_key": "main", "kapso_phone_id": "main", "inbox_days_back": "main",
    "inbox_autosave": "main", "inbox_sound": "main",
    # internet leads - main
    "web_leads_on": "main", "web_leads_url": "main", "web_leads_minutes": "main",
    "meta_on": "main", "meta_minutes": "main",
    "lead_welcome_on": "main", "lead_welcome_template": "main", "lead_welcome_lang": "main",
    "lead_welcome_name": "main",
}
SECRET = {"wa_token", "kapso_api_key"}
READ_ONLY = ("web_leads_key", "web_leads_status", "meta_status", "meta_page_name", "meta_page_id",
             "inbox_status", "last_full_backup", "letterhead_pdf", "logo_path", "rem_last_auto")


def op_settings_get(ctx, server):
    db, m = ctx.db, server.main_db()
    out = {}
    for k in list(SETTINGS) + list(LIST_KEYS) + list(PROFILE_KEYS) + list(READ_ONLY):
        src = m if (SETTINGS.get(k) == "main" or k in READ_ONLY[:6] or k in ("inbox_status",)) else db
        v = src.get(k)
        if k in SECRET and v:
            v = "••••••" + v[-4:]
        out[k] = v
    if not out.get("web_leads_key"):
        import weblead
        m.set("web_leads_key", weblead.new_key())
        out["web_leads_key"] = m.get("web_leads_key")
    if not ctx.can("settings"):
        # keys, tokens and the main company's connections are not for everybody
        for k in list(out):
            if k in SECRET or k in READ_ONLY[:6] or k == "inbox_status" or \
                    (db is not m and (SETTINGS.get(k) == "main")):
                out.pop(k, None)
    out["_main"] = db is m
    out["_currencies"] = [[k] + list(v) for k, v in CURRENCIES.items()]
    out["_company_kind"] = db.company_kind
    return out


def op_settings_set(ctx, server, values):
    db, m = ctx.db, server.main_db()
    changed = []
    for k, v in (values or {}).items():
        v = "" if v is None else str(v)
        if k in LIST_KEYS or k in PROFILE_KEYS:
            if not db.can("settings"):
                raise users_mod.NotAllowed("settings")
            if k in LIST_KEYS:
                v = "\n".join(x.strip() for x in v.splitlines() if x.strip())
                if not v:
                    continue
            if k in PROFILE_KEYS:
                continue        # profile goes through op_profile_save
            db.set(k, v)
            changed.append(k)
            continue
        perm = SETTINGS.get(k)
        if perm is None:
            raise ValueError(f"setting {k}")
        if perm == "main":
            if not (db.can("settings") and db is m):
                raise users_mod.NotAllowed("settings")
            if k in SECRET and v.startswith("••••"):
                continue
            m.set(k, v)
        else:
            if not db.can(perm):
                raise users_mod.NotAllowed(perm)
            if k == "lock_date":
                from util import normalize_date
                nv = normalize_date(v)
                if nv is None:
                    raise ValueError("date")
                if nv != db.get("lock_date"):
                    db.audit("settings", "قفل الفترات|Period lock", None, nv or "-")
                v = nv
            db.set(k, v)
        changed.append(k)
    if changed:
        db.audit("settings", "الإعدادات|Settings", None, "", ", ".join(changed)[:200])
    return changed


def op_profile_save(ctx, server, profile, company_id=None):
    """Company details (of the open company, or another one for the companies screen)."""
    m = server.main_db()
    cid = int(company_id or ctx.db.company_id)
    if cid == ctx.db.company_id:
        if not ctx.db.can("settings") and not m.can("companies"):
            raise users_mod.NotAllowed("settings")
        db = ctx.db
    else:
        if not m.can("companies"):
            raise users_mod.NotAllowed("companies")
        db = server.company_db(cid)
        db.user, db._perms = ctx.db.user, ctx.db._perms
    prof = {k: profile.get(k) for k in PROFILE_KEYS if k in profile}
    if profile.get("company_kind") in ("clearing", "business"):
        prof["company_kind"] = profile["company_kind"]
    db.save_profile(prof)
    db.audit("settings", "بيانات الشركة|Company details", cid, db.company_name("ar"))
    return True


def op_profile_get(ctx, server, company_id=None):
    m = server.main_db()
    cid = int(company_id or ctx.db.company_id)
    if cid != ctx.db.company_id and not m.can("companies"):
        raise users_mod.NotAllowed("companies")
    db = ctx.db if cid == ctx.db.company_id else server.company_db(cid)
    out = {k: db.get(k) for k in PROFILE_KEYS}
    out["company_kind"] = db.company_kind
    return out


def op_create_company(ctx, server, profile, user_ids=None):
    m = server.main_db()
    if not m.can("companies"):
        raise users_mod.NotAllowed("companies")
    return m.create_company(profile, user_ids or [])


def op_perm_catalog(ctx, server):
    return {"perms": [list(p) for p in users_mod.PERMISSIONS],
            "roles": {k: [v[0], v[1], v[2]] for k, v in users_mod.ROLES.items()},
            "own_only": users_mod.OWN_ONLY, "view_only": users_mod.VIEW_ONLY}


def op_dashboard(ctx, server):
    """Everything the Today page needs in one call."""
    db = ctx.db
    can = db.can
    out = {"today": core.iso(core.today())}
    if db.is_business:
        out["business"] = True
        return out
    if can("tx_view"):
        out["open"] = len(db.transactions(flt="open"))
        out["overdue"] = len(db.transactions(flt="overdue"))
        rows = sorted(db.transactions(), key=lambda r: r["id"], reverse=True)[:6]
        out["recent"] = [{"id": r["id"], "code": r["code"], "client_name": r["client_name"], "tx_type": r["tx_type"],
                          "status": r["status"], "due_date": r["due_date"], "created": r.get("created"),
                          "is_open": db.is_open_status(r["status"])} for r in rows]
        out["action"] = [dict(r) for r in db.action_needed()]
    items = db.expiring_items()
    out["expiring"] = [dict(i) for i in items]
    if can("tasks"):
        out["tasks"] = [dict(t) for t in db.tasks("today")]
    if can("crm"):
        out["leads"] = [dict(r) for r in db.follow_ups_due()]
    if can("money"):
        out["outstanding"] = db.finance_summary()["outstanding"]
    out["final_stage"] = db.final_stage()
    return out


def op_lead_counts(ctx, server):
    return {"follow_ups": len(ctx.db.follow_ups_due())}


OPS = {k: v for k, v in globals().items() if k.startswith("op_")}


# ---------------------------------------------------------------- message placeholders
import re as _re


def _L(v, lang):
    return core.split_label(v or "", lang)


def _money(v):
    return f"{(v or 0):,.2f}"


def placeholder_values(db, lang, client_id=None, tx_id=None, employee_id=None, extra=None):
    v = {"company": db.company_name(lang), "date": core.iso(core.today()),
         "client": "", "code": "", "phone": "", "cr": "", "tx_type": "",
         "tx_code": "", "status": "", "entity": "", "amount": "", "remaining": "",
         "employee": "", "doc": "", "expiry": ""}
    if client_id:
        c = db.client(client_id)
        if c:
            v.update(client=c["name"], code=c["code"], phone=c["phone"], cr=c["cr_number"])
            if db.can("money") or db.user is None:
                owed = sum(a["amount"] for a in db.outstanding_by_client() if a["client_id"] == client_id)
                v["remaining"] = _money(owed) if owed else ""
    if tx_id:
        t = db.transaction(tx_id)
        if t:
            v.update(tx_type=_L(t["tx_type"], lang), tx_code=t["code"], status=_L(t["status"], lang),
                     entity=_L(t["entity"], lang))
            if db.can("money") or db.user is None:
                v["amount"] = _money(db.tx_due(tx_id))
                rem = db.tx_due(tx_id) - db.paid_total(tx_id)
                if rem > 0:
                    v["remaining"] = _money(rem)
    if employee_id:
        e = db.employee(employee_id)
        if e:
            v["employee"] = e["name"]
    v.update(extra or {})
    return v


def fill_placeholders(text, values):
    def rep(m):
        k = m.group(1)
        if k not in values:
            return m.group(0)
        x = values.get(k)
        return str(x) if x not in (None, "") else "........"
    return _re.sub(r"\{(\w+)\}", rep, text or "")


def op_fill(ctx, server, body, client_id=None, tx_id=None, employee_id=None, extra=None):
    return fill_placeholders(body, placeholder_values(ctx.db, ctx.lang, client_id, tx_id, employee_id, extra))


def op_expiry_message(ctx, server, item):
    db, lang = ctx.db, ctx.lang
    tpl = next((t for t in db.templates("whatsapp") if t["category"] == "reminder"), None)
    body = tpl["body"] if tpl else "السلام عليكم {client}،\n{doc} ينتهي بتاريخ {expiry}.\n{company}"
    who = item.get("holder")
    doc = _L(item.get("doc_type"), lang)
    if who and who != item.get("client_name"):
        doc = f"{doc} ({who})"
    if item.get("number"):
        doc += (f" no. {item['number']}" if lang == "en" else f" رقم {item['number']}")
    return fill_placeholders(body, placeholder_values(db, lang, item.get("client_id"),
                                                      extra={"doc": doc, "expiry": item.get("expiry")}))


def op_tx_update_message(ctx, server, tx_id):
    db, lang = ctx.db, ctx.lang
    t = db.transaction(tx_id)
    c = db.client(t["client_id"])
    who = ""
    if t["employee_id"]:
        e = db.employee(t["employee_id"])
        who = e["name"] if e else ""
    if lang == "en":
        text = (f"Dear {c['name']},\nYour transaction {t['code']} ({_L(t['tx_type'], lang)})"
                f"{' - ' + who if who else ''} is now at: {_L(t['status'], lang)}.\n"
                f"We will keep you updated.\n{db.company_name('en')}")
    else:
        text = (f"السلام عليكم {c['name']}،\nنود إفادتكم بأن معاملة {t['code']} ({_L(t['tx_type'], lang)})"
                f"{' - ' + who if who else ''} وصلت إلى مرحلة: {_L(t['status'], lang)}.\n"
                f"وسنوافيكم بأي جديد أولًا بأول.\n{db.company_name('ar')}")
    return {"phone": c["phone"], "text": text}


OPS = {k: v for k, v in globals().items() if k.startswith("op_")}


def op_account_choices(ctx, server, types=None, money_only=False, include_ids=()):
    """[[id, label]] of leaf accounts for pickers (no accounting permission needed)."""
    db = ctx.db
    out, seen = [], set()
    for a in db.accounts(types=tuple(types) if types else None, leaf_only=True, money_only=money_only):
        out.append([a["id"], db.account_label(a["id"], ctx.lang)])
        seen.add(a["id"])
    for aid in include_ids or ():
        if aid and aid not in seen:
            out.append([aid, db.account_label(aid, ctx.lang)])
    return out


def op_sys_accounts(ctx, server):
    db = ctx.db
    keys = ("cash", "bank", "revenue", "disb", "other_exp", "ar", "ap", "sales", "purchases", "inventory",
            "vat_out", "vat_in", "cogs")
    out = {}
    for k in keys:
        try:
            out[k] = db.sys_account(k)
        except Exception:       # noqa: BLE001
            out[k] = None
    return out


def op_receipt_links(ctx, server, client_id, pid=None):
    """What a receipt can be linked to: unpaid transactions and invoices of the client."""
    db = ctx.db
    out = []
    row = db.payment(pid) if pid else None
    if row and row["client_id"] == client_id:
        if row["invoice_id"]:
            out.append({"kind": "inv", "id": row["invoice_id"], "label": f"{row['invoice_code']}", "rem": 0, "current": True})
        elif row["tx_id"]:
            out.append({"kind": "tx", "id": row["tx_id"], "label": f"{row['tx_code']} — {_L(row['tx_type'], ctx.lang)}",
                        "rem": 0, "current": True})
    for r in db.transactions(flt="unpaid", client_id=client_id):
        out.append({"kind": "tx", "id": r["id"], "label": f"{r['code']} — {_L(r['tx_type'], ctx.lang)}", "rem": r["remaining"]})
    for i in db.invoices(client_id=client_id):
        if i["status"] == "cancelled" or i["tx_id"]:
            continue
        rem = core.doc_total(i) - (i["paid"] or 0)
        if rem > 0.005:
            out.append({"kind": "inv", "id": i["id"], "label": f"{i['code']}", "rem": rem})
    return out


def op_method_account(ctx, server, method):
    return ctx.db.money_account_for_method(method)


OPS = {k: v for k, v in globals().items() if k.startswith("op_")}


def op_account_name(ctx, server, aid):
    a = ctx.db.account(aid)
    return f"{a['name_ar']}|{a['name_en']}" if a else ""


OPS = {k: v for k, v in globals().items() if k.startswith("op_")}


def op_client_balance(ctx, server, client_id):
    """Accounting balance of one client (+ = owes us, - = we owe him)."""
    db = ctx.db
    if not (db.can("receipts") or db.can("money") or db.can("accounting")):
        raise users_mod.NotAllowed("money")
    return db.ar_balances().get(client_id, 0)


OPS = {k: v for k, v in globals().items() if k.startswith("op_")}


def op_renewal_consts(ctx, server):
    import renewals
    return {"types": [list(t[:5]) for t in renewals.TYPES], "statuses": [list(s) for s in renewals.STATUSES],
            "channels": {k: list(v) for k, v in renewals.CHANNELS.items()}, "default_message": renewals.DEFAULT_MESSAGE,
            "api_template": renewals.API_TEMPLATE_TEXT, "wa_ready": bool(server.main_db().wa_ready()),
            "rem_auto": server.main_db().get("rem_auto"), "rem_time": server.main_db().get("rem_time") or "09:00"}


def op_reminder_text(ctx, server, group):
    if not ctx.db.can("renewals"):
        raise users_mod.NotAllowed("renewals")
    return {"text": ctx.db.reminder_text(group), "params": ctx.db.api_params(group)}


def op_renewal_letter_text(ctx, server, client_id, items):
    db = ctx.db
    c = db.client(client_id)
    lines = "\n".join("- " + db.item_line(i) for i in items)
    return (f"السادة / {c['name']} المحترمين،\nتحية طيبة وبعد،\n\n"
            f"نود إحاطتكم علمًا بأن المستندات التالية الخاصة بكم انتهت أو قاربت على الانتهاء:\n\n{lines}\n\n"
            f"ونأمل التكرم بتزويدنا بالمستندات المطلوبة لنقوم بإجراءات التجديد في أقرب وقت.\n\n"
            f"وتفضلوا بقبول فائق الاحترام،\n{db.company_name('ar')}")


OPS = {k: v for k, v in globals().items() if k.startswith("op_")}


def op_payment_reminder(ctx, server, client_id):
    db = ctx.db
    tpl = next((t for t in db.templates("whatsapp") if t["category"] == "payment"), None)
    text = fill_placeholders(tpl["body"], placeholder_values(db, ctx.lang, client_id)) if tpl else ""
    c = db.client(client_id)
    return {"phone": c["phone"] if c else "", "text": text}


OPS = {k: v for k, v in globals().items() if k.startswith("op_")}


def op_acc_consts(ctx, server):
    from accounting import TYPES
    return {"types": {k: list(v) for k, v in TYPES.items()}}


def op_account_flat(ctx, server, date_to=None):
    """Chart of accounts as a flat list (with level) for the tree view."""
    if not ctx.db.can("accounting"):
        raise users_mod.NotAllowed("accounting")
    roots, _ = ctx.db.account_tree(date_to)
    out = []

    def walk(a, lvl):
        out.append({k: a[k] for k in ("id", "code", "name_ar", "name_en", "type", "parent_id", "is_group",
                                      "is_money", "active", "sys_key", "total", "notes")} | {"level": lvl})
        for ch in a["children"]:
            walk(ch, lvl + 1)
    for a in roots:
        walk(a, 0)
    return out


def op_save_account(ctx, server, data, aid=None):
    db = ctx.db
    if not db.can("accounting_edit"):
        raise users_mod.NotAllowed("accounting_edit")
    code = (data.get("code") or "").strip()
    if not code or not (data.get("name_ar") or "").strip():
        raise ValueError("اكتب رقم الحساب واسمه." if ctx.lang == "ar" else "Enter code and name.")
    other = db.one("SELECT id FROM accounts WHERE code=?", (code,))
    if other and other["id"] != aid:
        raise ValueError("رقم الحساب مستخدم لحساب آخر." if ctx.lang == "ar" else "This code is already used.")
    if data.get("is_group") and aid and db.one("SELECT 1 FROM jlines WHERE account_id=? LIMIT 1", (aid,)):
        raise ValueError("الحساب عليه قيود، ماينفعش يتحول لحساب تجميعي." if ctx.lang == "ar"
                         else "The account has postings; it cannot become a group.")
    return db.save_account(data, aid)


def op_group_accounts(ctx, server):
    return [[a["id"], f"{a['code']} - {a['name_en'] if ctx.lang == 'en' and a['name_en'] else a['name_ar']}"]
            for a in ctx.db.accounts(active_only=False) if a["is_group"]]


OPS = {k: v for k, v in globals().items() if k.startswith("op_")}


def op_users_list(ctx, server):
    m = server.main_db()
    if not m.can("users"):
        raise users_mod.NotAllowed("users")
    counts = {r["assigned_user_id"]: r["n"] for r in ctx.db.q(
        "SELECT assigned_user_id, COUNT(*) n FROM transactions WHERE assigned_user_id IS NOT NULL GROUP BY assigned_user_id")}
    comps = {}
    for r in m.q("SELECT user_id, company_id FROM user_companies"):
        comps.setdefault(r["user_id"], []).append(r["company_id"])
    out = []
    for u in m.users():
        d = {k: u[k] for k in ("id", "username", "full_name", "role", "perms", "active", "phone", "last_login")}
        d["tx_count"] = counts.get(u["id"], 0)
        d["companies"] = comps.get(u["id"], [])
        out.append(d)
    return {"users": out, "actions": {k: list(v) for k, v in users_mod.ACTIONS.items()}}


def op_set_user_companies(ctx, server, uid, company_ids):
    m = server.main_db()
    if not m.can("users"):
        raise users_mod.NotAllowed("users")
    with m.atomic():
        m.con.execute("DELETE FROM user_companies WHERE user_id=?", (uid,))
        for cid in set(int(c) for c in company_ids or []):
            m.con.execute("INSERT OR IGNORE INTO user_companies(user_id, company_id) VALUES(?,?)", (uid, cid))
    m.audit("update", "مستخدم|User", uid, "", "الشركات")
    return True


def op_new_recovery_code(ctx, server):
    m = server.main_db()
    if not m.can("users"):
        raise users_mod.NotAllowed("users")
    return m.make_recovery_code()


def op_web_sessions(ctx, server):
    """Who is logged in now (devices)."""
    m = server.main_db()
    if not m.can("users"):
        raise users_mod.NotAllowed("users")
    import time as _t
    rows = m.q("""SELECT s.token, s.user_id, s.ip, s.agent, s.created, s.last_seen, u.username, u.full_name
                  FROM web_sessions s LEFT JOIN users u ON u.id=s.user_id ORDER BY s.last_seen DESC""")
    return [{"id": r["token"][:10], "user": r["full_name"] or r["username"], "ip": r["ip"], "agent": r["agent"],
             "created": _t.strftime("%Y-%m-%d %H:%M", _t.localtime(r["created"] or 0)),
             "last_seen": _t.strftime("%Y-%m-%d %H:%M", _t.localtime(r["last_seen"] or 0)),
             "me": r["token"] == ctx.token} for r in rows]


def op_end_session(ctx, server, sid):
    m = server.main_db()
    if not m.can("users"):
        raise users_mod.NotAllowed("users")
    m.x("DELETE FROM web_sessions WHERE substr(token,1,10)=? AND token<>?", (sid, ctx.token))
    return True


OPS = {k: v for k, v in globals().items() if k.startswith("op_")}


def op_company_consts(ctx, server):
    from companies import COUNTRIES, CURRENCIES
    from business import KINDS
    return {"countries": [list(c) for c in COUNTRIES], "currencies": [[k] + list(v) for k, v in CURRENCIES.items()],
            "kinds": {k: list(v) for k, v in KINDS.items()}}


def op_companies_list(ctx, server):
    m = server.main_db()
    out = []
    allowed = None
    if ctx.user["role"] != "admin" and not ctx.can("companies"):
        allowed = {c["id"] for c in m.companies_for(ctx.user)}
    for c in m.companies():
        if allowed is not None and c["id"] not in allowed:
            continue
        d = dict(c)
        d["users"] = sorted(m.company_users(c["id"]))
        out.append(d)
    return out


OPS = {k: v for k, v in globals().items() if k.startswith("op_")}


def op_sheet_script(ctx, server, new_key=False):
    import weblead
    m = server.main_db()
    if not m.can("settings"):
        raise users_mod.NotAllowed("settings")
    if new_key or not m.get("web_leads_key"):
        m.set("web_leads_key", weblead.new_key())
    return {"key": m.get("web_leads_key"), "script": weblead.script_for(m.get("web_leads_key"))}


def op_letterhead_info(ctx, server):
    import printing
    db = ctx.db
    return {"has_letterhead": bool(printing.letterhead_file(db)), "has_logo": bool(printing.logo_file(db)),
            "uploaded_letterhead": bool(db.get("letterhead_pdf"))}


def op_clear_branding(ctx, server, kind):
    db = ctx.db
    if not db.can("settings"):
        raise users_mod.NotAllowed("settings")
    if kind == "logo":
        db.set("logo_path", "")
    else:
        db.set("letterhead_pdf", "")
        db.set("use_letterhead", "0")
    return True


OPS = {k: v for k, v in globals().items() if k.startswith("op_")}


def op_invoice_for_tx(ctx, server, tid, create=False):
    """The invoice of a transaction ({id, code} or None). create=True makes it in one step
    from the transaction itself: one line (the transaction type at the office fee) + the government fees."""
    import datetime as _dt
    db = ctx.db
    if not (db.can("sales_view") or db.can("sales_edit")):
        raise users_mod.NotAllowed("sales_view")
    r = db.one("SELECT id, code FROM invoices WHERE tx_id=? AND status<>'cancelled' ORDER BY id LIMIT 1", (tid,))
    if r or not create:
        return dict(r) if r else None
    if not db.can("sales_edit"):
        raise users_mod.NotAllowed("sales_edit")
    t = db.transaction(tid)
    if not t:
        raise ValueError("not found")
    lang = ctx.lang if getattr(ctx, "lang", None) else "ar"
    desc = _L(t["tx_type"], lang) or ("خدمة" if lang == "ar" else "Service")
    if t["employee_name"] if "employee_name" in t.keys() else None:
        desc += f" - {t['employee_name']}"
    today = core.today()
    iid = db.save_invoice({
        "client_id": t["client_id"], "tx_id": tid, "date": core.iso(today),
        "due_date": core.iso(today + _dt.timedelta(days=7)),
        "items": [{"desc": desc, "qty": 1, "price": float(t["fee"] or 0)}],
        "gov_fees": float(t["gov_fees"] or 0), "discount": 0, "notes": db.get("invoice_notes") or "",
        "status": "issued", "revenue_account_id": t["revenue_account_id"]})
    inv = db.invoice(iid)
    db.audit("create", "فاتورة|Invoice", iid, inv["code"])
    return {"id": iid, "code": inv["code"], "created": True}


OPS = {k: v for k, v in globals().items() if k.startswith("op_")}

from ops_biz import BIZ_OPS  # noqa: E402
OPS.update(BIZ_OPS)
from ops_req import REQ_OPS  # noqa: E402
OPS.update(REQ_OPS)
