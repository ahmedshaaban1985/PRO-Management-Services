# -*- coding: utf-8 -*-
"""PRO Management Services - web server.

The proven data layer of the desktop program (core.DB and its mixins: users &
permissions, companies, clients/employees/transactions, renewals, CRM, WhatsApp
inbox, accounting, client companies) is reused as-is.  This file adds:
  * the first-run setup (company + administrator), login sessions (cookie)
  * a whitelisted RPC endpoint: the browser calls data-layer methods by name
    (read permissions are checked here, write permissions inside the data layer)
  * file upload / download, printable documents, Excel export / import, backups
  * background jobs that used to need the desktop program open (see jobs.py)

Every request runs on the event-loop thread (all endpoints are async and never
await while a database call is in progress), so one SQLite connection per company
is shared safely and the logged-in user is bound per request.
"""
import asyncio
import datetime as dt
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import core
import printing
import users as users_mod
from rpc_spec import RPC, MONEY_KEYS, SALARY_KEYS, MONEY_METHODS, PRESERVE

printing.BROWSER = True

APP_TITLE = "PRO Management Services"
MIN_PW = 6
HERE = Path(__file__).resolve().parent
WEB = Path(getattr(sys, "_MEIPASS", HERE)) / "web"
COOKIE = "proms_session"
SESSION_IDLE_HOURS = 12
SECURE_COOKIE = os.environ.get("PROMS_SECURE_COOKIE", "1") == "1"

app = FastAPI(title=APP_TITLE, docs_url=None, redoc_url=None, openapi_url=None)


# ======================================================================
# databases
# ======================================================================
class State:
    main = None
    dbs = {}                 # company id -> DB (company 1 = main)
    login_fail = {}          # ip -> [timestamps]


S = State()


def main_db():
    if S.main is None:
        S.main = core.DB()
        S.dbs = {1: S.main}
        S.main.x("""CREATE TABLE IF NOT EXISTS web_sessions (
                       token TEXT PRIMARY KEY, user_id INTEGER, company_id INTEGER, lang TEXT,
                       created REAL, last_seen REAL, ip TEXT, agent TEXT)""")
        S.main.x("""CREATE TABLE IF NOT EXISTS web_notes (
                       id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, company_id INTEGER,
                       kind TEXT, title TEXT, body TEXT, link TEXT, perm TEXT)""")
    return S.main


def company_db(cid):
    m = main_db()
    cid = int(cid or 1)
    db = S.dbs.get(cid)
    if db is None or db.con is None:
        db = m.open_company(cid)
        S.dbs[cid] = db
    return db


def sync_all_users():
    for cid, db in list(S.dbs.items()):
        if db is not S.main and db.con is not None:
            db.sync_users()


def note(title, body="", kind="info", link="", company_id=None, perm=""):
    """A notification for the bell icon (and the browser notification)."""
    m = main_db()
    m.x("INSERT INTO web_notes(ts, company_id, kind, title, body, link, perm) VALUES(?,?,?,?,?,?,?)",
        (time.time(), company_id, kind, title, body, link, perm))
    m.x("DELETE FROM web_notes WHERE ts < ?", (time.time() - 30 * 86400,))


# ======================================================================
# json
# ======================================================================
def to_json(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, sqlite3.Row):
        return {k: to_json(v[k]) for k in v.keys()}
    if isinstance(v, dict):
        return {str(k): to_json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [to_json(x) for x in v]
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, bytes):
        return None
    try:
        return {k: to_json(v[k]) for k in v.keys()}
    except Exception:       # noqa: BLE001
        return str(v)


def scrub(value, drop):
    """Remove keys the user may not see (salaries, money) from every dict in the result."""
    if not drop:
        return value
    if isinstance(value, dict):
        return {k: scrub(v, drop) for k, v in value.items() if k not in drop}
    if isinstance(value, list):
        return [scrub(x, drop) for x in value]
    return value


def err(msg, code=400, **extra):
    return JSONResponse(dict({"ok": False, "error": msg}, **extra), status_code=code)


# ======================================================================
# sessions
# ======================================================================
class Ctx:
    def __init__(self, token, row, user, db, lang):
        self.token, self.row, self.user, self.db, self.lang = token, row, user, db, lang

    def can(self, perm):
        if perm is None:
            return True
        if isinstance(perm, (tuple, list)):
            return any(self.db.can(p) for p in perm)
        return self.db.can(perm)


# a "view only" user (e.g. a client who should only look at his company) changes nothing
VIEW_ONLY_WRITE_PREFIXES = ("save_", "delete_", "set_", "update_", "add_", "create_", "rebuild", "restore",
                            "receive_", "convert_", "log_reminder", "inbox_mark", "inbox_save", "inbox_set",
                            "inbox_relink", "inbox_delete")
VIEW_ONLY_WRITE_OPS = {"op_settings_set", "op_profile_save", "op_create_company", "op_save_account",
                       "op_set_user_companies", "op_new_recovery_code", "op_end_session", "op_clear_branding",
                       "op_sheet_script"}


def view_only(ctx):
    return ctx is not None and ctx.user["role"] != "admin" and users_mod.VIEW_ONLY in ctx.db._perms


def view_only_blocks(ctx, name, args, kwargs):
    if not view_only(ctx):
        return False
    if name == "set_password":           # his own password stays his
        return False
    if name.startswith("op_"):
        if name == "op_invoice_for_tx":
            return bool(kwargs.get("create") or (len(args) > 1 and args[1]))
        return name in VIEW_ONLY_WRITE_OPS
    return name.startswith(VIEW_ONLY_WRITE_PREFIXES)


