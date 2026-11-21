"""ShapelessAI publishing API client, following the published OpenAPI routes."""
import asyncio, logging, time, uuid
from pathlib import Path
from urllib.parse import quote
import aiohttp
from config import SHAPELESSAI_API_KEY
log=logging.getLogger(__name__)
_BASE="https://shapelessai.com/api"
_calls=[]; _lock=asyncio.Lock()

async def _rate_limit():
    async with _lock:
        now=time.monotonic(); _calls[:]=[x for x in _calls if now-x<3600]
        if len(_calls)>=600: raise RuntimeError("ShapelessAI rate limit reached (600 requests/hour). Try later.")
        _calls.append(now)

async def _request(method,path,**kwargs):
    if not SHAPELESSAI_API_KEY: raise RuntimeError("SHAPELESSAI_API_KEY set nahi hai.")
    await _rate_limit()
    headers={"Authorization":f"Bearer {SHAPELESSAI_API_KEY}",**kwargs.pop("headers",{})}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
            async with s.request(method,_BASE+path,headers=headers,**kwargs) as r:
                try:data=await r.json(content_type=None)
                except Exception:data={"error":(await r.text())[:300]}
                if r.status>=400: raise RuntimeError(f"ShapelessAI HTTP {r.status}: {data.get('error') or data.get('message') or 'request failed'}")
                return data
    except RuntimeError:raise
    except Exception as e: raise RuntimeError(f"ShapelessAI request failed: {type(e).__name__}") from None

async def get_connections():
    try:
        data=await _request("GET","/connections")
        return data.get("connections",[])
    except Exception as e:
        log.warning("Connections lookup failed (%s)",type(e).__name__);raise

async def upload_video(path):
    """Upload local clip through documented PUT /api/studio/attachments/{filename}."""
    try:
        path=Path(path)
        async def chunks():
            f=path.open("rb")
            try:
                while True:
                    block=await asyncio.to_thread(f.read,1024*1024)
                    if not block:break
                    yield block
            finally:f.close()
        data=await _request("PUT",f"/studio/attachments/{quote(path.name)}",data=chunks(),headers={"Content-Type":"video/mp4"})
        key=data.get("mediaKey") or data.get("media_key") or data.get("key")
        if not key:raise RuntimeError("Upload response me mediaKey nahi mila.")
        return key
    except Exception as e:
        log.warning("Video upload failed (%s)",type(e).__name__);raise

async def publish_video(path, title, description="", hashtags=None, platforms=None, mode="queue", scheduled_at=None, idempotency_key=None):
    """Create one post per selected connection; API enforces actual plan cap; retries are idempotent."""
    try:
        tags=" ".join(hashtags or [])
        text=(description.strip()+("\n\n" if description.strip() and tags else "")+tags).strip()
        if not platforms: raise RuntimeError("Auto Post settings me kam se kam ek platform select karo.")
        connections=await get_connections()
        selected=[c for c in connections if c.get("status","active")=="active" and c.get("platform") in platforms]
        if not selected:raise RuntimeError("Selected platform ka active connected account nahi mila.")
        capdata=await _request("GET","/posts/cap",params={"at":scheduled_at} if mode=="schedule" and scheduled_at else {})
        cap=capdata.get("cap")
        if cap and int(cap.get("limit",10))-int(cap.get("used",0))<len(selected):
            raise RuntimeError(f"ShapelessAI Free plan me {cap.get('limit',10)} posts/day limit hai; abhi {cap.get('used',0)} use ho chuke. Selected accounts ke liye slots kam hain.")
        key=await upload_video(path); results=[]
        for c in selected:
            body={"connectionId":c["id"],"postText":text,"documentTitle":title,"mediaKeys":[key]}
            if mode=="queue":body["queue"]=True
            elif mode=="schedule":
                if not scheduled_at:raise RuntimeError("Schedule time missing hai.")
                body["scheduledAt"]=scheduled_at
            data=await _request("POST","/posts",json=body,headers={"Idempotency-Key":f"{idempotency_key or uuid.uuid4()}-{c['id']}"})
            if data.get("status") in ("failed","handoff") or data.get("error"):raise RuntimeError(data.get("error") or data.get("handoffNote") or "Post publish nahi hua.")
            results.append({"connection":c,"post":data})
        return results
    except Exception as e:
        log.warning("Publishing failed (%s)",type(e).__name__);raise
