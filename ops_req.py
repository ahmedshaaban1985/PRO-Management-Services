# -*- coding: utf-8 -*-
"""طلبات الخدمة بين المكتب وشركات العملاء - service requests between the office and client companies.

Two ways:
  in  : a client company asks the office (renew my C.R., my license, an employee's residence...)
        -> the office accepts (a transaction is opened for that client) or refuses.
  out : the office asks a client company for approval (we need to renew X, fees Y, ok?)
        -> the company approves or refuses -> the office starts it (a transaction is opened).
Everything lives in the main database, so both sides see the same request at the same moment.
"""
import datetime as dt

import users as users_mod

PERM = "requests"
STATUSES = {
    "pending": ("بانتظار الرد", "Waiting for reply"),
    "approved": ("وافقت الشركة - بانتظار البدء", "Approved - waiting to start"),
    "accepted": ("جاري التنفيذ", "In progress"),
    "done": ("تم التنفيذ", "Done"),
    "rejected": ("مرفوض", "Refused"),
    "cancelled": ("ملغي", "Cancelled"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS service_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT, company_id INTEGER, client_id INTEGER,
    direction TEXT,                 -- 'in' = from the company to the office, 'out' = from the office
    req_type TEXT DEFAULT '', details TEXT DEFAULT '',
    fee REAL DEFAULT 0, gov_fees REAL DEFAULT 0, wanted_date TEXT DEFAULT '',
    status TEXT DEFAULT 'pending', tx_id INTEGER,
    created TEXT, created_by INTEGER, created_name TEXT DEFAULT '',
    reply TEXT DEFAULT '', replied TEXT DEFAULT '', replied_name TEXT DEFAULT '',
    done TEXT DEFAULT ''
);
"""


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _m(server):
    m = server.main_db()
    if not getattr(m, "_req_ready", False):
        m.con.executescript(SCHEMA)
        cols = {r[1] for r in m.con.execute("PRAGMA table_info(companies)")}
        if "client_id" not in cols:        # which office client this company is
            m.con.execute("ALTER TABLE companies ADD COLUMN client_id INTEGER")
        m._commit()
        m._req_ready = True
    return m


def _side(ctx, server):
    """'office' when working inside the main company, else 'company'."""
    if not ctx.can(PERM):
        raise users_mod.NotAllowed(PERM)
    return "office" if ctx.db is server.main_db() else "company"


def _uname(ctx):
    u = ctx.user
    return (u["full_name"] or u["username"]) if u else ""


def _row(m, rid):
    r = m.one("SELECT * FROM service_requests WHERE id=?", (rid,))
    if r is None:
        raise ValueError("not_found")
    return r


def _check_owner(ctx, server, side, r):
    if side == "company" and r["company_id"] != ctx.db.company_id:
        raise users_mod.NotAllowed(PERM)


def _notify(server, r, title, to):
    """to = 'office' or 'company'."""
    m = server.main_db()
    comp = m.one("SELECT name_ar FROM companies WHERE id=?", (r["company_id"],))
    body = f"{r['code']} — {r['req_type']}" + (f" — {comp['name_ar']}" if comp and to == "office" else "")
    server.note(title, body, "request", "/requests", 1 if to == "office" else r["company_id"], PERM)


def _companies(m):
    return m.q("SELECT id, name_ar, name_en, kind, client_id FROM companies WHERE id<>1 AND active=1 ORDER BY name_ar")


# ---------------------------------------------------------------------------------- reading
def op_req_consts(ctx, server):
    side = _side(ctx, server)
    m = _m(server)
    out = {"side": side, "statuses": {k: list(v) for k, v in STATUSES.items()},
           "types": m.get_list("tx_types")}
    if side == "office":
        out["companies"] = [dict(c) for c in _companies(m)]
    return out


def op_req_list(ctx, server, status=""):
    side = _side(ctx, server)
    m = _m(server)
    sql = """SELECT r.*, c.name_ar AS company_name, cl.name AS client_name, t.code AS tx_code, t.status AS tx_status
             FROM service_requests r LEFT JOIN companies c ON c.id=r.company_id
             LEFT JOIN clients cl ON cl.id=r.client_id LEFT JOIN transactions t ON t.id=r.tx_id WHERE 1=1"""
    args = []
    if side == "company":
        sql += " AND r.company_id=?"
        args.append(ctx.db.company_id)
    rows = [dict(r) for r in m.q(sql + " ORDER BY r.id DESC", args)]
    final = m.final_stage()
    for r in rows:
        if r["status"] == "accepted" and r["tx_status"] and r["tx_status"] == final:
            r["status"] = "done"          # the office finished the transaction
        if side == "company":             # the company does not see the office's internal codes
            r.pop("client_name", None)
            r.pop("tx_status", None)
    if status == "open":
        rows = [r for r in rows if r["status"] in ("pending", "approved", "accepted")]
    elif status:
        rows = [r for r in rows if r["status"] == status]
    return rows


def op_req_counts(ctx, server):
    """Waiting for MY answer - for the badge."""
    if not ctx.can(PERM):
        return {"waiting": 0}
    side = "office" if ctx.db is server.main_db() else "company"
    m = _m(server)
    if side == "office":
        n = m.one("SELECT COUNT(*) n FROM service_requests WHERE (direction='in' AND status='pending') "
                  "OR (direction='out' AND status='approved')")["n"]
    else:
        n = m.one("SELECT COUNT(*) n FROM service_requests WHERE company_id=? AND direction='out' "
                  "AND status='pending'", (ctx.db.company_id,))["n"]
    return {"waiting": n}


# ---------------------------------------------------------------------------------- writing
def op_req_save(ctx, server, data):
    """New request. Office: data.company_id required (+ fee). Company: its own company."""
    side = _side(ctx, server)
    m = _m(server)
    d = data or {}
    req_type = (d.get("req_type") or "").strip()
    if not req_type:
        raise ValueError("req_type")
    if side == "office":
        cid = int(d.get("company_id") or 0)
        comp = m.one("SELECT * FROM companies WHERE id=? AND id<>1", (cid,))
        if comp is None:
            raise ValueError("company")
        direction, client_id = "out", d.get("client_id") or comp["client_id"]
        if d.get("client_id") and not comp["client_id"]:
            m.x("UPDATE companies SET client_id=? WHERE id=?", (int(d["client_id"]), cid))
    else:
        cid, direction = ctx.db.company_id, "in"
        comp = m.one("SELECT * FROM companies WHERE id=?", (cid,))
        client_id = comp["client_id"] if comp else None
    fee = float(d.get("fee") or 0) if side == "office" else 0
    gov = float(d.get("gov_fees") or 0) if side == "office" else 0
    rid = m.x("""INSERT INTO service_requests(company_id, client_id, direction, req_type, details, fee, gov_fees,
                 wanted_date, status, created, created_by, created_name) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (cid, client_id, direction, req_type, (d.get("details") or "").strip(), fee, gov,
               d.get("wanted_date") or "", "pending", _now(), ctx.user["id"], _uname(ctx)))
    m.x("UPDATE service_requests SET code=? WHERE id=?", (f"REQ-{rid:05d}", rid))
    r = _row(m, rid)
    if side == "office":
        _notify(server, r, "📨 طلب موافقة جديد من المكتب", "company")
    else:
        _notify(server, r, "📨 طلب جديد من شركة", "office")
    return rid