def bind(request):
    """The logged-in user of this request (or None) - sets user + permissions on the databases."""
    m = main_db()
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    s = m.one("SELECT * FROM web_sessions WHERE token=?", (token,))
    if s is None or time.time() - (s["last_seen"] or 0) > SESSION_IDLE_HOURS * 3600:
        if s is not None:
            m.x("DELETE FROM web_sessions WHERE token=?", (token,))
        return None
    user = m.user_row(s["user_id"])
    if user is None or not user["active"]:
        m.x("DELETE FROM web_sessions WHERE token=?", (token,))
        return None
    m.user, m._perms = user, frozenset(m.perms_of(user))
    allowed = [c["id"] for c in m.companies_for(user)]
    cid = s["company_id"] if s["company_id"] in allowed else (allowed[0] if allowed else 1)
    try:
        db = company_db(cid)
    except Exception:       # noqa: BLE001
        cid, db = 1, m
    if db is not m:
        db.user, db._perms = (db.user_row(user["id"]) or user), m._perms
    if cid != s["company_id"] or time.time() - (s["last_seen"] or 0) > 60:
        m.x("UPDATE web_sessions SET last_seen=?, company_id=? WHERE token=?", (time.time(), cid, token))
    return Ctx(token, s, user, db, s["lang"] or m.get("language") or "ar")


def unbind():
    for db in S.dbs.values():
        if db is not None and db.con is not None:
            db.user, db._perms = None, frozenset()


def new_session(response, user_id, company_id, request, lang="ar"):
    m = main_db()
    token = secrets.token_urlsafe(32)
    m.x("INSERT INTO web_sessions(token, user_id, company_id, lang, created, last_seen, ip, agent) "
        "VALUES(?,?,?,?,?,?,?,?)", (token, user_id, company_id, lang, time.time(), time.time(),
                                    client_ip(request), (request.headers.get("user-agent") or "")[:200]))
    response.set_cookie(COOKIE, token, max_age=30 * 86400, httponly=True, samesite="lax",
                        secure=SECURE_COOKIE and request.url.scheme == "https")
    return token


def client_ip(request):
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")) or "?"


def too_many_failures(ip):
    now = time.time()
    lst = [t for t in S.login_fail.get(ip, []) if now - t < 15 * 60]
    S.login_fail[ip] = lst
    return len(lst) >= 8


@app.middleware("http")
async def guard(request: Request, call_next):
    # simple CSRF protection: state-changing API calls must come from our own scripts
    if request.method == "POST" and request.url.path.startswith("/api/") \
            and request.headers.get("x-proms") != "1":
        return err("bad request", 403)
    try:
        response = await call_next(request)
    finally:
        unbind()
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/static/"):
        # always revalidate (cheap with ETag) so an update reaches every browser right away
        response.headers["Cache-Control"] = "no-cache"
    return response


# ======================================================================
# state, setup, login
# ======================================================================
def state_of(ctx):
    m = main_db()
    out = {"ok": True, "app": APP_TITLE, "setup": not m.has_users(), "logged_in": ctx is not None,
           "lang": (ctx.lang if ctx else (m.get("language") or "ar"))}
    if ctx is None:
        out["company_name"] = m.company_name("ar") or ""
        out["company_name_en"] = m.company_name("en") or ""
        out["has_logo"] = bool(m.get("logo_path") and Path(m.get("logo_path")).exists())
        return out
    db, u = ctx.db, ctx.user
    perms = sorted(m.perms_of(u))
    comps = [{"id": c["id"], "name_ar": c["name_ar"], "name_en": c["name_en"], "kind": c["kind"],
              "currency": c["currency"]} for c in m.companies_for(u)]
    out.update({
        "user": {"id": u["id"], "username": u["username"], "full_name": u["full_name"], "role": u["role"]},
        "perms": perms, "own_only": db.own_only, "view_only": view_only(ctx),
        "company": {"id": db.company_id, "name_ar": db.company_name("ar"), "name_en": db.company_name("en"),
                    "currency": db.currency(), "cur_ar": db.cur_sym("ar"), "cur_en": db.cur_sym("en"),
                    "kind": db.company_kind, "is_business": db.is_business,
                    "has_logo": bool(db.get("logo_path") and Path(db.get("logo_path")).exists()),
                    "vat_rate": db.vat_rate() if hasattr(db, "vat_rate") else 0},
        "companies": comps, "is_main": db is m,
        "lists": {k: db.get_list(k) for k in ("tx_types", "stages", "doc_types", "file_cats", "entities",
                                              "pay_methods", "crm_stages", "crm_sources", "crm_lost_reasons")},
        "settings": {k: db.get(k) for k in ("alert_days", "due_alert_days", "idle_minutes")},
        "inbox_ready": bool(m.inbox_ready()) if db is m else False,
        "today": core.iso(core.today()),
    })
    return out


@app.get("/api/state")
async def api_state(request: Request):
    ctx = bind(request)
    return to_json(state_of(ctx))


@app.post("/api/setup")
async def api_setup(request: Request):
    """First visit: the company details + the administrator account."""
    m = main_db()
    if m.has_users():
        return err("already set up", 409)
    body = await request.json()
    comp = body.get("company") or {}
    adm = body.get("admin") or {}
    name_ar = (comp.get("company_name_ar") or "").strip()
    username = (adm.get("username") or "").strip()
    pw = adm.get("password") or ""
    if not name_ar:
        return err("company_name")
    if not username:
        return err("username")
    if len(pw) < MIN_PW:
        return err("password_short")
    profile = {k: (comp.get(k) or "").strip() for k in ("company_name_ar", "company_name_en", "company_cr",
                                                         "company_license", "company_phone", "company_email",
                                                         "company_address", "company_country", "currency",
                                                         "company_bank", "company_iban")}
    profile["currency"] = (profile.get("currency") or "QAR").upper()
    if comp.get("company_kind") in ("clearing", "business"):
        profile["company_kind"] = comp["company_kind"]
    m.save_profile(profile)
    if comp.get("language") in ("ar", "en"):
        m.set("language", comp["language"])
    uid = m.create_user(username, pw, (adm.get("full_name") or "").strip(), "admin")
    m.grant_company(uid, 1)
    code = m.make_recovery_code()
    resp = JSONResponse({"ok": True, "recovery_code": code})
    new_session(resp, uid, 1, request, m.get("language") or "ar")
    m.x("UPDATE users SET last_login=? WHERE id=?", (core.now_str(), uid))
    return resp


