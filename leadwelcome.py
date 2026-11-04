# -*- coding: utf-8 -*-
"""رسالة ترحيب واتساب تلقائية للعميل المحتمل الجديد (من إعلانات فيسبوك وانستغرام / الفورم).

WhatsApp only lets a business start a conversation with an APPROVED template, so the welcome
is a template (made once in WhatsApp Manager, e.g. "lead_welcome", with {{1}} = the name).
It goes out from the company number through whichever connection the program already has:
  1) Kapso (the number used by the WhatsApp inbox page) - the message then shows in the inbox
  2) WhatsApp Cloud API settings of the renewals page (token + phone number ID)
config() runs on the UI thread (reads the database); send_all() is pure network (worker thread).
"""
import re

DEFAULTS = {"lead_welcome_on": "0", "lead_welcome_template": "", "lead_welcome_lang": "ar",
            "lead_welcome_name": "1"}


def _g(db, k):
    return db.get(k) or DEFAULTS.get(k, "")


def config(db):
    """-> None when the welcome is off, else a dict; dict["error"] says why it cannot be sent."""
    if _g(db, "lead_welcome_on") != "1":
        return None
    cfg = {"template": _g(db, "lead_welcome_template").strip(), "lang": _g(db, "lead_welcome_lang").strip() or "ar",
           "with_name": _g(db, "lead_welcome_name") == "1"}
    if not cfg["template"]:
        cfg["error"] = "اسم قالب رسالة الترحيب مش مكتوب في الإعدادات."
        return cfg
    try:
        ready = db.inbox_ready()
    except AttributeError:
        ready = False
    if ready:
        cfg.update(via="kapso", key=db.get("kapso_api_key"), phone_id=db.get("kapso_phone_id"))
    elif db.get("wa_token") and db.get("wa_phone_id"):
        import whatsapp_api
        cfg.update(via="cloud", key=db.get("wa_token"), phone_id=db.get("wa_phone_id"),
                   version=db.get("wa_api_version") or whatsapp_api.DEFAULT_VERSION)
    else:
        cfg["error"] = ("واتساب مش مربوط بالبرنامج لسه (لا رسائل واتساب Kapso ولا WhatsApp API في متابعة الإقامات)، "
                        "فرسالة الترحيب ما اتبعتتش أوتوماتيك.")
    return cfg


def first_name(name):
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    return name.split(" ")[0] if name else ""


def send_all(cfg, jobs, kapso_transport=None):
    """jobs: [(lead_id, phone, name)] -> [(lead_id, ok, info)]"""
    out = []
    if cfg.get("via") == "kapso":
        import inbox
        client = inbox.KapsoClient(cfg["key"], cfg["phone_id"], transport=kapso_transport)
    for lid, phone, name in jobs:
        params = [first_name(name) or "عميلنا العزيز"] if cfg.get("with_name") else []
        try:
            if cfg.get("via") == "kapso":
                ok, inf = True, client.send_template(phone, cfg["template"], cfg["lang"], params)
            else:
                import inbox
                import whatsapp_api
                ok, inf = whatsapp_api.send_template(cfg["key"], cfg["phone_id"], inbox.digits(phone),
                                                     cfg["template"], cfg["lang"], params, cfg.get("version"))
        except Exception as e:          # noqa: BLE001
            ok, inf = False, str(e)
        out.append((lid, ok, inf))
    return out
