# -*- coding: utf-8 -*-
"""رسائل واتساب داخل البرنامج - WhatsApp inbox through Kapso (WhatsApp Cloud API proxy).

How it works
------------
* The business number is connected to Kapso (coexistence: the WhatsApp Business app on
  the phone keeps working). Kapso keeps every message the number sends and receives.
* The program asks Kapso every few seconds for what is new (polling - a desktop program
  cannot receive webhooks), stores the messages in the main database and downloads the
  photos / videos / voice notes / files into  <data folder>/WhatsApp/<number>/ .
* Replies, photos and files are sent from the program through the same API.

Everything in this module that touches the network is pure Python (it runs inside a
worker thread); everything that touches the database is in InboxMixin and runs on the
UI thread only.
"""
import datetime as dt
import json
import mimetypes
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE_URL = "https://api.kapso.ai/meta/whatsapp/v24.0"
MEDIA_TYPES = ("image", "video", "audio", "document", "sticker")
STATUS_RANK = {"": 0, "pending": 1, "sent": 2, "delivered": 3, "read": 4, "played": 4, "failed": 5}

# what WhatsApp accepts when sending (type, max bytes) by file extension
SEND_RULES = {
    ".jpg": ("image", 5), ".jpeg": ("image", 5), ".png": ("image", 5),
    ".mp4": ("video", 16), ".3gp": ("video", 16),
    ".mp3": ("audio", 16), ".ogg": ("audio", 16), ".opus": ("audio", 16), ".aac": ("audio", 16),
    ".m4a": ("audio", 16), ".amr": ("audio", 16),
    ".pdf": ("document", 100), ".doc": ("document", 100), ".docx": ("document", 100),
    ".xls": ("document", 100), ".xlsx": ("document", 100), ".ppt": ("document", 100),
    ".pptx": ("document", 100), ".txt": ("document", 100),
}
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
        ".mp4": "video/mp4", ".3gp": "video/3gpp", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
        ".opus": "audio/ogg", ".aac": "audio/aac", ".m4a": "audio/mp4", ".amr": "audio/amr",
        ".pdf": "application/pdf", ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain"}
EXT_BY_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "video/mp4": ".mp4",
               "video/3gpp": ".3gp", "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/aac": ".aac",
               "audio/mp4": ".m4a", "audio/amr": ".amr", "application/pdf": ".pdf"}
AUTO_DOWNLOAD_MB = 25        # bigger files wait for a click


def T_(ar, en):
    return ar


def digits(phone):
    """International digits (Qatar 8-digit numbers get 974)."""
    d = re.sub(r"\D", "", str(phone or ""))
    if d.startswith("00"):
        d = d[2:]
    if len(d) == 8:
        d = "974" + d
    return d


def pretty_phone(d):
    d = digits(d)
    if d.startswith("974") and len(d) == 11:
        return f"+974 {d[3:7]} {d[7:]}"
    return "+" + d if d else ""


def send_kind(path):
    """-> (kind, error) for a file the user wants to send."""
    p = Path(path)
    ext = p.suffix.lower()
    size = p.stat().st_size if p.exists() else 0
    rule = SEND_RULES.get(ext)
    if not rule:
        return None, T_("نوع الملف ده مش مسموح في واتساب. المسموح: صور JPG/PNG، فيديو MP4، صوت، "
                        "و PDF و Word و Excel و PowerPoint و TXT.", "File type not allowed by WhatsApp.")
    kind, mb = rule
    if size > mb * 1024 * 1024:
        if kind in ("image", "video") and size <= 100 * 1024 * 1024 and ext in (".jpg", ".jpeg", ".png", ".mp4"):
            return "document", ""        # too big as a photo/video -> goes as a file (quality kept)
        return None, T_(f"الملف أكبر من المسموح ({mb} ميجا لنوع ده).", f"File too large (max {mb} MB).")
    if size == 0:
        return None, T_("الملف فاضي.", "Empty file.")
    return kind, ""