@app.post("/api/login")
async def api_login(request: Request):
    m = main_db()
    ip = client_ip(request)
    if too_many_failures(ip):
        return err("too_many", 429)
    body = await request.json()
    row = m.check_login(body.get("username") or "", body.get("password") or "")
    if row is None:
        S.login_fail.setdefault(ip, []).append(time.time())
        await asyncio.sleep(0.6)
        return err("bad_login", 401)
    S.login_fail.pop(ip, None)
    allowed = [c["id"] for c in m.companies_for(row)]
    if not allowed:
        return err("no_company", 403)
    last = m.get_int(f"last_company_{row['id']}", 0)
    cid = last if last in allowed else allowed[0]
    resp = JSONResponse({"ok": True})
    new_session(resp, row["id"], cid, request, body.get("lang") or m.get("language") or "ar")
    m.user = row
    m._perms = frozenset(m.perms_of(row))
    m.x("UPDATE users SET last_login=? WHERE id=?", (core.now_str(), row["id"]))
    m.audit("login", "مستخدم|User", row["id"], row["username"], "web " + ip)
    return resp


@app.post("/api/logout")
async def api_logout(request: Request):
    ctx = bind(request)
    m = main_db()
    if ctx:
        m.audit("logout", "مستخدم|User", ctx.user["id"], ctx.user["username"])
        m.x("DELETE FROM web_sessions WHERE token=?", (ctx.token,))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.post("/api/recover")
async def api_recover(request: Request):
    """Forgot the admin password: the recovery code shown at setup resets it."""
    m = main_db()
    ip = client_ip(request)
    if too_many_failures(ip):
        return err("too_many", 429)
    body = await request.json()
    if not m.check_recovery_code(body.get("code") or ""):
        S.login_fail.setdefault(ip, []).append(time.time())
        return err("bad_code", 401)
    username = (body.get("username") or "").strip()
    row = m.one("SELECT * FROM users WHERE username=? AND role='admin'", (username,))
    pw = body.get("password") or ""
    if row is None:
        return err("no_admin", 404)
    if len(pw) < MIN_PW:
        return err("password_short")
    m.set_password(row["id"], pw)
    code = m.make_recovery_code()
    sync_all_users()
    return {"ok": True, "recovery_code": code}


@app.post("/api/session")
async def api_session(request: Request):
    """Switch company / language for this browser."""
    body = await request.json()
    ctx = bind(request)
    if ctx is None:
        return err("login", 401)
    m = main_db()
    if "company_id" in body:
        cid = int(body["company_id"])
        if cid not in [c["id"] for c in m.companies_for(ctx.user)]:
            return err("not allowed", 403)
        company_db(cid)
        m.x("UPDATE web_sessions SET company_id=? WHERE token=?", (cid, ctx.token))
        m.set(f"last_company_{ctx.user['id']}", cid)
    if body.get("lang") in ("ar", "en"):
        m.x("UPDATE web_sessions SET lang=? WHERE token=?", (body["lang"], ctx.token))
    return {"ok": True}


# ======================================================================
# RPC: call a data-layer method by name
# ======================================================================
@app.post("/api/call")
async def api_call(request: Request):
    body = await request.json()
    ctx = bind(request)          # after the last await: nothing else runs until we return
    if ctx is None:
        return err("login", 401)
    name = body.get("m") or ""
    if view_only_blocks(ctx, name, body.get("a") or [], body.get("k") or {}):
        return err("not_allowed", 403, perm="view_only")
    if name.startswith("op_"):
        import ops
        fn = ops.OPS.get(name)
        if fn is None:
            return err(f"unknown op {name}", 404)
        try:
            r = fn(ctx, sys.modules[__name__], *(body.get("a") or []), **(body.get("k") or {}))
        except users_mod.NotAllowed as e:
            return err("not_allowed", 403, perm=str(e))
        except (ValueError, KeyError, TypeError) as e:
            return err(str(e) or e.__class__.__name__, 400)
        drop = set()
        if not ctx.db.can("salaries"):
            drop |= SALARY_KEYS
        if not ctx.db.can("money"):
            drop |= MONEY_KEYS
        return {"ok": True, "r": scrub(to_json(r), drop)}
    spec = RPC.get(name)
    if spec is None:
        return err(f"unknown method {name}", 404)
    perm, target = spec[0], spec[1]
    if not ctx.can(perm):
        return err("not_allowed", 403, perm=perm)
    obj = main_db() if target == "main" else ctx.db
    # a user limited to some companies (e.g. a client's own login) must not see the others' names
    if name in ("companies", "company_row") and ctx.user["role"] != "admin" and not ctx.can("companies"):
        allowed = {c["id"] for c in main_db().companies_for(ctx.user)}
        if name == "companies":
            rows = main_db().companies(*(body.get("a") or []), **(body.get("k") or {}))
            return {"ok": True, "r": to_json([r for r in rows if r["id"] in allowed])}
        a0 = (body.get("a") or [None])[0]
        if a0 is None or int(a0) not in allowed:
            return err("not_allowed", 403, perm="companies")
    if target == "main" and not (ctx.db is main_db()) and spec[2:] and spec[2] == "main_only":
        return err("main company only", 403)
    fn = getattr(obj, name, None)
    if fn is None:
        return err(f"not available {name}", 404)
    args = body.get("a") or []
    kwargs = body.get("k") or {}
    # hidden fields (money / salaries) are not sent back by the browser: keep the stored values
    keep = PRESERVE.get(name)
    if keep and len(args) >= 2 and args[1] and isinstance(args[0], dict):
        getter, keys = keep
        old = getattr(obj, getter)(args[1])
        if old is not None:
            for k in keys:
                if k not in args[0] and k in old.keys():
                    args[0][k] = old[k]
    try:
        result = fn(*args, **kwargs)
    except users_mod.NotAllowed as e:
        return err("not_allowed", 403, perm=str(e))
    except (ValueError, KeyError, TypeError, sqlite3.IntegrityError) as e:
        return err(str(e) or e.__class__.__name__, 400, kind=e.__class__.__name__)
    except Exception as e:      # noqa: BLE001
        return err(str(e) or e.__class__.__name__, 500, kind=e.__class__.__name__)
    if name in ("create_user", "update_user", "set_password", "delete_user"):
        sync_all_users()
    drop = set()
    if not ctx.db.can("salaries"):
        drop |= SALARY_KEYS
    if name in MONEY_METHODS and not ctx.db.can("money"):
        drop |= MONEY_KEYS
    return {"ok": True, "r": scrub(to_json(result), drop)}


