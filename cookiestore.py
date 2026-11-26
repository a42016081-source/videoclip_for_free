"""Persistent YouTube cookies.

Problem: YouTube rotates cookies. yt-dlp refreshes the cookie jar, but the old code re-copied the ORIGINAL
(stale) secret file on every restart and never saved the refreshed jar -> cookies had to be set again and again.

Fix: the live jar is kept in MongoDB. On start we load it from Mongo (falling back to COOKIES_FILE once),
after every yt-dlp run we save the refreshed jar back, and an admin can replace it with /cookies in Telegram.
"""
import hashlib
import logging
import threading
from pathlib import Path
from config import DATA_DIR, COOKIES_FILE as ENV_COOKIES
import database

log = logging.getLogger(__name__)
PATH = DATA_DIR / "cookies.txt"
_lock = threading.Lock()
_last_hash = ""


def _h(t: str) -> str:
    return hashlib.sha1(t.encode("utf-8", "ignore")).hexdigest()


def valid(text: str) -> bool:
    return "youtube.com" in text and "\t" in text


def init():
    """Call once after init_db(). Mongo copy wins over the env/secret file."""
    global _last_hash
    with _lock:
        stored = database.cookies_get()
        if stored and valid(stored):
            PATH.write_text(stored, encoding="utf-8")
            log.info("Cookies loaded from MongoDB")
        elif ENV_COOKIES and Path(ENV_COOKIES).is_file():
            txt = Path(ENV_COOKIES).read_text(encoding="utf-8", errors="ignore")
            PATH.write_text(txt, encoding="utf-8")
            database.cookies_put(txt)
            log.info("Cookies seeded from COOKIES_FILE into MongoDB")
        _last_hash = _h(PATH.read_text(encoding="utf-8", errors="ignore")) if PATH.exists() else ""


def path() -> str | None:
    return str(PATH) if PATH.exists() else None


def persist():
    """Save the jar back to Mongo if yt-dlp changed it (refreshed cookies)."""
    global _last_hash
    if not PATH.exists():
        return
    with _lock:
        txt = PATH.read_text(encoding="utf-8", errors="ignore")
        if txt and valid(txt) and _h(txt) != _last_hash:
            if database.cookies_put(txt):
                _last_hash = _h(txt)
                log.info("Refreshed cookies saved to MongoDB")


def replace(text: str) -> bool:
    """Admin upload: write file + Mongo."""
    global _last_hash
    if not valid(text):
        return False
    with _lock:
        PATH.write_text(text, encoding="utf-8")
        _last_hash = _h(text)
        database.cookies_put(text)
    return True