def mime_of(path):
    ext = Path(path).suffix.lower()
    return MIME.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def iso_utc(ts):
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------------
# parsing Kapso / Meta message objects
# ----------------------------------------------------------------------------------
def parse_message(m):
    """One message object from GET /{phone_number_id}/messages -> flat dict (or None)."""
    if not isinstance(m, dict) or not m.get("id"):
        return None
    k = m.get("kapso") or {}
    typ = (m.get("type") or "text").lower()
    direction = (k.get("direction") or "").lower()
    if direction not in ("inbound", "outbound"):
        direction = "inbound" if m.get("from") and not m.get("to") else "outbound"
    phone = k.get("phone_number") or (m.get("from") if direction == "inbound" else m.get("to"))
    try:
        ts = int(float(m.get("timestamp") or 0))
    except (TypeError, ValueError):
        ts = 0
    out = {"wamid": str(m["id"]), "direction": direction, "type": typ, "phone": digits(phone),
           "ts": ts or int(time.time()), "status": (k.get("status") or ("" if direction == "inbound" else "sent")).lower(),
           "body": "", "caption": "", "media_id": "", "media_url": "", "mime": "", "filename": "",
           "size": 0, "contact_name": (k.get("contact_name") or "").strip(), "extra": "",
           "reply_to": ((m.get("context") or {}).get("id") or "")}
    content = k.get("content") or ""
    if typ == "text":
        out["body"] = (m.get("text") or {}).get("body", "") or content
    elif typ in MEDIA_TYPES:
        md = m.get(typ) or {}
        kd = k.get("media_data") or {}
        out["media_id"] = str(md.get("id") or "")
        out["media_url"] = k.get("media_url") or kd.get("url") or md.get("link") or ""
        out["mime"] = md.get("mime_type") or kd.get("content_type") or ""
        out["filename"] = md.get("filename") or kd.get("filename") or ""
        out["caption"] = md.get("caption") or ""
        try:
            out["size"] = int(kd.get("byte_size") or md.get("file_size") or 0)
        except (TypeError, ValueError):
            out["size"] = 0
        if typ == "audio" and md.get("voice"):
            out["extra"] = json.dumps({"voice": True})
    elif typ == "location":
        loc = m.get("location") or {}
        out["body"] = " - ".join(x for x in (loc.get("name"), loc.get("address")) if x)
        out["extra"] = json.dumps({"lat": loc.get("latitude"), "lng": loc.get("longitude")})
    elif typ == "contacts":
        names = []
        for c in m.get("contacts") or []:
            nm = (c.get("name") or {}).get("formatted_name") or ""
            ph = ", ".join(p.get("phone", "") for p in c.get("phones") or [])
            names.append(f"{nm} {ph}".strip())
        out["body"] = "\n".join(names) or content
    elif typ == "reaction":
        r = m.get("reaction") or {}
        out["body"] = r.get("emoji") or ""
        out["reply_to"] = r.get("message_id") or out["reply_to"]
    elif typ == "interactive":
        it = m.get("interactive") or {}
        rep = it.get("button_reply") or it.get("list_reply") or {}
        out["body"] = rep.get("title") or ((it.get("body") or {}).get("text")) or content
    elif typ == "button":
        out["body"] = (m.get("button") or {}).get("text") or content
    elif typ == "template":
        out["body"] = content or ("📋 " + ((m.get("template") or {}).get("name") or ""))
    else:
        out["body"] = content or f"[{typ}]"
    if not out["phone"]:
        return None
    return out


# ----------------------------------------------------------------------------------
# the API client (network only - safe to use from a worker thread)
# ----------------------------------------------------------------------------------
class KapsoError(Exception):
    def __init__(self, msg, code=None, status=None):
        super().__init__(msg)
        self.code, self.status = code, status


ERRORS = {
    131047: ("مر أكتر من 24 ساعة من آخر رسالة من العميل، فواتساب مش بيسمح برسالة عادية. "
             "ابعتله من واتساب على الموبايل أو استنى لما يكلمك.",
             "More than 24 hours since the customer's last message: only a template is allowed."),
    131026: ("الرسالة متوصلتش: الرقم ده مش عليه واتساب أو مش قادر يستقبل.",
             "Message undeliverable."),
    131051: ("نوع الرسالة ده مش مدعوم.", "Unsupported message type."),
    131052: ("تعذر تحميل الملف من واتساب.", "Media download error."),
    131053: ("تعذر رفع الملف لواتساب (اتأكد من النوع والحجم).", "Media upload error."),
    130429: ("عدد الرسائل كتير في وقت قليل، حاول كمان شوية.", "Rate limit hit."),
    131056: ("بعتّ رسائل كتير لنفس الرقم في وقت قليل، استنى شوية.", "Too many messages to this number."),
}


def default_transport(method, url, headers, body, timeout):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except Exception:       # noqa: BLE001
            data = b""
        return e.code, dict(e.headers or {}), data


