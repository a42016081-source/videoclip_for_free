"""Automatic viral-clip pipeline and approval callbacks."""
import asyncio, json, logging, re, shutil, uuid
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from config import TMP_DIR, ADMIN_IDS
from database import get_autopost, get_user, save_job, get_history
from downloader import download_section
from editor import render
from transcriber import transcribe
from subtitles import build_ass, STYLE_NAMES
from viral import top_clips
from queue_manager import job_slot
from utils import fmt_time
import llm
log=logging.getLogger(__name__)
_PENDING={}

def _buttons(job_id):
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Post",callback_data=f"auto:post:{job_id}"),InlineKeyboardButton("✏️ Edit title",callback_data=f"auto:edit:{job_id}"),InlineKeyboardButton("❌ Skip",callback_data=f"auto:skip:{job_id}")]])

async def run_auto(update,ctx,url,parent_id=None,completed=None,settings=None):
    uid=update.effective_user.id; chat=update.effective_chat.id; cfg=settings or get_autopost(uid)
    parent_id=parent_id or uuid.uuid4().hex; completed=set(completed or [])
    parent_payload={"kind":"pipeline","uid":uid,"chat":chat,"url":url,"completed":sorted(completed),"settings":cfg}
    save_job(parent_id,uid,"running",parent_payload)
    progress=await update.effective_message.reply_text("🤖 Auto pipeline shuru… viral moments dhoondh raha hoon.")
    async def queued(pos): await progress.edit_text(f"⏳ Queue me position {pos}…")
    try:
        async with job_slot(queued):
            moments=await top_clips(url,cfg.get("top_n",5),cfg.get("clip_length",40),cfg.get("weights"))
            if not moments: return await progress.edit_text("❌ Koi suitable clip nahi mila.")
            await progress.edit_text(f"🎯 {len(moments)} clips mile. Ab cut + subtitles bana raha hoon…")
            for i,m in enumerate(moments,1):
                if i in completed: continue
                await progress.edit_text(f"✂️ Clip {i}/{len(moments)} cut + subtitles process ho raha hai…")
                job_id=f"{parent_id[:8]}{i:06d}"; d=TMP_DIR/"auto"/job_id; d.mkdir(parents=True,exist_ok=True)
                try:
                    src=await download_section(url,m["start"],m["end"],"best",d)
                    transcript=await transcribe(src,get_user(uid).get("sub_lang","auto"))
                    segs=transcript.get("segments",[])
                    words=[]
                    for s in segs:
                        for w in s.get("words",[]) or []:
                            if isinstance(w,dict): words.append(w)
                    ass=None
                    if words:
                        ass=d/"subs.ass"; ass.write_text(build_ass(words,get_user(uid).get("sub_style","classic"),vertical=True),encoding="utf-8")
                    out=d/"short.mp4"
                    await render(src,out,m["end"]-m["start"],reframe=get_user(uid).get("reframe","blur"),ass=str(ass) if ass else None)
                    meta=await llm.generate_metadata(transcript.get("text","") or m.get("reason","viral moment"),get_user(uid).get("bot_lang","hinglish"))
                    title=str(meta.get("title") or f"Viral Moment #{i}")[:90]
                    hashtags=(meta.get("hashtags") or [])[:int(cfg.get("hashtag_count",10))]
                    item={"uid":uid,"chat":chat,"path":str(out),"title":title,"description":str(meta.get("description") or "Dekho ye viral moment!"),"hashtags":hashtags,"platforms":cfg.get("platforms",[]),"mode":("now" if cfg.get("post_mode")=="now" else "schedule" if cfg.get("post_mode")=="schedule" else "queue"),"url":url,"start":m["start"],"end":m["end"],"score":m["score"],"state":"awaiting_approval" if cfg.get("mode","approval")=="approval" else "ready"}
                    _PENDING[job_id]=item; save_job(job_id,uid,item["state"],item)
                    caption=f"🎬 {title}\n\n{item['description']}\n{' '.join(hashtags)}\n\n⭐ Viral score: {m['score']:.2f} · {fmt_time(m['start'])}–{fmt_time(m['end'])}"
                    markup=_buttons(job_id) if item["state"]=="awaiting_approval" else None
                    with open(out,"rb") as f: await ctx.bot.send_video(chat_id,video=InputFile(f,filename="short.mp4"),caption=caption[:1024],supports_streaming=True,reply_markup=markup,write_timeout=300)
                    if item["state"]=="ready": await _post(job_id,ctx)
                    completed.add(i); parent_payload["completed"]=sorted(completed); save_job(parent_id,uid,"running",parent_payload)
                except Exception as e:
                    log.warning("Auto clip failed (%s)",type(e).__name__)
                    await ctx.bot.send_message(chat,f"⚠️ Clip {i} fail ho gaya: {str(e)[:250]}")
                finally:
                    # Keep awaiting-approval local clips until the decision is made.
                    if job_id not in _PENDING: shutil.rmtree(d,ignore_errors=True)
            save_job(parent_id,uid,"complete",parent_payload)
            await progress.edit_text("✅ Auto pipeline complete. Approval wale clips par Post/Edit/Skip choose karo.")
    except Exception as e:
        save_job(parent_id,uid,"failed",parent_payload)
        await progress.edit_text(f"❌ Auto pipeline fail: {str(e)[:350]}")

