# -*- coding: utf-8 -*-
"""Background work that the desktop program did only while it was open - now the
server does it around the clock:

  * WhatsApp inbox sync through Kapso (+ media download, sending)
  * Facebook / Instagram lead ads (Graph API) and the Google Sheet route
  * WhatsApp welcome to brand-new leads
  * automatic renewal reminders on WhatsApp (daily, at the configured time)
  * daily database copies + the periodic full backup

Network calls run in threads (asyncio.to_thread); every database change runs on the
event-loop thread after server.unbind(), so it is never attributed to a web user.
"""
import asyncio
import time
import traceback

import core
import server

_state = {"meta_last": 0.0, "sheet_last": 0.0, "inbox_last": 0.0, "inbox_first": True, "media_busy": False,
          "day": None, "running": set()}


def _db():
    server.unbind()
    return server.main_db()


def start(loop):
    loop.create_task(_loop())


async def _loop():
    await asyncio.sleep(5)
    n = 0
    while True:
        n += 1
        for fn in (inbox_tick, meta_tick, sheet_tick, reminders_tick, daily_tick):
            try:
                await fn()
            except Exception:       # noqa: BLE001
                traceback.print_exc()
        await asyncio.sleep(10)


async def _once(name, coro_fn):
    """Never run the same job twice at the same time."""
    if name in _state["running"]:
        return None
    _state["running"].add(name)
    try:
        return await coro_fn()
    finally:
        _state["running"].discard(name)


# ----------------------------------------------------------------------- WhatsApp inbox
async def inbox_tick(force=False):
    m = _db()
    if not m.inbox_ready():
        return None
    every = 15
    if not force and time.time() - _state["inbox_last"] < every:
        return None
    _state["inbox_last"] = time.time()

    async def run():
        import inbox
        key, pid = m.get("kapso_api_key"), m.get("kapso_phone_id")
        since = m.inbox_since()
        status_since = int(time.time()) - 3 * 86400
        try:
            res = await asyncio.to_thread(inbox.run_sync, inbox.KapsoClient(key, pid), since, status_since)
        except Exception as e:      # noqa: BLE001
            _db().set("inbox_status", core.now_str() + "  ⚠ " + str(e))
            return {"error": str(e)}
        db = _db()
        out = db.inbox_store(res["messages"], res.get("statuses") or ())
        db.set("inbox_status", core.now_str() + "  🟢")
        first, _state["inbox_first"] = _state["inbox_first"], False
        fresh = [x for x in out["new_inbound"] if x["ts"] > time.time() - 15 * 60]
        if fresh and not first:
            last = fresh[-1]
            ch = db.inbox_chat(last["phone"])
            name = (ch["contact_name"] if ch is not None and ch["contact_name"] else inbox.pretty_phone(last["phone"]))
            server.note("💬 " + name, inbox.preview_text(_R(last))[:200], "inbox", "/inbox/" + last["phone"],
                        1, "inbox")
        await media()
        return {"new": len(out["new_inbound"])}
    return await _once("inbox", run)


class _R(dict):
    def keys(self):
        return super().keys()


async def media():
    import inbox
    if _state["media_busy"]:
        return
    _state["media_busy"] = True
    try:
        for _ in range(10):
            db = _db()
            rows = db.inbox_pending_media(6)
            if not rows:
                return
            key, pid = db.get("kapso_api_key"), db.get("kapso_phone_id")
            folder = str(db.inbox_media_dir())
            jobs = [{k: r[k] for k in ("wamid", "phone", "type", "media_url", "media_id", "mime", "filename", "ts")}
                    for r in rows]
            client = inbox.KapsoClient(key, pid)
            for job in jobs:
                try:
                    path = await asyncio.to_thread(inbox.download_to, client, job, folder)
                    err = ""
                except Exception as e:  # noqa: BLE001
                    path, err = "", str(e)
                try:
                    _db().inbox_media_done(job["wamid"], path or None, err)
                except Exception:       # noqa: BLE001
                    pass
    finally:
        _state["media_busy"] = False