class KapsoClient:
    def __init__(self, api_key, phone_id, base=BASE_URL, transport=None, timeout=40):
        self.key = (api_key or "").strip()
        self.pid = str(phone_id or "").strip()
        self.base = base.rstrip("/")
        self.transport = transport or default_transport
        self.timeout = timeout

    # ---- low level ----
    def _call(self, method, path_or_url, params=None, body=None, content_type=None, auth=True, raw=False):
        url = path_or_url if path_or_url.startswith("http") else self.base + path_or_url
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v not in (None, "")})
        headers = {"User-Agent": "CreativeReps"}
        if auth:
            headers["X-API-Key"] = self.key
        if content_type:
            headers["Content-Type"] = content_type
        try:
            status, h, data = self.transport(method, url, headers, body, self.timeout)
        except urllib.error.URLError as e:
            raise KapsoError(T_("مفيش اتصال بالإنترنت أو موقع Kapso مش بيرد: ", "No connection: ")
                             + str(getattr(e, "reason", e)))
        except OSError as e:
            raise KapsoError(T_("مشكلة في الاتصال: ", "Connection problem: ") + str(e))
        if status >= 400:
            raise self._error(status, data)
        if raw:
            ctype = ""
            for hk, hv in (h or {}).items():
                if hk.lower() == "content-type":
                    ctype = hv
            return data, ctype
        try:
            return json.loads((data or b"{}").decode("utf-8") or "{}")
        except ValueError:
            raise KapsoError(T_("Kapso رجّع رد مش مفهوم.", "Unexpected reply from Kapso."), status=status)

    @staticmethod
    def _error(status, data):
        msg, code = "", None
        try:
            j = json.loads((data or b"{}").decode("utf-8") or "{}")
            err = j.get("error", j)
            if isinstance(err, dict):
                code = err.get("code")
                msg = (err.get("error_data") or {}).get("details") if isinstance(err.get("error_data"), dict) else ""
                msg = msg or err.get("error_user_msg") or err.get("message") or ""
            else:
                msg = str(err)
            if not msg and isinstance(j.get("errors"), list) and j["errors"]:
                msg = str(j["errors"][0])
        except Exception:       # noqa: BLE001
            msg = (data or b"")[:200].decode("utf-8", "replace")
        if code in ERRORS:
            msg = ERRORS[code][0]
        elif status in (401, 403):
            msg = T_("مفتاح Kapso (API key) غلط أو مالوش صلاحية على الرقم ده.", "Wrong Kapso API key.")
        elif status == 404:
            msg = T_("الـ Phone number ID غلط أو الرقم مش متوصل في Kapso.", "Wrong phone number ID.") + \
                (f" ({msg})" if msg else "")
        return KapsoError(f"{msg or 'HTTP ' + str(status)}", code=code, status=status)

    # ---- reading ----
    def test(self):
        """Raises KapsoError if the key / number are wrong."""
        return self._call("GET", f"/{self.pid}/conversations", {"limit": 1})

    def list_messages(self, since_ts=None, direction=None, max_pages=60, limit=100):
        out, after = [], None
        for _ in range(max_pages):
            j = self._call("GET", f"/{self.pid}/messages",
                           {"since": iso_utc(since_ts) if since_ts else None, "direction": direction,
                            "limit": limit, "after": after})
            page = j.get("data") or []
            for m in page:
                p = parse_message(m)
                if p:
                    out.append(p)
            nxt = ((j.get("paging") or {}).get("cursors") or {}).get("after")
            if not page or not nxt or nxt == after:
                break
            after = nxt
        return out

    def download_media(self, media_url="", media_id=""):
        """-> (bytes, content type). Tries Kapso's link first, then the media id."""
        errors = []
        if media_url:
            host = urllib.parse.urlparse(media_url).netloc.lower()
            try:
                return self._call("GET", media_url, auth=("kapso" in host), raw=True)
            except KapsoError as e:
                errors.append(str(e))
        if media_id:
            try:
                j = self._call("GET", f"/{media_id}", {"phone_number_id": self.pid})
                url = j.get("download_url") or j.get("url")
                if url:
                    host = urllib.parse.urlparse(url).netloc.lower()
                    data, ctype = self._call("GET", url, auth=("kapso" in host), raw=True)
                    return data, (j.get("mime_type") or ctype)
            except KapsoError as e:
                errors.append(str(e))
        raise KapsoError(" / ".join(errors) or T_("مفيش رابط للملف.", "No media link."))

    # ---- sending ----
    def upload_media(self, path, mime=None):
        p = Path(path)
        boundary = "----CR" + uuid.uuid4().hex
        mime = mime or mime_of(p)
        name = p.name.replace('"', "")
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"messaging_product\"\r\n\r\nwhatsapp\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\n{mime}\r\n".encode(),
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
             f"Content-Type: {mime}\r\n\r\n").encode("utf-8") + p.read_bytes() + b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        j = self._call("POST", f"/{self.pid}/media", body=b"".join(parts),
                       content_type=f"multipart/form-data; boundary={boundary}")
        mid = j.get("id")
        if not mid:
            raise KapsoError(T_("الملف اترفع بس مرجعش رقم.", "Upload returned no id."))
        return str(mid)

    def _send(self, payload):
        body = json.dumps(dict({"messaging_product": "whatsapp", "recipient_type": "individual"}, **payload),
                          ensure_ascii=False).encode("utf-8")
        j = self._call("POST", f"/{self.pid}/messages", body=body, content_type="application/json")
        mid = ((j.get("messages") or [{}])[0] or {}).get("id")
        if not mid:
            raise KapsoError(T_("واتساب مرجعش رقم الرسالة.", "No message id returned."))
        return str(mid)

    def send_template(self, to, name, lang="ar", params=()):
        """An approved template (needed for the first message to someone who never wrote to us)."""
        tpl = {"name": name, "language": {"code": lang or "ar"}}
        if params:
            tpl["components"] = [{"type": "body", "parameters": [
                {"type": "text", "text": str(p).replace("\n", " ").replace("\t", " ")[:1000]} for p in params]}]
        return self._send({"to": digits(to), "type": "template", "template": tpl})

    def send_text(self, to, text, reply_to=None):
        p = {"to": digits(to), "type": "text", "text": {"body": text, "preview_url": True}}
        if reply_to:
            p["context"] = {"message_id": reply_to}
        return self._send(p)

    def send_media(self, to, kind, media_id, caption="", filename=""):
        obj = {"id": media_id}
        if caption and kind in ("image", "video", "document"):
            obj["caption"] = caption[:1024]
        if kind == "document" and filename:
            obj["filename"] = filename
        return self._send({"to": digits(to), "type": kind, kind: obj})