# ======================================================================
# files
# ======================================================================
OWNER_PERMS = {"client": "clients_view", "employee": "employees_view", "owner": "clients_view",
               "transaction": "tx_view", "document": "clients_view", "lead": "crm",
               "company": "company_files"}


async def _save_upload(up: UploadFile) -> Path:
    folder = Path(tempfile.mkdtemp(prefix="up_", dir=str(main_db().path.parent)))
    name = re.sub(r'[\\/:*?"<>|]+', "_", Path(up.filename or "file").name)[:120] or "file"
    path = folder / name
    with open(path, "wb") as f:
        while True:
            chunk = await up.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    return path


@app.post("/api/upload")
async def api_upload(request: Request, file: UploadFile = File(...), owner_type: str = Form(""),
                     owner_id: int = Form(0), category: str = Form(""), note: str = Form("")):
    path = await _save_upload(file)
    ctx = bind(request)
    if view_only(ctx):
        if path:
            shutil.rmtree(path.parent, ignore_errors=True)
        return err("not_allowed", 403, perm="view_only")
    if ctx is None:
        shutil.rmtree(path.parent, ignore_errors=True)
        return err("login", 401)
    try:
        fid = ctx.db.add_file(owner_type, owner_id, str(path), category, note)
    except users_mod.NotAllowed:
        return err("not_allowed", 403)
    except Exception as e:      # noqa: BLE001
        return err(str(e))
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
    return {"ok": True, "r": fid}


@app.get("/api/file/{fid}")
async def api_file(fid: int, request: Request, download: int = 0):
    ctx = bind(request)
    if ctx is None:
        return err("login", 401)
    row = ctx.db.file(fid)
    if row is None:
        return err("not found", 404)
    if not ctx.can(OWNER_PERMS.get(row["owner_type"], "clients_view")):
        return err("not_allowed", 403)
    if ctx.db.own_only:
        cid = ctx.db.owner_client_id(row["owner_type"], row["owner_id"])
        vis = ctx.db.visible_client_ids()
        if cid and vis is not None and cid not in vis:
            return err("not_allowed", 403)
    p = ctx.db.file_path(row)
    if not p.exists():
        return err("missing", 404)
    return FileResponse(str(p), filename=p.name,
                        content_disposition_type="attachment" if download else "inline")


@app.get("/api/photo/{owner_type}/{owner_id}")
async def api_photo(owner_type: str, owner_id: int, request: Request):
    ctx = bind(request)
    if ctx is None:
        return err("login", 401)
    if not ctx.can(OWNER_PERMS.get(owner_type, "clients_view")):
        return err("not_allowed", 403)
    p = ctx.db.photo_of(owner_type, owner_id)
    if not p or not Path(p).exists():
        return Response(status_code=204)
    return FileResponse(str(p))


@app.get("/api/logo")
async def api_logo(request: Request, cid: int = 0):
    ctx = bind(request)
    db = ctx.db if ctx else main_db()
    p = db.get("logo_path")
    if not p or not Path(p).exists():
        return Response(status_code=204)
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


@app.post("/api/branding")
async def api_branding(request: Request, file: UploadFile = File(...), kind: str = Form("logo")):
    """Logo (png/jpg) or letterhead (pdf) of the open company."""
    path = await _save_upload(file)
    ctx = bind(request)
    if view_only(ctx):
        if path:
            shutil.rmtree(path.parent, ignore_errors=True)
        return err("not_allowed", 403, perm="view_only")
    if ctx is None or not ctx.can("settings"):
        shutil.rmtree(path.parent, ignore_errors=True)
        return err("not_allowed", 403)
    db = ctx.db
    try:
        ext = path.suffix.lower()
        if kind == "logo":
            if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                return err("image only")
            target = db.path.parent / ("logo" + ext)
            shutil.copyfile(path, target)
            db.set("logo_path", str(target))
        else:
            if ext != ".pdf":
                return err("pdf only")
            target = db.path.parent / "letterhead.pdf"
            shutil.copyfile(path, target)
            db.set("letterhead_pdf", str(target))
            db.set("use_letterhead", "1")
            _letterhead_png(db, force=True)
        db.audit("settings", "الهوية|Branding", None, kind)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
    return {"ok": True}


