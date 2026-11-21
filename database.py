"""MongoDB-backed storage with a SQLite fallback and legacy synchronous API."""
import asyncio, json, logging, sqlite3, threading
from pathlib import Path
from datetime import datetime, timezone
from config import DB_PATH, MONGODB_URI, MONGODB_DB
log = logging.getLogger(__name__)
USER_COLUMNS = {"quality", "delivery", "shorts", "reframe", "subs", "sub_style", "sub_lang", "bot_lang", "watermark", "banned", "sub_cfg", "allowed"}
_loop = None
_thread = None
_client = None
_db = None
_mongo_ok = False


def _mongo_loop():
    global _loop, _thread
    if _loop and _loop.is_running(): return _loop
    ready = threading.Event()
    def run():
        global _loop
        _loop = asyncio.new_event_loop(); asyncio.set_event_loop(_loop); ready.set(); _loop.run_forever()
    _thread = threading.Thread(target=run, name="mongo-async-loop", daemon=True); _thread.start(); ready.wait(5)
    return _loop


def _run(coro, timeout=15):
    return asyncio.run_coroutine_threadsafe(coro, _mongo_loop()).result(timeout)


def _sqlite_init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, quality TEXT DEFAULT 'best', delivery TEXT DEFAULT 'auto', shorts INTEGER DEFAULT 0, reframe TEXT DEFAULT 'blur', subs INTEGER DEFAULT 0, sub_style TEXT DEFAULT 'classic', sub_lang TEXT DEFAULT 'auto', bot_lang TEXT DEFAULT 'en', watermark INTEGER DEFAULT 0, banned INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP, sub_cfg TEXT DEFAULT '', allowed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,url TEXT,start INTEGER,"end" INTEGER,quality TEXT,file_path TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS stats(user_id INTEGER PRIMARY KEY,clips INTEGER DEFAULT 0,last_clip TEXT);
        CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY,file_path TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER,history_id INTEGER,PRIMARY KEY(user_id,history_id));
        CREATE TABLE IF NOT EXISTS autopost_settings(user_id INTEGER PRIMARY KEY, settings TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,user_id INTEGER,state TEXT,payload TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS cache_index(key TEXT PRIMARY KEY,file_path TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);''')
        cols={r[1] for r in c.execute('PRAGMA table_info(users)')}
        for col, ddl in (("sub_cfg", "TEXT DEFAULT ''"),("allowed","INTEGER DEFAULT 0")):
            if col not in cols: c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")


def init_db():
    """Initialize persistent Mongo once; return False and use SQLite on failure."""
    global _client, _db, _mongo_ok
    if MONGODB_URI:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            async def connect():
                global _client, _db
                _client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
                await _client.admin.command("ping")
                _db = _client[MONGODB_DB]
                await _db.users.create_index("user_id", unique=True)
                await _db.history.create_index([("user_id",1),("created_at",-1)])
                await _db.history.create_index("id", unique=True)
                await _db.jobs.create_index("id", unique=True)
                await _db.jobs.create_index("updated_at")
                await _db.autopost_settings.create_index("user_id", unique=True)
                await _db.favorites.create_index([("user_id",1),("history_id",1)], unique=True)
                await _db.cache_index.create_index("key", unique=True)
            _run(connect(), 12); _mongo_ok=True; return True
        except Exception as e:
            log.warning("MongoDB connection failed (%s); using local SQLite. Settings will not persist across runner restarts.", type(e).__name__)
    else:
        log.warning("MONGODB_URI is not set; using local SQLite. Settings will not persist across runner restarts.")
    _sqlite_init(); return False


def persistent(): return _mongo_ok


def _sdb(): return sqlite3.connect(DB_PATH, timeout=30)
def _user_default(uid): return {"user_id":int(uid),"quality":"best","delivery":"auto","shorts":0,"reframe":"blur","subs":0,"sub_style":"classic","sub_lang":"auto","bot_lang":"en","watermark":0,"banned":0,"sub_cfg":"","allowed":0}

def get_user(uid: int) -> dict:
    uid=int(uid)
    if _mongo_ok:
        async def op():
            d=await _db.users.find_one({"user_id":uid})
            if not d:
                d=_user_default(uid); d["created_at"]=datetime.now(timezone.utc); await _db.users.update_one({"user_id":uid},{"$setOnInsert":d},upsert=True)
                d=await _db.users.find_one({"user_id":uid})
            d.pop("_id",None); return {**_user_default(uid),**d}
        return _run(op())
    c=_sdb(); c.row_factory=sqlite3.Row
    try:
        r=c.execute("SELECT * FROM users WHERE user_id=?",(uid,)).fetchone()
        if not r: c.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)",(uid,)); c.commit(); r=c.execute("SELECT * FROM users WHERE user_id=?",(uid,)).fetchone()
        return dict(r)
    finally: c.close()

def set_user(uid: int, **kw):
    bad=set(kw)-USER_COLUMNS
    if bad: raise ValueError(f"unknown user column(s): {bad}")
    if not kw: return
    if _mongo_ok:
        async def op(): await _db.users.update_one({"user_id":int(uid)},{"$set":{"user_id":int(uid),**kw}},upsert=True)
        return _run(op())
    get_user(uid); c=_sdb()
    try: c.execute("UPDATE users SET "+", ".join(f"{k}=?" for k in kw)+" WHERE user_id=?",list(kw.values())+[int(uid)]); c.commit()
    finally: c.close()

def add_history(uid,url,s,e,q,path) -> int:
    if _mongo_ok:
        async def op():
            latest=await _db.history.find_one({},sort=[("id",-1)],projection={"id":1})
            next_id=int((latest or {}).get("id",0))+1
            row={"id":next_id,"user_id":int(uid),"url":url,"start":s,"end":e,"quality":q,"file_path":path,"created_at":datetime.now(timezone.utc)}
            await _db.history.insert_one(row); return next_id
        return _run(op())
    c=_sdb()
    try:
        cur=c.execute('INSERT INTO history(user_id,url,start,"end",quality,file_path) VALUES(?,?,?,?,?,?)',(uid,url,s,e,q,path)); c.execute("INSERT INTO stats(user_id,clips,last_clip) VALUES(?,1,CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO UPDATE SET clips=clips+1,last_clip=CURRENT_TIMESTAMP",(uid,)); c.commit(); return cur.lastrowid
    finally:c.close()
def _hist(d):
    if not d:return None
    d=dict(d); d.pop("_id",None); return d
def get_history(uid,limit=20):
    if _mongo_ok:
        async def op(): return [_hist(x) for x in await _db.history.find({"user_id":int(uid)}).sort("created_at",-1).limit(int(limit)).to_list(length=int(limit))]
        return _run(op())
    c=_sdb(); c.row_factory=sqlite3.Row
    try:return [dict(r) for r in c.execute("SELECT * FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?",(uid,limit)).fetchall()]
    finally:c.close()
def get_stats(uid):
    rows=get_history(uid,100000)
    return {"clips":len(rows),"last_clip":rows[0].get("created_at") if rows else None}
def global_stats():
    if _mongo_ok:
        async def op():
            return {"users":await _db.users.count_documents({}),"banned":await _db.users.count_documents({"banned":1}),"clips":await _db.history.count_documents({})}
        return _run(op())
    c=_sdb()
    try:return {"users":c.execute("SELECT COUNT(*) FROM users").fetchone()[0],"banned":c.execute("SELECT COUNT(*) FROM users WHERE banned=1").fetchone()[0],"clips":c.execute("SELECT COALESCE(SUM(clips),0) FROM stats").fetchone()[0]}
    finally:c.close()
def all_user_ids():
    if _mongo_ok:
        async def op(): return [d["user_id"] async for d in _db.users.find({}, {"user_id":1})]
        return _run(op())
    c=_sdb()
    try:return [r[0] for r in c.execute("SELECT user_id FROM users")]
    finally:c.close()
def is_banned(uid):return bool(get_user(uid).get("banned"))
def ban_user(uid,b=True):set_user(uid,banned=int(b))
def is_allowed(uid):
    from config import ADMIN_IDS
    return int(uid) in ADMIN_IDS or bool(get_user(uid).get("allowed"))
def allow_user(uid,allowed=True):set_user(uid,allowed=int(allowed))
def cache_get(key):
    if _mongo_ok:
        async def op():
            d=await _db.cache_index.find_one({"key":key}); p=d and d.get("file_path"); return p if p and Path(p).exists() else None
        return _run(op())
    c=_sdb()
    try:r=c.execute("SELECT file_path FROM cache WHERE key=?",(key,)).fetchone(); return r[0] if r and Path(r[0]).exists() else None
    finally:c.close()
def cache_put(key,path):
    if _mongo_ok:
        async def op(): await _db.cache_index.update_one({"key":key},{"$set":{"key":key,"file_path":path,"created_at":datetime.now(timezone.utc)}},upsert=True)
        return _run(op())
    c=_sdb()
    try:c.execute("INSERT OR REPLACE INTO cache(key,file_path) VALUES(?,?)",(key,path));c.commit()
    finally:c.close()
def add_favorite(uid,hid):
    if _mongo_ok:
        async def op(): await _db.favorites.update_one({"user_id":int(uid),"history_id":hid},{"$setOnInsert":{"user_id":int(uid),"history_id":hid}},upsert=True)
        return _run(op())
    c=_sdb()
    try:c.execute("INSERT OR IGNORE INTO favorites VALUES(?,?)",(uid,hid));c.commit()
    finally:c.close()
def remove_favorite(uid,hid):
    if _mongo_ok:
        async def op(): await _db.favorites.delete_one({"user_id":int(uid),"history_id":hid})
        return _run(op())
    c=_sdb()
    try:c.execute("DELETE FROM favorites WHERE user_id=? AND history_id=?",(uid,hid));c.commit()
    finally:c.close()
def get_favorites(uid):
    if _mongo_ok:
        async def op():
            ids=[x["history_id"] async for x in _db.favorites.find({"user_id":int(uid)})]; return [_hist(x) async for x in _db.history.find({"id":{"$in":ids}}).sort("created_at",-1)] if ids else []
        return _run(op())
    c=_sdb(); c.row_factory=sqlite3.Row
    try:return [dict(r) for r in c.execute("SELECT h.* FROM history h JOIN favorites f ON h.id=f.history_id WHERE f.user_id=? ORDER BY h.id DESC",(uid,))]
    finally:c.close()
def is_favorite(uid,hid):
    if _mongo_ok:
        async def op(): return bool(await _db.favorites.find_one({"user_id":int(uid),"history_id":hid}))
        return _run(op())
    c=_sdb()
    try:return bool(c.execute("SELECT 1 FROM favorites WHERE user_id=? AND history_id=?",(uid,hid)).fetchone())
    finally:c.close()
def get_history_by_id(hid):
    if _mongo_ok:
        async def op():
            return _hist(await _db.history.find_one({"id":int(hid)}))
        return _run(op())
    c=_sdb(); c.row_factory=sqlite3.Row
    try:r=c.execute("SELECT * FROM history WHERE id=?",(hid,)).fetchone();return dict(r) if r else None
    finally:c.close()
def get_autopost(uid):
    defaults={"mode":"approval","platforms":[],"top_n":5,"clip_length":40,"weights":{"heatmap":.4,"chat":.3,"llm":.3},"hashtag_count":10,"post_mode":"queue"}
    if _mongo_ok:
        async def op():
            d=await _db.autopost_settings.find_one({"user_id":int(uid)}) or {}; d.pop("_id",None); return {**defaults,**d}
        return _run(op())
    c=_sdb()
    try:r=c.execute("SELECT settings FROM autopost_settings WHERE user_id=?",(uid,)).fetchone();return {**defaults,**(json.loads(r[0]) if r else {})}
    finally:c.close()
def set_autopost(uid,**kw):
    v={**get_autopost(uid),**kw}
    if _mongo_ok:
        async def op(): await _db.autopost_settings.update_one({"user_id":int(uid)},{"$set":{"user_id":int(uid),**v}},upsert=True)
        return _run(op())
    c=_sdb()
    try:c.execute("INSERT OR REPLACE INTO autopost_settings VALUES(?,?)",(uid,json.dumps(v)));c.commit()
    finally:c.close()
def save_job(job_id,uid,state,payload):
    if _mongo_ok:
        async def op(): await _db.jobs.update_one({"id":job_id},{"$set":{"id":job_id,"user_id":int(uid),"state":state,"payload":payload,"updated_at":datetime.now(timezone.utc)}},upsert=True)
        return _run(op())
    c=_sdb()
    try:c.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,CURRENT_TIMESTAMP)",(job_id,uid,state,json.dumps(payload)));c.commit()
    finally:c.close()
def get_jobs():
    if _mongo_ok:
        async def op(): return [_hist(x) for x in await _db.jobs.find({"state":{"$in":["pending","running","awaiting_approval","ready","publish_failed"]}}).to_list(length=200)]
        return _run(op())
    c=_sdb(); c.row_factory=sqlite3.Row
    try:
        out=[]
        for r in c.execute("SELECT id,user_id,state,payload,updated_at FROM jobs WHERE state IN ('pending','running','awaiting_approval','ready','publish_failed')").fetchall():
            x=dict(r); x["payload"]=json.loads(x.get("payload") or "{}"); out.append(x)
        return out
    finally:c.close()