def run_sync(client, since_ts, status_since=None):
    """Worker-thread job: new messages (+ fresh statuses of recent sent messages)."""
    msgs = client.list_messages(since_ts)
    statuses = []
    if status_since:
        try:
            statuses = [(m["wamid"], m["status"]) for m in
                        client.list_messages(status_since, direction="outbound", max_pages=3)]
        except KapsoError:
            statuses = []
    return {"messages": msgs, "statuses": statuses}


def download_to(client, job, folder):
    """job: dict(wamid, phone, type, media_url, media_id, mime, filename, ts) -> saved path."""
    data, ctype = client.download_media(job.get("media_url"), job.get("media_id"))
    if not data:
        raise KapsoError(T_("الملف وصل فاضي.", "Empty file."))
    ctype = (ctype or job.get("mime") or "").split(";")[0].strip().lower()
    name = job.get("filename") or ""
    ext = Path(name).suffix.lower() if name else ""
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext or ""):
        ext = ""
    if not ext:
        ext = EXT_BY_MIME.get(ctype) or EXT_BY_MIME.get((job.get("mime") or "").split(";")[0]) or \
            mimetypes.guess_extension(ctype or "") or ".bin"
    stamp = dt.datetime.fromtimestamp(int(job.get("ts") or time.time())).strftime("%Y-%m-%d_%H%M%S")
    label = {"image": "صورة", "video": "فيديو", "audio": "صوت", "sticker": "ملصق"}.get(job["type"], "ملف")
    base = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", Path(name).stem).strip()[:60] if name else label
    base = base.rstrip(". ") or label
    short = re.sub(r"[^A-Za-z0-9]", "", job["wamid"])[-10:]
    d = Path(folder) / re.sub(r"\D", "", job["phone"])
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{stamp} {base} {short}{ext}"
    n = 2
    while target.exists():
        target = d / f"{stamp} {base} {short} ({n}){ext}"
        n += 1
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    return str(target)


# ----------------------------------------------------------------------------------
# database side (UI thread)
# ----------------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS wa_chats (
    phone TEXT PRIMARY KEY, contact_name TEXT DEFAULT '', client_id INTEGER, lead_id INTEGER,
    last_ts INTEGER DEFAULT 0, last_text TEXT DEFAULT '', last_type TEXT DEFAULT '',
    last_dir TEXT DEFAULT '', last_status TEXT DEFAULT '', unread INTEGER DEFAULT 0,
    last_inbound_ts INTEGER DEFAULT 0, link_manual INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS wa_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, wamid TEXT UNIQUE, phone TEXT NOT NULL,
    direction TEXT, type TEXT, body TEXT DEFAULT '', caption TEXT DEFAULT '',
    media_id TEXT DEFAULT '', media_url TEXT DEFAULT '', mime TEXT DEFAULT '', filename TEXT DEFAULT '',
    size INTEGER DEFAULT 0, local_path TEXT DEFAULT '', media_state TEXT DEFAULT '',
    media_tries INTEGER DEFAULT 0, media_error TEXT DEFAULT '', status TEXT DEFAULT '',
    error TEXT DEFAULT '', ts INTEGER, reply_to TEXT DEFAULT '', extra TEXT DEFAULT '',
    sent_by TEXT DEFAULT '', file_id INTEGER, ts_local INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_wa_msg_phone ON wa_messages(phone, ts);