async def _ensure_clip(job_id,item):
    try:
        path=Path(item.get("path",""))
        if path.is_file():return path
        d=TMP_DIR/"auto"/str(job_id);d.mkdir(parents=True,exist_ok=True)
        src=await download_section(item["url"],item["start"],item["end"],"best",d)
        tr=await transcribe(src,get_user(item["uid"]).get("sub_lang","auto")); words=tr.get("words",[])
        ass=None
        if words:
            ass=d/"subs.ass";ass.write_text(build_ass(words,get_user(item["uid"]).get("sub_style","classic"),vertical=True),encoding="utf-8")
        path=d/"short.mp4";await render(src,path,item["end"]-item["start"],reframe=get_user(item["uid"]).get("reframe","blur"),ass=str(ass) if ass else None)
        item["path"]=str(path);save_job(job_id,item["uid"],item.get("state","awaiting_approval"),item);return path
    except Exception as e:
        log.warning("Clip rebuild failed (%s)",type(e).__name__);raise RuntimeError("Clip dobara render nahi ho paaya. /auto dobara try karo.") from None

async def _post(job_id,ctx):
    from publisher import publish_video
    item=_PENDING.get(job_id)
    if not item: raise RuntimeError("Clip file state expire ho gaya; /auto dobara chalao.")
    try:
        await _ensure_clip(job_id,item)
        if item.get("mode")=="schedule" and not item.get("scheduled_at"):
            from handlers import STATE
            STATE[item["uid"]]={"step":"await_auto_schedule","auto_job_id":job_id}
            await ctx.bot.send_message(item["chat"],"🗓️ Schedule time ISO-8601 me bhejo, example: 2026-12-01T18:30:00+05:30")
            return
        result=await publish_video(item["path"],item["title"],item["description"],item["hashtags"],item["platforms"],item["mode"],item.get("scheduled_at"),idempotency_key=job_id)
        item["state"]="published";save_job(job_id,item["uid"],"published",item)
        await ctx.bot.send_message(item["chat"],f"✅ Posted/queued: {item['title']} · {len(result)} account(s).")
        _PENDING.pop(job_id,None);shutil.rmtree(Path(item["path"]).parent,ignore_errors=True)
    except Exception as e:
        item["state"]="publish_failed";save_job(job_id,item["uid"],item["state"],item)
        await ctx.bot.send_message(item["chat"],f"❌ Post nahi hua: {str(e)[:500]}\nClip Telegram me upar available hai. Dobara try karne ke liye button dabao.",reply_markup=_buttons(job_id))

async def handle_auto_callback(update,ctx):
    q=update.callback_query; uid=update.effective_user.id; _,action,job_id=(q.data or "").split(":",2)
    item=_PENDING.get(job_id)
    if not item or item["uid"]!=uid:
        await q.answer("Clip request nahi mila.",show_alert=True);return
    if action=="skip":
        save_job(job_id,uid,"skipped",item);_PENDING.pop(job_id,None);shutil.rmtree(Path(item["path"]).parent,ignore_errors=True);await q.answer("Skipped");await q.edit_message_reply_markup(None);return
    if action=="edit":
        from handlers import STATE
        STATE[uid]={"step":"await_auto_title","auto_job_id":job_id}
        await q.answer();await q.message.reply_text("✏️ Naya title bhejo:");return
    if action=="post":
        if item.get("mode")=="schedule":
            from handlers import STATE
            STATE[uid]={"step":"await_auto_schedule","auto_job_id":job_id}
            await q.answer(); await q.message.reply_text("🗓️ Schedule time ISO-8601 me bhejo, example: 2026-12-01T18:30:00+05:30"); return
        await q.answer("Posting…");await q.edit_message_reply_markup(None)
        try:
            async with job_slot(): await _post(job_id,ctx)
        except Exception as e: await ctx.bot.send_message(item["chat"],f"❌ Post error: {str(e)[:350]}")

