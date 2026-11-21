"""Chat replay intensity scanner. Uses chat-downloader when available."""
import asyncio, logging, re
from collections import defaultdict
log=logging.getLogger(__name__)

async def chat_spikes(url: str, bin_seconds: int = 10) -> list[dict]:
    """Return normalized [{start, end, score, reason}] chat activity bins; [] when unavailable."""
    try:
        def collect():
            from chat_downloader import ChatDownloader
            out=defaultdict(float)
            downloader=ChatDownloader()
            chat=downloader.get_chat(url, message_types=["text_message","paid_message","membership_item"])
            for m in chat:
                t=float(m.get("time_in_seconds",0) or 0); msg=str(m.get("message", ""))
                emotes=m.get("emotes") or []
                w=1+min(3,len(emotes)*.5)
                if re.search(r"\b(lol|lmao|haha+|rofl|😂|🤣|💀|KEKW|OMEGALUL)\b",msg,re.I): w+=1.5
                if re.search(r"\b(clip it|clip that|clip this|clipp? it)\b",msg,re.I): w+=4
                out[int(t//bin_seconds)*bin_seconds]+=w
            return out
        bins=await asyncio.to_thread(collect)
        if len(bins)<3:return []
        vals=list(bins.values()); avg=sum(vals)/len(vals); peak=max(vals) or 1
        result=[]
        for t,v in bins.items():
            normalized=max(0,min(1,v/peak))
            if v>=max(avg*1.7,vals[int(len(vals)*.8)] if len(vals)>5 else avg*1.5):
                result.append({"start":t,"end":t+bin_seconds,"score":normalized,"reason":"Chat spike"})
        return result
    except Exception as e:
        log.info("Chat replay unavailable (%s)",type(e).__name__); return []