CREATE INDEX IF NOT EXISTS idx_wa_msg_media ON wa_messages(media_state);
"""

DEFAULTS = {"inbox_on": "0", "kapso_api_key": "", "kapso_phone_id": "", "inbox_days_back": "14",
            "inbox_autosave": "1", "inbox_sound": "1", "inbox_status": ""}


def preview_text(row):
    t = row["type"] if "type" in row.keys() else row["last_type"]
    icons = {"image": "📷 صورة", "video": "🎥 فيديو", "audio": "🎤 رسالة صوتية", "document": "📄 ملف",
             "sticker": "🙂 ملصق", "location": "📍 موقع", "contacts": "👤 جهة اتصال",
             "reaction": "تفاعل"}
    body = (row["caption"] or row["body"]) if "caption" in row.keys() else row["last_text"]
    if t == "document" and "filename" in row.keys() and row["filename"]:
        body = body or row["filename"]
    if t in icons:
        return f"{icons[t]}" + (f": {body}" if body and t != "reaction" else (f" {body}" if body else ""))
    return (body or "").replace("\n", " ")


class InboxMixin:
    """Mixed into core.DB (used on the main company's database)."""

    def _inbox_setup(self):
        self.con.executescript(SCHEMA)
        for k, v in DEFAULTS.items():
            self.con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
        # users created before the inbox existed: reps get it once (admins have everything)
        if not self.con.execute("SELECT 1 FROM settings WHERE key='inbox_perms_added'").fetchone():
            try:
                for u in self.con.execute("SELECT id, role, perms FROM users").fetchall():
                    if u["role"] == "rep":
                        try:
                            perms = set(json.loads(u["perms"] or "[]"))
                        except ValueError:
                            perms = set()
                        perms.add("inbox")
                        self.con.execute("UPDATE users SET perms=? WHERE id=?",
                                         (json.dumps(sorted(perms)), u["id"]))
            except Exception:       # noqa: BLE001  (no users table yet)
                pass
            self.con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('inbox_perms_added', '1')")
        # the data folder moved (restore on another PC / user): point the files at the new place
        here = str(self.path.parent / "WhatsApp")
        for r in self.con.execute("SELECT id, local_path FROM wa_messages WHERE local_path<>'' AND "
                                  "local_path NOT LIKE ?", (here + "%",)).fetchall():
            parts = re.split(r"[\\/]WhatsApp[\\/]", r["local_path"], maxsplit=1)
            if len(parts) == 2:
                self.con.execute("UPDATE wa_messages SET local_path=? WHERE id=?",
                                 (str(Path(here) / Path(parts[1].replace("\\", "/"))), r["id"]))
        self.con.commit()

    # ---------- settings ----------
    def inbox_ready(self):
        return self.get("inbox_on") == "1" and bool(self.get("kapso_api_key")) and bool(self.get("kapso_phone_id"))

    def inbox_client(self, transport=None):
        return KapsoClient(self.get("kapso_api_key"), self.get("kapso_phone_id"), transport=transport)

    def inbox_media_dir(self):
        d = self.path.parent / "WhatsApp"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def inbox_since(self):
        """Where the next sync starts (server timestamps, with a 10-minute overlap)."""
        # only server timestamps count (a wrong PC clock must not make us skip messages)
        r = self.one("SELECT MAX(ts) FROM wa_messages WHERE ts_local=0")
        if r and r[0]:
            return int(r[0]) - 600
        days = max(1, min(90, self.get_int("inbox_days_back", 14)))
        return int(time.time()) - days * 86400

    # ---------- storing what arrived ----------
    def inbox_store(self, messages, statuses=()):
        """-> dict(new_inbound=[rows...], changed=set(phones))"""
        new_in, changed = [], set()
        self._idx_cache = None
        with self.atomic():
            for m in sorted(messages, key=lambda x: x["ts"]):
                old = self.one("SELECT id, status, media_url, media_id, media_state, local_path, ts_local "
                               "FROM wa_messages WHERE wamid=?", (m["wamid"],))
                if old is not None:
                    if STATUS_RANK.get(m["status"], 0) > STATUS_RANK.get(old["status"] or "", 0):
                        self.con.execute("UPDATE wa_messages SET status=? WHERE id=?", (m["status"], old["id"]))
                        changed.add(m["phone"])
                    if old["ts_local"]:         # our own message came back: take the server's time
                        self.con.execute("UPDATE wa_messages SET ts=?, ts_local=0 WHERE id=?", (m["ts"], old["id"]))
                    if (m["media_url"] or m["media_id"]) and not (old["media_url"] or old["media_id"]):
                        state = old["media_state"]
                        if not state and not old["local_path"] and m["type"] in MEDIA_TYPES:
                            state = "pending"
                        self.con.execute("UPDATE wa_messages SET media_url=?, media_id=?, media_state=? WHERE id=?",
                                         (m["media_url"], m["media_id"], state, old["id"]))
                        changed.add(m["phone"])
                    continue
                if m["type"] == "reaction" and not m["body"]:
                    continue                    # a removed reaction
                state = ""
                if m["type"] in MEDIA_TYPES and (m["media_url"] or m["media_id"]):
                    state = "pending" if (m["size"] or 0) <= AUTO_DOWNLOAD_MB * 1024 * 1024 else "manual"
                self.con.execute(
                    "INSERT INTO wa_messages(wamid, phone, direction, type, body, caption, media_id, media_url, "
                    "mime, filename, size, media_state, status, ts, reply_to, extra) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (m["wamid"], m["phone"], m["direction"], m["type"], m["body"], m["caption"], m["media_id"],
                     m["media_url"], m["mime"], m["filename"], m["size"], state, m["status"], m["ts"],
                     m["reply_to"], m["extra"]))
                self._inbox_touch_chat(m)
                changed.add(m["phone"])
                if m["direction"] == "inbound":
                    new_in.append(m)
            for wamid, st in statuses:
                old = self.one("SELECT id, phone, status FROM wa_messages WHERE wamid=?", (wamid,))
                if old is not None and STATUS_RANK.get(st, 0) > STATUS_RANK.get(old["status"] or "", 0):
                    self.con.execute("UPDATE wa_messages SET status=? WHERE id=?", (st, old["id"]))
                    changed.add(old["phone"])
            for ph in changed:
                self._inbox_refresh_last(ph)
        return {"new_inbound": new_in, "changed": changed}

    def _inbox_touch_chat(self, m):
        ph = m["phone"]
        c = self.one("SELECT * FROM wa_chats WHERE phone=?", (ph,))
        if c is None:
            self.con.execute("INSERT INTO wa_chats(phone, contact_name) VALUES(?,?)", (ph, m.get("contact_name", "")))
            if getattr(self, "_idx_cache", None) is None:
                self._idx_cache = self._inbox_index()
            self._inbox_autolink(ph, self._idx_cache)
        elif m.get("contact_name") and m["contact_name"] != c["contact_name"]:
            self.con.execute("UPDATE wa_chats SET contact_name=? WHERE phone=?", (m["contact_name"], ph))
        if m["direction"] == "inbound":
            self.con.execute("UPDATE wa_chats SET unread=unread+1, "
                             "last_inbound_ts=MAX(last_inbound_ts, ?) WHERE phone=?", (m["ts"], ph))

    def _inbox_refresh_last(self, ph):
        r = self.one("SELECT * FROM wa_messages WHERE phone=? AND type<>'reaction' ORDER BY ts DESC, id DESC LIMIT 1",
                     (ph,))
        if r is None:
            return
        self.con.execute("UPDATE wa_chats SET last_ts=?, last_text=?, last_type=?, last_dir=?, last_status=? "
                         "WHERE phone=?", (r["ts"], (r["caption"] or r["body"] or r["filename"] or "")[:300],
                                           r["type"], r["direction"], r["status"], ph))

    def _inbox_index(self):
        """phone key -> client id / lead id (open leads win), built once."""
        from crm import phone_key
        clients, leads = {}, {}
        for r in self.q("SELECT id, phone, phone2 FROM clients ORDER BY id"):
            for p in (r["phone"], r["phone2"]):
                k = phone_key(p)
                if len(k) >= 7:
                    clients.setdefault(k, r["id"])
        try:
            closed = {self.crm_won(), self.crm_lost()}
            rows = self.q("SELECT id, phone, stage FROM leads WHERE phone<>'' ORDER BY id DESC")
        except Exception:       # noqa: BLE001
            closed, rows = set(), []
        for r in rows:
            k = phone_key(r["phone"])
            if len(k) < 7:
                continue
            if k not in leads or (r["stage"] not in closed and leads[k][1] in closed):
                leads[k] = (r["id"], r["stage"])
        return clients, {k: v[0] for k, v in leads.items()}

    def _inbox_autolink(self, ph, index=None):
        """Link a chat to the client / lead with the same phone number."""
        from crm import phone_key
        clients, leads = index or self._inbox_index()
        k = phone_key(ph)
        self.con.execute("UPDATE wa_chats SET client_id=?, lead_id=? WHERE phone=?",
                         (clients.get(k), leads.get(k), ph))

    def inbox_relink_all(self):
        """New clients / leads may have been added: link chats that are still unlinked."""
        rows = self.q("SELECT phone FROM wa_chats WHERE link_manual=0 AND client_id IS NULL")
        if not rows:
            return
        index = self._inbox_index()
        with self.atomic():
            for r in rows:
                self._inbox_autolink(r["phone"], index)

    def inbox_set_link(self, phone, client_id=None, lead_id=None):
        self.x("UPDATE wa_chats SET client_id=?, lead_id=?, link_manual=1 WHERE phone=?",
               (client_id, lead_id, phone))
        self.audit("update", "محادثة واتساب|WhatsApp chat", None, pretty_phone(phone),
                   f"client={client_id or ''} lead={lead_id or ''}")

    # ---------- reading ----------
    def inbox_chats(self, search="", flt="all"):
        s = f"%{(search or '').strip()}%"
        sql = """SELECT ch.*, c.name AS client_name, c.code AS client_code, l.name AS lead_name,
                        l.code AS lead_code, l.assigned_user_id AS lead_user
                 FROM wa_chats ch LEFT JOIN clients c ON c.id=ch.client_id
                 LEFT JOIN leads l ON l.id=ch.lead_id
                 WHERE ch.last_ts>0 AND (ch.phone LIKE ? OR ch.contact_name LIKE ? OR COALESCE(c.name,'') LIKE ?
                       OR COALESCE(l.name,'') LIKE ? OR COALESCE(c.code,'') LIKE ? OR ch.last_text LIKE ?)"""
        args = [s] * 6
        if flt == "unread":
            sql += " AND ch.unread>0"
        elif flt == "clients":
            sql += " AND ch.client_id IS NOT NULL"
        elif flt == "leads":
            sql += " AND ch.lead_id IS NOT NULL AND ch.client_id IS NULL"
        elif flt == "unknown":
            sql += " AND ch.client_id IS NULL AND ch.lead_id IS NULL"
        rows = self.q(sql + " ORDER BY ch.last_ts DESC", args)
        if self.own_only:
            ids = self.visible_client_ids() or set()
            uid = self.user["id"]
            rows = [r for r in rows if (r["client_id"] is None and r["lead_id"] is None)
                    or (r["client_id"] in ids) or (r["lead_user"] == uid)]
        return rows

    def inbox_chat(self, phone):
        return self.one("""SELECT ch.*, c.name AS client_name, c.code AS client_code, l.name AS lead_name,
                                  l.code AS lead_code, l.assigned_user_id AS lead_user
                           FROM wa_chats ch LEFT JOIN clients c ON c.id=ch.client_id
                           LEFT JOIN leads l ON l.id=ch.lead_id WHERE ch.phone=?""", (phone,))

    def inbox_messages(self, phone, limit=200, after_id=None):
        if after_id:
            return self.q("SELECT * FROM wa_messages WHERE phone=? AND id>? ORDER BY ts, id", (phone, after_id))
        rows = self.q("SELECT * FROM wa_messages WHERE phone=? ORDER BY ts DESC, id DESC LIMIT ?", (phone, limit))
        return list(reversed(rows))

    def inbox_message(self, mid):
        return self.one("SELECT * FROM wa_messages WHERE id=?", (mid,))

    def inbox_mark_read(self, phone):
        self.x("UPDATE wa_chats SET unread=0 WHERE phone=?", (phone,))

    def inbox_unread_total(self):
        rows = self.inbox_chats(flt="unread") if self.own_only else None
        if rows is not None:
            return sum(r["unread"] for r in rows)
        r = self.one("SELECT COALESCE(SUM(unread),0) FROM wa_chats")
        return int(r[0] or 0)

    def inbox_window_open(self, phone):
        """True while the 24-hour customer-service window is open."""
        r = self.one("SELECT last_inbound_ts FROM wa_chats WHERE phone=?", (phone,))
        return bool(r and r[0] and time.time() - r[0] < 24 * 3600)

    # ---------- media ----------
    def inbox_pending_media(self, limit=6):
        return self.q("SELECT * FROM wa_messages WHERE media_state='pending' AND media_tries<3 "
                      "ORDER BY ts DESC LIMIT ?", (limit,))

    def inbox_request_media(self, mid):
        self.x("UPDATE wa_messages SET media_state='pending', media_tries=0, media_error='' WHERE id=?", (mid,))

    def inbox_media_done(self, wamid, path=None, error=""):
        """-> (row, file_id saved to the client's files or None)"""
        r = self.one("SELECT * FROM wa_messages WHERE wamid=?", (wamid,))
        if r is None:
            return None, None
        if path:
            self.x("UPDATE wa_messages SET local_path=?, media_state='ok', media_error='' WHERE id=?", (path, r["id"]))
        else:
            tries = r["media_tries"] + 1
            self.x("UPDATE wa_messages SET media_tries=?, media_error=?, media_state=? WHERE id=?",
                   (tries, error[:300], "pending" if tries < 3 else "failed", r["id"]))
            return self.inbox_message(r["id"]), None
        fid = None
        if self.get("inbox_autosave") != "0" and r["direction"] == "inbound" and r["type"] != "sticker":
            try:
                fid = self.inbox_save_to_client(r["id"])
            except Exception:       # noqa: BLE001  (the file stays in the WhatsApp folder anyway)
                fid = None
        return self.inbox_message(r["id"]), fid

    def inbox_save_to_client(self, mid, force=False):
        """Copy a received photo / file into the linked client's files (category واتساب)."""
        r = self.inbox_message(mid)
        if r is None or not r["local_path"] or not Path(r["local_path"]).exists():
            return None
        if r["file_id"] and not force:
            return r["file_id"]
        ch = self.one("SELECT client_id FROM wa_chats WHERE phone=?", (r["phone"],))
        if not ch or not ch["client_id"] or not self.client(ch["client_id"]):
            return None
        note = (r["caption"] or "") + ("  " if r["caption"] else "") + \
            "واتساب " + dt.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M")
        add = getattr(self.add_file, "__wrapped__", None)
        fid = add(self, "client", ch["client_id"], r["local_path"], "واتساب|WhatsApp", note) if add \
            else self.add_file("client", ch["client_id"], r["local_path"], "واتساب|WhatsApp", note)
        self.x("UPDATE wa_messages SET file_id=? WHERE id=?", (fid, mid))
        return fid

    # ---------- sending (the network part runs in a worker) ----------
    def inbox_add_outgoing(self, phone, kind, body="", caption="", local_path="", filename=""):
        """The message shows at once as ⏱ and gets its real id when WhatsApp accepts it."""
        ph = digits(phone)
        u = self.user
        now = int(time.time())
        with self.atomic():
            if not self.one("SELECT 1 FROM wa_chats WHERE phone=?", (ph,)):
                self.con.execute("INSERT INTO wa_chats(phone) VALUES(?)", (ph,))
                self._inbox_autolink(ph)
            mid = self.con.execute(
                "INSERT INTO wa_messages(wamid, phone, direction, type, body, caption, filename, size, local_path, "
                "media_state, status, ts, sent_by, ts_local) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                ("local-" + uuid.uuid4().hex, ph, "outbound", kind, body, caption, filename,
                 Path(local_path).stat().st_size if local_path else 0, local_path,
                 "ok" if local_path else "", "pending", now,
                 (u["full_name"] or u["username"]) if u is not None else "")).lastrowid
            self._inbox_refresh_last(ph)
            self.audit("add", "رسالة واتساب|WhatsApp message", mid, pretty_phone(ph),
                       (body or caption or filename)[:120])
        return mid

    def inbox_sent(self, mid, wamid=None, error=""):
        """-> id of a copy the sync brought first (removed now), or None"""
        r = self.inbox_message(mid)
        if r is None:
            return None
        removed = None
        with self.atomic():
            if wamid:
                status, ts, ts_local = "sent", r["ts"], r["ts_local"]
                murl, mmid = r["media_url"], r["media_id"]
                dup = self.one("SELECT * FROM wa_messages WHERE wamid=?", (wamid,))
                if dup is not None and dup["id"] != mid:
                    # the sync already brought it: keep ours (it has the local file) and drop the copy
                    if STATUS_RANK.get(dup["status"] or "", 0) > STATUS_RANK["sent"]:
                        status = dup["status"]
                    ts, ts_local = dup["ts"], 0
                    murl, mmid = murl or dup["media_url"], mmid or dup["media_id"]
                    self.con.execute("DELETE FROM wa_messages WHERE id=?", (dup["id"],))
                    removed = dup["id"]
                self.con.execute("UPDATE wa_messages SET wamid=?, status=?, error='', ts=?, ts_local=?, "
                                 "media_url=?, media_id=? WHERE id=?", (wamid, status, ts, ts_local, murl, mmid, mid))
            else:
                self.con.execute("UPDATE wa_messages SET status='failed', error=? WHERE id=?", (error[:400], mid))
            self._inbox_refresh_last(r["phone"])
        return removed

    def inbox_retry(self, mid):
        self.x("UPDATE wa_messages SET status='pending', error='', wamid=? WHERE id=?",
               ("local-" + uuid.uuid4().hex, mid))

    def inbox_delete_failed(self, mid):
        r = self.inbox_message(mid)
        if r is not None and r["status"] == "failed":
            self.x("DELETE FROM wa_messages WHERE id=?", (mid,))
            self._inbox_refresh_last(r["phone"])
            self._commit()