@app.post("/api/company_doc")
async def api_company_doc(request: Request, file: UploadFile = File(None), did: int = Form(0),
                          title: str = Form(""), doc_number: str = Form(""), issue_date: str = Form(""),
                          expiry: str = Form(""), notes: str = Form("")):
    """Company document of a client company (C.R., license, contracts...) with an optional file."""
    path = await _save_upload(file) if file is not None and file.filename else None
    ctx = bind(request)
    if view_only(ctx):
        if path:
            shutil.rmtree(path.parent, ignore_errors=True)
        return err("not_allowed", 403, perm="view_only")
    try:
        if ctx is None:
            return err("login", 401)
        if not ctx.can("company_files"):
            return err("not_allowed", 403)
        data = {"title": title, "doc_number": doc_number, "issue_date": issue_date, "expiry": expiry,
                "notes": notes}
        try:
            rid = ctx.db.save_company_doc(data, did or None, src_file=str(path) if path else None)
        except ValueError as e:
            return err(str(e))
        ctx.db.audit("edit" if did else "create", "ملفات الشركة|Company documents", rid, title)
        return {"ok": True, "r": rid}
    finally:
        if path:
            shutil.rmtree(path.parent, ignore_errors=True)


@app.get("/api/company_doc_file/{did}")
async def api_company_doc_file(did: int, request: Request, download: int = 0):
    ctx = bind(request)
    if ctx is None:
        return err("login", 401)
    if not ctx.can("company_files"):
        return err("not_allowed", 403)
    row = ctx.db.company_doc(did)
    if not row or not row["path"] or not Path(row["path"]).exists():
        return err("missing", 404)
    p = Path(row["path"])
    return FileResponse(str(p), filename=row["original_name"] or p.name,
                        content_disposition_type="attachment" if download else "inline")


# ======================================================================
# printable documents (open in the browser -> print / save as PDF)
# ======================================================================
PRINT_KINDS = {
    "quotation": ("sales_view", printing.quotation_html),
    "invoice": ("sales_view", printing.invoice_html),
    "receipt": (("sales_view", "receipts"), printing.receipt_html),
    "voucher": (("accounting", "expenses"), printing.voucher_html),
    "statement": (("sales_view", "finance"), printing.statement_html),
    "sale_invoice": ("sales_view", printing.sales_invoice_html),
    "sale_receipt": (("sales_view", "receipts"), printing.sales_receipt_html),
    "supplier_payment": ("purchases", printing.supplier_payment_html),
}


def _letterhead_png(db, force=False):
    """First page of the letterhead PDF as an image (cached) for the browser."""
    pdf = printing.letterhead_file(db)
    if not pdf:
        return None
    png = Path(db.path.parent) / "letterhead_web.png"
    if force or not png.exists() or png.stat().st_mtime < Path(pdf).stat().st_mtime:
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf
        doc = pymupdf.open(str(pdf))
        pix = doc[0].get_pixmap(dpi=150)
        pix.save(str(png))
        doc.close()
    return png


@app.get("/api/letterhead.png")
async def api_letterhead(request: Request):
    ctx = bind(request)
    if ctx is None:
        return err("login", 401)
    p = _letterhead_png(ctx.db)
    if not p:
        return Response(status_code=204)
    return FileResponse(str(p), headers={"Cache-Control": "no-cache"})


def _web_html(db, inner, lang, title):
    """Wrap printing.py html in an A4 print page (letterhead as background when set)."""
    inner = re.sub(r'src="file:[^"]*"', 'src="/api/logo"', inner)
    inner = re.sub(r"(?is)^.*?<body[^>]*>", "", inner)
    inner = re.sub(r"(?is)</body>.*$", "", inner)
    lh = printing.letterhead_file(db)
    d = "ltr" if lang == "en" else "rtl"
    if lh:
        top, side, bottom = (printing._mm(db, "lh_top_mm", 46), printing._mm(db, "lh_side_mm", 18),
                             printing._mm(db, "lh_bottom_mm", 30))
        bg = "background:url('/api/letterhead.png') top left / 210mm 297mm no-repeat;"
    else:
        top, side, bottom, bg = 12, 14, 14, ""
    return f"""<!doctype html><html lang="{lang}" dir="{d}"><head><meta charset="utf-8">
<title>{printing._e(title)}</title>
<style>
@page {{ size: A4; margin: 0; }}
html,body {{ margin:0; background:#e9e9ec; }}
.sheet {{ width:210mm; min-height:297mm; margin:12px auto; background:#fff; box-shadow:0 2px 12px rgba(0,0,0,.15);
  {bg} box-sizing:border-box; padding:{top}mm {side}mm {bottom}mm {side}mm; font-family:'Segoe UI',Tahoma,Arial,sans-serif;
  font-size:10.5pt; color:#111; -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
table {{ border-collapse:collapse; }}
.bar {{ position:sticky; top:0; background:#141414; padding:10px; text-align:center; z-index:5; }}
.bar button {{ background:linear-gradient(90deg,#C29B25,#DCBB5A); border:0; padding:9px 26px; border-radius:8px;
  font-weight:bold; font-size:14px; cursor:pointer; margin:0 4px; }}
.bar .sec {{ background:#333; color:#eee; }}
@media print {{ html,body {{ background:#fff; }} .bar {{ display:none; }}
  .sheet {{ margin:0; box-shadow:none; min-height:auto; }} }}
</style></head><body>
<div class="bar"><button onclick="window.print()">{'Print / Save PDF' if lang == 'en' else 'طباعة / حفظ PDF'}</button>
<button class="sec" onclick="window.close()">{'Close' if lang == 'en' else 'إغلاق'}</button></div>
<div class="sheet" dir="{d}">{inner}</div></body></html>"""


