"""One-time migration of data/bot.db into the configured MongoDB database."""
import asyncio, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
import config

def rows(conn,table):
    try:return [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    except sqlite3.Error:return []

async def migrate():
    if not config.MONGODB_URI: raise SystemExit("MONGODB_URI is required")
    if not Path(config.DB_PATH).exists(): raise SystemExit("data/bot.db not found")
    client=AsyncIOMotorClient(config.MONGODB_URI,serverSelectionTimeoutMS=5000)
    try:
        await client.admin.command("ping"); db=client[config.MONGODB_DB]
        await db.users.create_index("user_id",unique=True)
        await db.history.create_index([("user_id",1),("created_at",-1)])
        await db.history.create_index("id",unique=True)
        c=sqlite3.connect(config.DB_PATH);c.row_factory=sqlite3.Row
        try:
            users=rows(c,"users")
            for u in users:
                u["user_id"]=int(u["user_id"]);u.setdefault("allowed",0)
                await db.users.update_one({"user_id":u["user_id"]},{"$setOnInsert":u},upsert=True)
            history=rows(c,"history")
            for h in history:
                h["id"]=int(h["id"]);h["user_id"]=int(h["user_id"])
                await db.history.update_one({"id":h["id"]},{"$setOnInsert":h},upsert=True)
            for f in rows(c,"favorites"):
                await db.favorites.update_one({"user_id":int(f["user_id"]),"history_id":int(f["history_id"])},{"$setOnInsert":{"user_id":int(f["user_id"]),"history_id":int(f["history_id"])}},upsert=True)
            for x in rows(c,"cache"):
                await db.cache_index.update_one({"key":x["key"]},{"$setOnInsert":x},upsert=True)
            for settings in rows(c,"autopost_settings"):
                await db.autopost_settings.update_one({"user_id":int(settings["user_id"])},{"$setOnInsert":{"user_id":int(settings["user_id"]),**__import__("json").loads(settings["settings"] or "{}")}},upsert=True)
            print(f"Migrated/checked {len(users)} users, {len(history)} history rows.")
        finally:c.close()
    finally:client.close()

if __name__=="__main__":asyncio.run(migrate())