def _set(m, rid, ctx, status, reply=None, **extra):
    sets, args = ["status=?", "replied=?", "replied_name=?"], [status, _now(), _uname(ctx)]
    if reply is not None:
        sets.append("reply=?")
        args.append(reply)
    for k, v in extra.items():
        sets.append(f"{k}=?")
        args.append(v)
    m.x(f"UPDATE service_requests SET {', '.join(sets)} WHERE id=?", (*args, rid))


def _open_transaction(ctx, server, m, r, client_id, fee, gov):
    """Office only: a transaction for the request's client (normal permission checks apply)."""
    if not client_id:
        raise ValueError("client_missing")
    stages = m.get_list("stages")
    note = f"{r['code']}: {r['details']}".strip()
    tid = m.save_transaction({
        "client_id": int(client_id), "tx_type": r["req_type"], "status": stages[0] if stages else "",
        "start_date": dt.date.today().isoformat(),
        "due_date": r["wanted_date"] or (dt.date.today() + dt.timedelta(days=14)).isoformat(),
        "fee": float(fee or 0), "gov_fees": float(gov or 0), "notes": note,
        "revenue_account_id": m.sys_account("revenue"), "gov_account_id": m.sys_account("cash"),
    })
    m.x("UPDATE companies SET client_id=COALESCE(client_id, ?) WHERE id=?", (int(client_id), r["company_id"]))
    return tid


