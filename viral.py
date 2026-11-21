"""Combine YouTube replay heatmap, live chat spikes, and transcript LLM scores."""
import asyncio, logging, os, re
import aiohttp
from chat_spikes import chat_spikes
from downloader import fetch_info, download_audio
from transcriber import transcribe_long
from config import TMP_DIR, YOUTUBE_API_KEY
import llm
log=logging.getLogger(__name__)

async def _video_api(video_id):
    if not YOUTUBE_API_KEY:return None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.get("https://www.googleapis.com/youtube/v3/videos",params={"part":"snippet,contentDetails,liveStreamingDetails","id":video_id,"key":YOUTUBE_API_KEY}) as r:
                if r.status!=200:return None
                data=await r.json(); return (data.get("items") or [None])[0]
    except Exception as e:
        log.warning("YouTube API lookup failed (%s)",type(e).__name__);return None

def _duration_iso(value):
    m=re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",value or "")
    return int(m.group(1) or 0)*3600+int(m.group(2) or 0)*60+int(m.group(3) or 0) if m else 0

def _normalize(items):
    if not items:return []
    vals=[max(0,float(x.get("value",x.get("score",0)) or 0)) for x in items]; lo=min(vals); hi=max(vals)
    return [((v-lo)/(hi-lo) if hi>lo else (1.0 if v else 0.0)) for v in vals]

def _heatmap(info):
    result=[]
    for x in info.get("heatmap") or []:
        try:
            start=float(x.get("start_time",x.get("start",0))); end=float(x.get("end_time",x.get("end",start+10))); val=float(x.get("value",0))
            if end>start:result.append({"start":start,"end":end,"value":val,"reason":"Most replayed heatmap"})
        except (TypeError,ValueError):continue
    return result

def _candidate_windows(signals,duration,clip_len,step=10):
    bins=[]
    for start in range(0,max(1,int(duration)),step):
        end=min(duration,start+step); bins.append({"start":start,"end":end,"heat":0.0,"chat":0.0,"llm":0.0,"reasons":[]})
    for key,items in signals.items():
        if not items:continue
        norms=_normalize(items)
        for x,n in zip(items,norms):
            mid=(float(x["start"])+float(x["end"]))/2
            i=min(len(bins)-1,max(0,int(mid//step)))
            bins[i][key]=max(bins[i][key],n)
            if n>=.65 and x.get("reason"):bins[i]["reasons"].append(x["reason"])
    windows=[]
    size=max(1,round(clip_len/step))
    for i in range(len(bins)):
        j=min(len(bins),i+size)
        if j-i<size and duration-(i*step)>=20:continue
        window=bins[i:j]
        if not window:continue
        windows.append({"start":float(i*step),"end":float(min(duration,i*step+clip_len)),
                        "heat":max(x["heat"] for x in window),"chat":max(x["chat"] for x in window),"llm":max(x["llm"] for x in window),
                        "reasons":list(dict.fromkeys(y for x in window for y in x["reasons"]))})
    return windows

async def top_clips(url: str, top_n: int=5, clip_length: int=40, weights=None) -> list[dict]:
    """Analyze a finished YouTube VOD; raises RuntimeError for live streams."""
    try:
        info=await fetch_info(url); vid=info.get("id")
        api=await _video_api(vid) if vid else None
        details=(api or {}).get("liveStreamingDetails",{})
        live_now=bool((api or {}).get("snippet",{}).get("liveBroadcastContent")=="live") or bool(details.get("activeLiveChatId")) or (bool(details.get("actualStartTime")) and not details.get("actualEndTime")) or bool(info.get("is_live"))
        if live_now:raise RuntimeError("Ye stream abhi live hai—try again after the stream ends.")
        duration=_duration_iso(((api or {}).get("contentDetails") or {}).get("duration")) or float(info.get("duration") or 0)
        if duration<=0:raise RuntimeError("Video ki duration nahi mil paayi.")
        if duration<20:raise RuntimeError("Video 20 second se chhoti hai, clip nahi ban sakta.")
        clip_length=max(20,min(60,int(clip_length))); clip_length=min(clip_length,int(duration))
        signals={"heat":_heatmap(info),"chat":await chat_spikes(url)}
        llm_items=[]
        try:
            out=TMP_DIR/"viral";out.mkdir(parents=True,exist_ok=True)
            audio=await download_audio(url,out); transcript=await transcribe_long(audio)
            llm_items=await llm.score_virality(transcript.get("segments",[]),duration)
        except Exception as e: log.warning("Virality transcript unavailable (%s)",type(e).__name__)
        signals["llm"]=llm_items
        w=weights or {"heatmap":.4,"chat":.3,"llm":.3}
        present={"heat":signals["heat"],"chat":signals["chat"],"llm":signals["llm"]}
        active={k:max(0,float(w.get({"heat":"heatmap","chat":"chat","llm":"llm"}[k],0))) for k,v in present.items() if v}
        total=sum(active.values())
        if not total:raise RuntimeError("Is video ke liye heatmap, chat ya transcript signal available nahi hua.")
        active={k:v/total for k,v in active.items()}
        windows=_candidate_windows(present,duration,clip_length)
        ranked=[]
        for x in windows:
            score=sum(active.get(k,0)*x[k] for k in active)
            reasons=x["reasons"] or ["Multiple engagement signals"]
            ranked.append({"start":round(x["start"],2),"end":round(x["end"],2),"score":round(score,3),"reason":", ".join(reasons[:3])})
        ranked.sort(key=lambda x:x["score"],reverse=True);picked=[]
        for item in ranked:
            if all(item["end"]<=y["start"] or item["start"]>=y["end"] for y in picked):picked.append(item)
            if len(picked)>=max(1,min(10,int(top_n))):break
        return picked
    except Exception:
        raise