@app.get("/print/{kind}/{ref}")
async def print_doc(kind: str, ref: int, request: Request, lang: str = "ar"):
    ctx = bind(request)
    if ctx is None:
        return HTMLResponse("<script>location='/'</script>", status_code=401)
    spec = PRINT_KINDS.get(kind)
    if spec is None:
        return HTMLResponse("not found", status_code=404)
    if not ctx.can(spec[0]):
        return HTMLResponse("not allowed", status_code=403)
    try:
        h, _c, name = spec[1](ctx.db, ref, lang)
    except Exception as e:      # noqa: BLE001
        return HTMLResponse(f"error: {e}", status_code=400)
    return HTMLResponse(_web_html(ctx.db, h, lang, name))


@app.post("/print/letter")
async def print_letter(request: Request):
    """A letter from a template (text already filled in by the browser)."""
    form = await request.form()
    ctx = bind(request)
    if ctx is None:
        return HTMLResponse("login", status_code=401)
    lang = form.get("lang") or "ar"
    cid = int(form.get("client_id") or 0) or None
    c = ctx.db.client(cid) if cid else None
    h, _c, name = printing.letter_html(ctx.db, form.get("text") or "", lang, form.get("title") or "", c)
    return HTMLResponse(_web_html(ctx.db, h, lang, name))


@app.post("/print/report")
async def print_report(request: Request):
    """Any table on screen -> printable report (title, headers, rows sent by the browser)."""
    form = await request.form()
    ctx = bind(request)
    if ctx is None:
        return HTMLResponse("login", status_code=401)
    lang = form.get("lang") or "ar"
    data = json.loads(form.get("data") or "{}")
    title, sub = data.get("title", ""), data.get("subtitle", "")
    headers, rows = data.get("headers", []), data.get("rows", [])
    bold = set(data.get("bold", []))
    e = printing._e
    th = "".join(f'<th style="background:#141414;color:#E6C65C;padding:6px;border:1px solid #333">{e(h)}</th>'
                 for h in headers)
    trs = "".join("<tr>" + "".join(
        f'<td style="padding:5px;border:1px solid #ddd;{"font-weight:bold;background:#f6f1e0" if i in bold else ""}">'
        f'{e(c)}</td>' for c in r) + "</tr>" for i, r in enumerate(rows))
    co = ctx.db.company_name(lang)
    inner = (("" if printing.letterhead_file(ctx.db) else printing.letterhead(ctx.db, lang)) +
             f'<h2 style="text-align:center;margin:6px 0">{e(title)}</h2>'
             f'<p style="text-align:center;color:#555;margin:0 0 10px">{e(sub)}</p>'
             f'<table style="width:100%;font-size:9pt"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'
             f'<p style="color:#888;font-size:8pt;margin-top:12px">{e(co)} — {core.now_str()}</p>')
    return HTMLResponse(_web_html(ctx.db, inner, lang, title))


# ======================================================================
# Excel
# ======================================================================
@app.post("/api/export")
async def api_export(request: Request):
    """Any table on screen -> .xlsx (headers + rows sent by the browser)."""
    body = await request.json()
    ctx = bind(request)
    if ctx is None:
        return err("login", 401)
    if not ctx.can("export"):
        return err("not_allowed", 403)
    import excel_io
    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    excel_io.export_rows(tmp, ctx.db, ctx.lang, body.get("title", ""), body.get("headers", []),
                         body.get("rows", []), money_cols=tuple(body.get("money_cols", [])))
    name = re.sub(r'[\\/:*?"<>|]+', "_", body.get("title") or "export") + f" {core.iso(core.today())}.xlsx"
    return FileResponse(tmp, filename=name, background=_cleanup(tmp))


def _cleanup(path):
    from starlette.background import BackgroundTask
    return BackgroundTask(lambda: os.path.exists(path) and os.unlink(path))


@app.get("/api/export/employees")
async def api_export_employees(request: Request, client_id: int = 0, status: str = "active"):
    ctx = bind(request)
    if ctx is None:
        return err("login", 401)
    if not (ctx.can("export") and ctx.can("employees_view")):
        return err("not_allowed", 403)
    import excel_io
    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    excel_io.export_employees(ctx.db, tmp, client_id or None, ctx.lang, status, ctx.db.can("salaries"))
    nm = "كشف الموظفين" if ctx.lang == "ar" else "Employees"
    if client_id:
        c = ctx.db.client(client_id)
        nm += " - " + core.safe_name(c["name"]) if c else ""
    return FileResponse(tmp, filename=f"{nm} {core.iso(core.today())}.xlsx", background=_cleanup(tmp))


@app.get("/api/export/employees_template")
async def api_emp_template(request: Request):
    ctx = bind(request)
    if ctx is None:
        return err("login", 401)
    import excel_io
    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    excel_io.employees_template(tmp, ctx.lang)
    return FileResponse(tmp, filename="employees_template.xlsx", background=_cleanup(tmp))


@app.post("/api/import/employees")
async def api_import_employees(request: Request, file: UploadFile = File(...), client_id: int = Form(...),
                               commit: int = Form(0)):
    """Excel sheet of employees -> preview (commit=0) or save (commit=1)."""
    import excel_io
    path = await _save_upload(file)
    ctx = bind(request)
    if view_only(ctx):
        if path:
            shutil.rmtree(path.parent, ignore_errors=True)
        return err("not_allowed", 403, perm="view_only")
    if ctx is None or not ctx.can("employees_edit"):
        shutil.rmtree(path.parent, ignore_errors=True)
        return err("not_allowed", 403)
    try:
        rows, problems = excel_io.read_employees(str(path))
    except Exception as e:      # noqa: BLE001
        return err(str(e))
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
    db = ctx.db
    out = []
    for r in rows:
        dup = db.find_duplicate_employee(r.get("qid_number", ""), r.get("passport_number", ""))
        out.append({"data": r, "duplicate": bool(dup), "dup_name": dup["name"] if dup else ""})
    saved = 0
    if commit:
        with db.atomic():
            for o in out:
                if o["duplicate"]:
                    continue
                d = dict(o["data"])
                d["client_id"] = client_id
                d.setdefault("status", "active")
                db.save_employee(d)
                saved += 1
    return to_json({"ok": True, "rows": out, "problems": problems, "saved": saved})