async def send_wa(mid):
    import inbox
    db = _db()
    r = db.inbox_message(mid)
    if r is None:
        return
    key, pid = db.get("kapso_api_key"), db.get("kapso_phone_id")
    job = {"to": r["phone"], "kind": "text" if r["type"] == "text" else r["type"], "body": r["body"],
           "path": r["local_path"], "caption": r["caption"], "filename": r["filename"]}

    def run():
        c = inbox.KapsoClient(key, pid)
        if job["kind"] == "text":
            return c.send_text(job["to"], job["body"])
        media_id = c.upload_media(job["path"])
        return c.send_media(job["to"], job["kind"], media_id, job.get("caption", ""), job.get("filename", ""))
    try:
        wamid, err = await asyncio.to_thread(run), ""
    except Exception as e:      # noqa: BLE001
        wamid, err = "", str(e)
    _db().inbox_sent(mid, wamid or None, err)


# ----------------------------------------------------------------------- internet leads
def _announce(db, res, title):
    new = res["new"] + res["client"]
    for i in new[:5]:
        ld = db.lead(i)
        if ld is None:
            continue
        svc = (ld["service"] or "").split("|")[0]
        server.note(title, " — ".join(x for x in (ld["name"], ld["phone"], svc) if x), "lead",
                    "/crm/lead/" + str(i), 1, "crm")
    if len(new) > 5:
        server.note(title, f"+ {len(new) - 5}", "lead", "/crm", 1, "crm")


async def meta_tick(force=False):
    m = _db()
    if m.get("meta_on") != "1" or not m.get("meta_page_id") or not m.get("meta_page_token"):
        return None
    every = max(1, m.get_int("meta_minutes", 2)) * 60
    if not force and time.time() - _state["meta_last"] < every:
        return None
    _state["meta_last"] = time.time()

    async def run():
        import metalead
        since = m.get_int("meta_since", 0) or int(time.time()) - 7 * 86400
        try:
            result = await asyncio.to_thread(metalead.fetch_leads, m.get("meta_page_id"),
                                             m.get("meta_page_token"), max(0, since - 600))
        except Exception as e:      # noqa: BLE001
            db = _db()
            if db.get("meta_err_day") != core.iso(core.today()):
                db.set("meta_err_day", core.iso(core.today()))
                server.note("⚠ ربط إعلانات فيسبوك وانستغرام", str(e)[:300], "error", "/settings", 1, "settings")
            db.set("meta_status", core.now_str() + "  ⚠ " + str(e))
            return {"error": str(e)}
        db = _db()
        res = metalead.import_leads(db, result)
        if result.get("max_ts", 0) > db.get_int("meta_since", 0):
            db.set("meta_since", result["max_ts"])
        new = res["new"] + res["client"]
        db.set("meta_status", core.now_str() + f"  ✓ {len(result.get('forms', []))} فورم، جديد {len(new)}")
        _announce(db, res, "🎯 عميل جديد من إعلانات فيسبوك / انستغرام")
        await welcome(res["fresh"])
        return {"forms": len(result.get("forms", [])), "new": len(new), "existing": len(res["existing"]),
                "skipped": res["skipped"]}
    return await _once("meta", run)


async def sheet_tick(force=False):
    m = _db()
    if m.get("web_leads_on") != "1" or not m.get("web_leads_url"):
        return None
    every = max(2, m.get_int("web_leads_minutes", 5)) * 60
    if not force and time.time() - _state["sheet_last"] < every:
        return None
    _state["sheet_last"] = time.time()

    async def run():
        import weblead
        try:
            payload = await asyncio.to_thread(weblead.fetch, m.get("web_leads_url"), m.get("web_leads_key"))
        except Exception as e:      # noqa: BLE001
            _db().set("web_leads_status", core.now_str() + "  ⚠ " + str(e))
            return {"error": str(e)}
        db = _db()
        if not db.get("web_leads_started"):
            weblead.mark_seen(db, payload)          # first connection: old rows are not new leads
            db.set("web_leads_started", "1")
        res = weblead.import_payload(db, payload)
        new = res["new"] + res["client"]
        db.set("web_leads_status", core.now_str() + f"  ✓ جديد {len(new)}")
        _announce(db, res, "🎯 عميل جديد من الإنترنت")
        await welcome(new)
        return {"new": len(new), "existing": len(res["existing"]), "skipped": res["skipped"]}
    return await _once("sheet", run)