def op_req_respond(ctx, server, rid, action, data=None):
    """action:
      office : accept (in/pending or out/approved -> opens a transaction), reject (in/pending),
               done (accepted), cancel (own out request not started)
      company: approve / reject (out/pending), cancel (own in/pending)"""
    side = _side(ctx, server)
    m = _m(server)
    d = data or {}
    r = _row(m, rid)
    _check_owner(ctx, server, side, r)
    reply = (d.get("reply") or "").strip()
    st, direc = r["status"], r["direction"]
    if side == "company":
        if action in ("approve", "reject") and direc == "out" and st == "pending":
            _set(m, rid, ctx, "approved" if action == "approve" else "rejected", reply)
            _notify(server, _row(m, rid), "✅ الشركة وافقت على الطلب" if action == "approve" else "⛔ الشركة رفضت الطلب", "office")
            return True
        if action == "cancel" and direc == "in" and st == "pending":
            _set(m, rid, ctx, "cancelled", reply)
            _notify(server, _row(m, rid), "↩ الشركة ألغت طلبها", "office")
            return True
        raise ValueError("req_state")
    # office
    if action == "accept" and ((direc == "in" and st == "pending") or (direc == "out" and st == "approved")):
        fee = d.get("fee", r["fee"])
        gov = d.get("gov_fees", r["gov_fees"])
        tid = _open_transaction(ctx, server, m, r, d.get("client_id") or r["client_id"], fee, gov)
        _set(m, rid, ctx, "accepted", reply if direc == "in" else None, tx_id=tid,
             client_id=int(d.get("client_id") or r["client_id"]), fee=float(fee or 0), gov_fees=float(gov or 0))
        _notify(server, _row(m, rid), "🚀 المكتب بدأ تنفيذ طلبك", "company")
        return tid
    if action == "reject" and direc == "in" and st == "pending":
        _set(m, rid, ctx, "rejected", reply)
        _notify(server, _row(m, rid), "⛔ المكتب رد على طلبك", "company")
        return True
    if action == "done" and st == "accepted":
        _set(m, rid, ctx, "done", reply if reply else None, done=_now())
        _notify(server, _row(m, rid), "🎉 تم تنفيذ طلبك", "company")
        return True
    if action == "cancel" and direc == "out" and st in ("pending", "approved"):
        _set(m, rid, ctx, "cancelled", reply)
        _notify(server, _row(m, rid), "↩ المكتب ألغى الطلب", "company")
        return True
    raise ValueError("req_state")


def op_req_link_company(ctx, server, company_id, client_id):
    """Office: which of my clients this company is."""
    if _side(ctx, server) != "office":
        raise users_mod.NotAllowed(PERM)
    m = _m(server)
    m.x("UPDATE companies SET client_id=? WHERE id=? AND id<>1", (int(client_id) if client_id else None, int(company_id)))
    return True


REQ_OPS = {k: v for k, v in globals().items() if k.startswith("op_")}