# ======================================================================
# backup (administrator)
# ======================================================================
@app.get("/api/backup")
async def api_backup(request: Request):
    ctx = bind(request)
    if ctx is None:
        return err("login", 401)
    if not ctx.can("settings") or ctx.db is not main_db():
        return err("not_allowed", 403)
    folder = Path(tempfile.mkdtemp())
    p = main_db().backup_to(folder, with_files=True)
    return FileResponse(str(p), filename=p.name,
                        background=_cleanup_dir(folder))


def _cleanup_dir(folder):
    from starlette.background import BackgroundTask
    return BackgroundTask(lambda: shutil.rmtree(folder, ignore_errors=True))


# ======================================================================
# notifications (bell)
# ======================================================================
@app.get("/api/notes")
async def api_notes(request: Request, after: int = 0):
    ctx = bind(request)
    if ctx is None:
        return err("login", 401)
    rows = main_db().q("SELECT * FROM web_notes WHERE id>? AND (company_id IS NULL OR company_id=?) "
                       "ORDER BY id DESC LIMIT 50", (after, ctx.db.company_id))
    rows = [r for r in rows if not r["perm"] or ctx.can(r["perm"])]
    unread = 0
    try:
        if ctx.db is main_db() and ctx.can("inbox"):
            unread = main_db().inbox_unread_total()
    except Exception:       # noqa: BLE001
        pass
    req_waiting = 0
    try:
        import ops_req
        req_waiting = ops_req.op_req_counts(ctx, sys.modules[__name__])["waiting"]
    except Exception:       # noqa: BLE001
        pass
    return to_json({"ok": True, "notes": rows, "inbox_unread": unread, "req_waiting": req_waiting})


# ======================================================================
# WhatsApp inbox: media + sending (network in a thread)
# ======================================================================
@app.get("/api/wa_media/{mid}")
async def api_wa_media(mid: int, request: Request):
    ctx = bind(request)
    if ctx is None or not ctx.can("inbox"):
        return err("not_allowed", 403)
    r = main_db().inbox_message(mid)
    if r is None or not r["local_path"] or not Path(r["local_path"]).exists():
        return Response(status_code=204)
    return FileResponse(r["local_path"])


@app.post("/api/wa_send")
async def api_wa_send(request: Request, phone: str = Form(...), text: str = Form(""),
                      file: UploadFile = File(None), caption: str = Form("")):
    import inbox
    tmp = await _save_upload(file) if (file is not None and file.filename) else None
    ctx = bind(request)
    if view_only(ctx):
        if tmp:
            shutil.rmtree(tmp.parent, ignore_errors=True)
        return err("not_allowed", 403, perm="view_only")
    if ctx is None or not ctx.can("inbox"):
        if tmp:
            shutil.rmtree(tmp.parent, ignore_errors=True)
        return err("not_allowed", 403)
    m = main_db()
    if tmp is not None:
        kind, why = inbox.send_kind(str(tmp))
        if not kind:
            shutil.rmtree(tmp.parent, ignore_errors=True)
            return err(why or "type")
        folder = m.inbox_media_dir() / inbox.digits(phone)
        folder.mkdir(parents=True, exist_ok=True)
        local = folder / f"{int(time.time())}_{tmp.name}"
        shutil.move(str(tmp), local)
        shutil.rmtree(tmp.parent, ignore_errors=True)
        mid = m.inbox_add_outgoing(phone, kind, caption=caption,
                                   local_path=str(local), filename=local.name)
    else:
        if not text.strip():
            return err("empty")
        mid = m.inbox_add_outgoing(phone, "text", body=text)
    import jobs
    asyncio.get_running_loop().create_task(jobs.send_wa(mid))
    return to_json({"ok": True, "r": m.inbox_message(mid)})


# ======================================================================
# Facebook / Instagram connect (Meta) - helper endpoints
# ======================================================================
@app.post("/api/meta/pages")
async def api_meta_pages(request: Request):
    body = await request.json()
    ctx = bind(request)
    if view_only(ctx):
        return err("not_allowed", 403, perm="view_only")
    if ctx is None or not (ctx.can("settings") and ctx.can("crm")):
        return err("not_allowed", 403)
    import metalead
    tok = (body.get("token") or "").strip()
    app_id, secret = (body.get("app_id") or "").strip(), (body.get("app_secret") or "").strip()
    try:
        missing = await asyncio.to_thread(metalead.missing_permissions, tok)
        if missing:
            return err(metalead.missing_message(missing), 400, missing=missing)
        long_lived = bool(app_id and secret)
        if long_lived:
            tok = await asyncio.to_thread(metalead.long_lived_user_token, app_id, secret, tok)
        pages = await asyncio.to_thread(metalead.list_pages, tok)
    except metalead.MetaError as e:
        return err(str(e))
    unbind()
    main_db().set("meta_app_id", app_id)
    S.meta_pages = {p["id"]: p for p in pages}
    return {"ok": True, "long_lived": long_lived,
            "pages": [{"id": p["id"], "name": p["name"], "tasks": p.get("tasks") or []} for p in pages]}


