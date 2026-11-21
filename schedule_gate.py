"""Throttle frequent GitHub cron ticks to one bot start every 350 minutes using MongoDB."""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

async def main():
    uri=os.getenv("MONGODB_URI")
    allowed=False
    if uri:
        client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=5000)
        try:
            await client.admin.command("ping")
            coll=client[os.getenv("MONGODB_DB","clipbot")].workflow_state
            now=datetime.now(timezone.utc); now=now.replace(microsecond=(now.microsecond//1000)*1000); cutoff=now-timedelta(minutes=350)
            d=await coll.find_one_and_update({"_id":"github-actions-bot","$or":[{"last_started":{"$lte":cutoff}},{"last_started":{"$exists":False}}]},
              {"$set":{"last_started":now}},upsert=True,return_document=ReturnDocument.AFTER)
            saved=(d or {}).get("last_started")
            if saved and saved.tzinfo is None:saved=saved.replace(tzinfo=timezone.utc)
            allowed=bool(saved and abs((saved-now).total_seconds())<1)
        finally: client.close()
    with open(os.environ.get("GITHUB_ENV","/dev/null"),"a",encoding="utf-8") as f:
        f.write("RUN_BOT="+("true" if allowed else "false")+"\n")
    print("Scheduled bot slot claimed." if allowed else "No scheduled bot slot is due.")

if __name__=="__main__":
    try: asyncio.run(main())
    except Exception as exc:
        # Never print configuration or connection details.
        print(f"Schedule gate unavailable ({type(exc).__name__}); scheduled bot start skipped.")
        try:
            with open(os.environ.get("GITHUB_ENV","/dev/null"),"a",encoding="utf-8") as f:f.write("RUN_BOT=false\n")
        except Exception:pass