async def edit_title(uid,title,ctx):
    from handlers import STATE
    st=STATE.pop(uid,{})
    job_id=st.get("auto_job_id"); item=_PENDING.get(job_id)
    if not item:return False
    item["title"]=title[:90];save_job(job_id,uid,item["state"],item)
    await ctx.bot.send_message(item["chat"],f"✅ Title update hua: {item['title']}",reply_markup=_buttons(job_id))
    return True


async def set_schedule(uid,job_id,scheduled_at,ctx):
    item=_PENDING.get(job_id)
    if not item or item["uid"]!=uid: return False
    item["scheduled_at"]=scheduled_at
    from publisher import publish_video
    try:
        await _ensure_clip(job_id,item)
        async with job_slot():
            result=await publish_video(item["path"],item["title"],item["description"],item["hashtags"],item["platforms"],"schedule",scheduled_at,idempotency_key=job_id)
        save_job(job_id,uid,"scheduled",item)
        await ctx.bot.send_message(item["chat"],f"✅ Scheduled: {item['title']} · {len(result)} account(s).")
        _PENDING.pop(job_id,None);shutil.rmtree(Path(item["path"]).parent,ignore_errors=True);return True
    except Exception as e:
        await ctx.bot.send_message(item["chat"],f"❌ Schedule nahi hua: {str(e)[:450]}\nClip Telegram me available hai. Dobara schedule ke liye button dabao.",reply_markup=_buttons(job_id));return False


async def resume_pending(app):
    """Rebuild clips interrupted by a runner restart and restore approval controls."""
    try:
        from database import get_jobs
        jobs=get_jobs(); jobs.sort(key=lambda r:0 if (r.get("payload") or {}).get("kind")=="pipeline" else 1)
        for row in jobs:
            payload=row.get("payload") or {}
            if payload.get("kind")=="pipeline":
                class User: id=payload.get("uid")
                class Chat: id=payload.get("chat")
                class Message:
                    async def reply_text(self,text,**kwargs):return await app.bot.send_message(payload["chat"],text,**kwargs)
                class Update:
                    effective_user=User(); effective_chat=Chat(); effective_message=Message()
                class Context: bot=app.bot
                await run_auto(Update(),Context(),payload["url"],parent_id=row["id"],completed=payload.get("completed",[]),settings=payload.get("settings"))
                continue
            async with job_slot():
                await _resume_one(app,row)
    except Exception as e:log.warning("Pending job scan failed (%s)",type(e).__name__)

async def _resume_one(app,row):
    try:
        from telegram import InputFile
        payload=row.get("payload") or {}; jid=row.get("id")
        if not jid or not payload or not payload.get("url"):return
        path=Path(payload.get("path",""))
        if not path.is_file():
            d=TMP_DIR/"auto"/str(jid);d.mkdir(parents=True,exist_ok=True)
            src=await download_section(payload["url"],payload["start"],payload["end"],"best",d)
            tr=await transcribe(src,get_user(payload["uid"]).get("sub_lang","auto")); words=tr.get("words",[])
            ass=None
            if words:
                ass=d/"subs.ass";ass.write_text(build_ass(words,get_user(payload["uid"]).get("sub_style","classic"),vertical=True),encoding="utf-8")
            path=d/"short.mp4";await render(src,path,payload["end"]-payload["start"],reframe=get_user(payload["uid"]).get("reframe","blur"),ass=str(ass) if ass else None)
            payload["path"]=str(path);save_job(jid,payload["uid"],payload.get("state","awaiting_approval"),payload)
        _PENDING[jid]=payload
        if payload.get("state") in ("awaiting_approval","publish_failed"):
            caption=f"🔄 Restart ke baad clip restore hua\n{payload['title']}\n{' '.join(payload.get('hashtags',[]))}"
            with open(path,"rb") as f: await app.bot.send_video(payload["chat"],InputFile(f,filename="short.mp4"),caption=caption[:1024],reply_markup=_buttons(jid),supports_streaming=True)
        else:await _post(jid,app)
    except Exception as e:
        log.warning("Pending clip resume failed (%s)",type(e).__name__)
        try:await app.bot.send_message((row.get("payload") or {}).get("chat"),f"⚠️ Restart ke baad pending clip resume nahi hua: {str(e)[:200]}")
        except Exception:pass