@app.post("/api/meta/connect")
async def api_meta_connect(request: Request):
    body = await request.json()
    ctx = bind(request)
    if view_only(ctx):
        return err("not_allowed", 403, perm="view_only")
    if ctx is None or not (ctx.can("settings") and ctx.can("crm")):
        return err("not_allowed", 403)
    import metalead
    p = getattr(S, "meta_pages", {}).get(str(body.get("page_id")))
    if not p:
        return err("page")
    try:
        forms = await asyncio.to_thread(metalead.list_forms, p["id"], p["access_token"])
    except metalead.MetaError as e:
        return err(str(e))
    ctx = bind(request)
    m = main_db()
    m.set("meta_page_id", p["id"])
    m.set("meta_page_name", p["name"])
    m.set("meta_page_token", p["access_token"])
    m.set("meta_since", int(time.time()) - int(body.get("days") or 7) * 86400)
    m.set("meta_on", "1")
    m.set("meta_status", core.now_str() + f"  ✓ اتربطت صفحة «{p['name']}» — فيها {len(forms)} فورم")
    m.audit("settings", "ربط فيسبوك|Facebook link", None, p["name"])
    import jobs
    asyncio.get_running_loop().create_task(jobs.meta_tick(force=True))
    return {"ok": True, "forms": [f.get("name") for f in forms]}


@app.post("/api/kapso/test")
async def api_kapso_test(request: Request):
    import inbox
    body = await request.json()
    ctx = bind(request)
    if view_only(ctx):
        return err("not_allowed", 403, perm="view_only")
    if ctx is None or not ctx.can("settings"):
        return err("not_allowed", 403)
    m = main_db()
    key = (body.get("key") or "").strip()
    if not key or key.startswith("••"):
        key = m.get("kapso_api_key")
    pid = (body.get("phone_id") or "").strip() or m.get("kapso_phone_id")
    unbind()
    try:
        await asyncio.to_thread(inbox.KapsoClient(key, pid).test)
    except Exception as e:      # noqa: BLE001
        return err(str(e))
    return {"ok": True}


@app.post("/api/jobs/run")
async def api_jobs_run(request: Request):
    """'Test and receive now' buttons."""
    body = await request.json()
    ctx = bind(request)
    if view_only(ctx):
        return err("not_allowed", 403, perm="view_only")
    if ctx is None or not ctx.can("settings"):
        return err("not_allowed", 403)
    unbind()
    import jobs
    what = body.get("job")
    if what == "meta":
        res = await jobs.meta_tick(force=True)
    elif what == "sheet":
        res = await jobs.sheet_tick(force=True)
    elif what == "inbox":
        res = await jobs.inbox_tick(force=True)
    elif what == "reminders":
        res = await jobs.reminders_tick(force=True)
    else:
        return err("job")
    return to_json({"ok": True, "r": res})


# ======================================================================
# renewal reminders through the WhatsApp Business API (open company)
# ======================================================================
def _wa_cfg():
    import whatsapp_api
    m = main_db()
    return {"token": m.get("wa_token"), "phone_id": m.get("wa_phone_id"), "template": m.get("wa_template"),
            "lang": m.get("wa_template_lang") or "ar", "version": m.get("wa_api_version") or whatsapp_api.DEFAULT_VERSION}


@app.post("/api/reminders/send")
async def api_reminders_send(request: Request):
    """'Send all automatically': every due group of the open company with a phone number."""
    import whatsapp_api
    ctx = bind(request)
    if view_only(ctx):
        return err("not_allowed", 403, perm="view_only")
    if ctx is None or not ctx.can("renewals"):
        return err("not_allowed", 403)
    if not main_db().wa_ready():
        return err("wa_not_ready")
    cid = ctx.db.company_id
    groups = [g for g in ctx.db.due_reminders() if core.phone_digits(g["phone"])]
    jobs = [(g, core.phone_digits(g["phone"]), ctx.db.api_params(g)) for g in groups]
    who = ctx.user
    cfg = _wa_cfg()
    ok = fail = 0
    for g, to, params in jobs:
        res = await asyncio.to_thread(whatsapp_api.send_template, cfg["token"], cfg["phone_id"], to,
                                      cfg["template"], cfg["lang"], params, cfg["version"])
        db = company_db(cid)
        db.user = who                       # logged under the person who pressed the button
        try:
            if res[0]:
                ok += 1
                db.log_reminder(g, "api", "sent", message=" | ".join(params), wa_id=res[1])
            else:
                fail += 1
                db.log_reminder(g, "api", "failed", message=" | ".join(params), error=res[1])
        finally:
            db.user = None
        await asyncio.sleep(0.3)
    return {"ok": True, "sent": ok, "failed": fail}


@app.post("/api/reminders/test")
async def api_reminders_test(request: Request):
    import whatsapp_api
    body = await request.json()
    ctx = bind(request)
    if view_only(ctx):
        return err("not_allowed", 403, perm="view_only")
    if ctx is None or not ctx.can("settings"):
        return err("not_allowed", 403)
    to = core.phone_digits(body.get("to") or "")
    cfg = _wa_cfg()
    name = ctx.db.company_name("ar")
    if not to:
        return err("to")
    if not (cfg["token"] and cfg["phone_id"] and cfg["template"]):
        return err("wa_not_ready")
    ok, inf = await asyncio.to_thread(whatsapp_api.send_template, cfg["token"], cfg["phone_id"], to, cfg["template"],
                                      cfg["lang"], ["عميل تجربة", "موظف تجربة - البطاقة الشخصية - تنتهي قريبًا", name],
                                      cfg["version"])
    return {"ok": bool(ok), "info": inf} if ok else err(inf)


# ======================================================================
# pages
# ======================================================================
@app.on_event("startup")
async def startup():
    main_db()
    import jobs
    jobs.start(asyncio.get_running_loop())


@app.get("/healthz")
async def healthz():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/{path:path}")
async def index(path: str):
    return FileResponse(str(WEB / "index.html"), headers={"Cache-Control": "no-cache"})