async def welcome(lead_ids):
    import leadwelcome
    db = _db()
    if not lead_ids:
        return
    cfg = leadwelcome.config(db)
    if cfg is None:
        return
    jobs = []
    for lid in lead_ids:
        ld = db.lead(lid)
        if ld and ld["phone"]:
            jobs.append((lid, ld["phone"], ld["name"]))
    if not jobs:
        return
    if cfg.get("error"):
        for lid, *_ in jobs:
            db._log_activity(lid, "note", "⚠ رسالة الترحيب ما اتبعتتش: " + cfg["error"])
        return
    results = await asyncio.to_thread(leadwelcome.send_all, cfg, jobs)
    db = _db()
    tpl = db.get("lead_welcome_template")
    for lid, ok, inf in results:
        if ok:
            db._log_activity(lid, "whatsapp", f"✓ اتبعتت رسالة الترحيب على واتساب أوتوماتيك (قالب {tpl})")
        else:
            db._log_activity(lid, "note", f"⚠ رسالة الترحيب ما اتبعتتش: {inf}")


# ----------------------------------------------------------------------- renewals
async def reminders_tick(force=False):
    m = _db()
    if m.get("rem_auto") != "1" and not force:
        return None
    if not m.wa_ready():
        return {"error": "WhatsApp API"} if force else None
    if not force:
        if m.get("rem_last_auto") == core.iso(core.today()):
            return None
        if time.strftime("%H:%M") < (m.get("rem_time") or "09:00"):
            return None
    m.set("rem_last_auto", core.iso(core.today()))

    async def run():
        import whatsapp_api
        db = _db()
        cfg = {"token": db.get("wa_token"), "phone_id": db.get("wa_phone_id"), "template": db.get("wa_template"),
               "lang": db.get("wa_template_lang") or "ar",
               "version": db.get("wa_api_version") or whatsapp_api.DEFAULT_VERSION}
        jobs = []
        for c in db.companies(active_only=True):
            if c["kind"] == "business":
                continue
            cdb = server.company_db(c["id"])
            server.unbind()
            for g in cdb.due_reminders():
                to = core.phone_digits(g["phone"])
                if to:
                    jobs.append((c["id"], g, to, cdb.api_params(g)))
        ok = fail = 0
        for cid, g, to, params in jobs:
            res = await asyncio.to_thread(whatsapp_api.send_template, cfg["token"], cfg["phone_id"], to,
                                          cfg["template"], cfg["lang"], params, cfg["version"])
            cdb = server.company_db(cid)
            server.unbind()
            if res[0]:
                ok += 1
                cdb.log_reminder(g, "api", "sent", message=" | ".join(params), wa_id=res[1])
            else:
                fail += 1
                cdb.log_reminder(g, "api", "failed", message=" | ".join(params), error=res[1])
            await asyncio.sleep(0.4)
        if jobs:
            server.note("🪪 تذكيرات الواتساب", f"اتبعت {ok}" + (f"، وفشل {fail}" if fail else ""), "renewals",
                        "/renewals", None, "renewals")
        return {"sent": ok, "failed": fail}
    return await _once("reminders", run)


# ----------------------------------------------------------------------- daily
async def daily_tick():
    today = core.iso(core.today())
    if _state["day"] == today:
        return
    _state["day"] = today
    m = _db()
    try:
        m.auto_backup()
        m.auto_backup_companies()
    except Exception:       # noqa: BLE001
        traceback.print_exc()
    try:
        await asyncio.to_thread(_full_backup_if_due)
    except Exception:       # noqa: BLE001
        traceback.print_exc()


def _full_backup_if_due():
    """Runs in a thread with its own connection (the zip can take a while)."""
    db = core.DB(server.main_db().path)
    try:
        if db.get("auto_backup_dir"):
            db.auto_full_backup()
    finally:
        db.close()
