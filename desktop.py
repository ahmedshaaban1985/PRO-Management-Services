# -*- coding: utf-8 -*-
"""PRO Management Services - desktop edition.

Runs the very same program as the web version (server.py + web/) on this computer only:
a private server on 127.0.0.1 and an app window (Microsoft Edge / Chrome in "app" mode).
Its data is kept on this computer, separate from the web version.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP = "PROManagement"


def base_dir() -> Path:
    root = os.environ.get("PROGRAMDATA") if sys.platform == "win32" else None
    p = Path(root) / APP if root else Path.home() / f".{APP}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    p = (Path(root) / APP) if root else Path.home() / f".{APP}-user"
    p.mkdir(parents=True, exist_ok=True)
    return p


DATA = base_dir() / "data"
STATE = user_dir() / "running.json"


def alive(port) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1.5) as r:
            return r.status == 200
    except Exception:       # noqa: BLE001
        return False


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def find_browser():
    cands = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if root:
            cands += [Path(root) / "Microsoft/Edge/Application/msedge.exe",
                      Path(root) / "Google/Chrome/Application/chrome.exe"]
    for c in cands:
        if c.exists():
            return str(c)
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        w = shutil.which(name)
        if w:
            return w
    return None


def open_window(url, wait=False):
    """App window without browser bars; returns when the window is closed (if wait)."""
    exe = find_browser()
    if not exe:
        import webbrowser
        webbrowser.open(url)
        return False
    profile = user_dir() / "window"
    args = [exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-features=Translate", "--window-size=1366,850"]
    flags = 0x08000000 if sys.platform == "win32" else 0      # CREATE_NO_WINDOW
    p = subprocess.Popen(args, creationflags=flags)
    if wait:
        p.wait()
    return True


def fix_streams():
    """A windowed program has no console: sys.stdout/stderr are None, and libraries that
    print or check isatty() crash. Send everything to a log file instead."""
    if sys.stdout is None or sys.stderr is None:
        log = open(user_dir() / "desktop.log", "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = log
        if sys.stderr is None:
            sys.stderr = log


def main():
    fix_streams()
    # already running? just open another window on it
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
        if alive(st["port"]):
            open_window(f"http://127.0.0.1:{st['port']}/")
            return
    except Exception:       # noqa: BLE001
        pass

    DATA.mkdir(parents=True, exist_ok=True)
    os.environ["PROMS_DATA"] = str(DATA)
    os.environ["PROMS_SECURE_COOKIE"] = "0"        # plain http on this computer only
    os.environ["PROMS_DESKTOP"] = "1"
    port = free_port()

    import uvicorn
    import server          # noqa: F401  (the same program as the web version)
    cfg = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning",
                         workers=1, access_log=False, log_config=None)
    srv = uvicorn.Server(cfg)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if alive(port):
            break
        time.sleep(0.15)
    STATE.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")
    try:
        if os.environ.get("PROMS_NO_WINDOW"):          # tests
            time.sleep(float(os.environ["PROMS_NO_WINDOW"]))
        elif open_window(f"http://127.0.0.1:{port}/", wait=True):
            # Edge/Chrome may hand the window to a process it already had: keep serving while
            # any window of our profile is still open
            while profile_in_use():
                time.sleep(2)
        else:
            # no Edge/Chrome: the program keeps running until Windows signs out
            while True:
                time.sleep(60)
    finally:
        srv.should_exit = True
        try:
            STATE.unlink()
        except OSError:
            pass
        th.join(timeout=5)


def profile_in_use() -> bool:
    """True while a browser window using our private profile is open."""
    lock = user_dir() / "window" / "lockfile"
    if sys.platform == "win32":
        if not lock.exists():
            return False
        try:
            with open(lock, "a"):        # Windows: refused while the browser holds it
                return False
        except OSError:
            return True
    return (user_dir() / "window" / "SingletonLock").exists()


if __name__ == "__main__":
    main()
