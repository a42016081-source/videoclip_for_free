"""SQLite storage: users/settings, history, stats, cache, favorites."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from config import DB_PATH

USER_COLUMNS = {"quality", "delivery", "shorts", "reframe", "subs", "sub_style",
                "sub_lang", "bot_lang", "watermark", "banned"}


@contextmanager
def _conn():
    """Connection that commits on success and ALWAYS closes (sqlite3's own
    context manager does not close, which leaks connections)."""
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db():
    with _conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            quality TEXT DEFAULT 'best', delivery TEXT DEFAULT 'auto',
            shorts INTEGER DEFAULT 0, reframe TEXT DEFAULT 'blur',
            subs INTEGER DEFAULT 0, sub_style TEXT DEFAULT 'classic',
            sub_lang TEXT DEFAULT 'auto', bot_lang TEXT DEFAULT 'en',
            watermark INTEGER DEFAULT 0, banned INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, url TEXT, start INTEGER, "end" INTEGER,
            quality TEXT, file_path TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS stats(
            user_id INTEGER PRIMARY KEY, clips INTEGER DEFAULT 0, last_clip TEXT
        );
        CREATE TABLE IF NOT EXISTS cache(
            key TEXT PRIMARY KEY, file_path TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS favorites(
            user_id INTEGER, history_id INTEGER,
            PRIMARY KEY(user_id, history_id)
        );
        """)


def get_user(uid: int) -> dict:
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if r:
            return dict(r)
        c.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))
        return dict(c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone())


def set_user(uid: int, **kw):
    bad = set(kw) - USER_COLUMNS
    if bad:
        raise ValueError(f"unknown user column(s): {bad}")
    get_user(uid)  # make sure the row exists
    keys = ", ".join(f"{k}=?" for k in kw)
    with _conn() as c:
        c.execute(f"UPDATE users SET {keys} WHERE user_id=?", list(kw.values()) + [uid])


def add_history(uid, url, s, e, q, path) -> int:
    with _conn() as c:
        cur = c.execute('INSERT INTO history(user_id,url,start,"end",quality,file_path) VALUES(?,?,?,?,?,?)',
                        (uid, url, s, e, q, path))
        c.execute("INSERT INTO stats(user_id,clips,last_clip) VALUES(?,1,CURRENT_TIMESTAMP) "
                  "ON CONFLICT(user_id) DO UPDATE SET clips=clips+1,last_clip=CURRENT_TIMESTAMP", (uid,))
        return cur.lastrowid


def get_history(uid, limit=20) -> list[dict]:
    with _conn() as c:
        return [_hrow(r) for r in c.execute(
            "SELECT * FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()]


def _hrow(r) -> dict:
    d = dict(r)
    d["end"] = d.get("end")
    return d


def get_stats(uid) -> dict:
    with _conn() as c:
        r = c.execute("SELECT * FROM stats WHERE user_id=?", (uid,)).fetchone()
        return dict(r) if r else {"clips": 0, "last_clip": None}


def global_stats() -> dict:
    with _conn() as c:
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        banned = c.execute("SELECT COUNT(*) FROM users WHERE banned=1").fetchone()[0]
        clips = c.execute("SELECT COALESCE(SUM(clips),0) FROM stats").fetchone()[0]
    return {"users": users, "banned": banned, "clips": clips}


def all_user_ids() -> list[int]:
    with _conn() as c:
        return [r["user_id"] for r in c.execute("SELECT user_id FROM users").fetchall()]


def is_banned(uid) -> bool:
    return bool(get_user(uid).get("banned"))


def ban_user(uid, b=True):
    set_user(uid, banned=int(b))


def cache_get(key):
    with _conn() as c:
        r = c.execute("SELECT file_path FROM cache WHERE key=?", (key,)).fetchone()
        if r and Path(r["file_path"]).exists():
            return r["file_path"]
        return None


def cache_put(key, path):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO cache(key,file_path) VALUES(?,?)", (key, path))


def add_favorite(uid, hid):
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO favorites VALUES(?,?)", (uid, hid))


def remove_favorite(uid, hid):
    with _conn() as c:
        c.execute("DELETE FROM favorites WHERE user_id=? AND history_id=?", (uid, hid))


def get_favorites(uid) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT h.* FROM history h JOIN favorites f ON h.id=f.history_id "
            "WHERE f.user_id=? ORDER BY h.id DESC", (uid,)).fetchall()]


def is_favorite(uid, hid) -> bool:
    with _conn() as c:
        return bool(c.execute("SELECT 1 FROM favorites WHERE user_id=? AND history_id=?",
                              (uid, hid)).fetchone())


def get_history_by_id(hid) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM history WHERE id=?", (hid,)).fetchone()
        return dict(r) if r else None
