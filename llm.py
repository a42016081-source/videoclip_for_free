"""LLM helpers (OpenAI-compatible API: Groq / NVIDIA NIM / OpenAI)."""
import json
import logging
from openai import AsyncOpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
log = logging.getLogger(__name__)


def available() -> bool:
    return bool(LLM_API_KEY)


def _c():
    return AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=90) if LLM_API_KEY else None


def _json_slice(raw: str, open_c: str, close_c: str):
    a, b = raw.find(open_c), raw.rfind(close_c)
    if a < 0 or b < a:
        return None
    try:
        return json.loads(raw[a:b + 1])
    except ValueError:
        return None


def _chunk_lines(segments, limit=9000, max_chunks=8):
    lines = [f"[{int(s['start'])}] {s['text'].strip()}" for s in segments if s.get("text", "").strip()]
    chunks, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > limit:
            chunks.append(cur)
            cur = ""
        cur += ln + "\n"
    if cur:
        chunks.append(cur)
    if len(chunks) > max_chunks:                       # sample evenly across the video
        step = len(chunks) / max_chunks
        chunks = [chunks[int(i * step)] for i in range(max_chunks)]
    return chunks


def _valid(m, duration):
    try:
        s, e = float(m["start"]), float(m["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if duration:
        s, e = min(max(s, 0), duration - 1), min(e, duration)
    if e - s < 8:
        e = min(s + 20, duration) if duration else s + 20
    if e <= s:
        return None
    try:
        score = float(m.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    return {"start": s, "end": e, "score": round(score), "reason": str(m.get("reason", ""))[:140]}


async def find_highlights(segments: list[dict], duration: float = 0) -> list[dict]:
    """Top-5 viral moments. `segments` = [{start,end,text}] so the LLM sees real timestamps."""
    c = _c()
    if not c:
        return []
    found = []
    for chunk in _chunk_lines(segments):
        prompt = ("You are a viral short-form clip detector. Each transcript line starts with [seconds]. "
                  "Pick up to 3 of the best self-contained moments (15-60 seconds long) with a strong hook. "
                  "Use ONLY timestamps that appear in the transcript. Return JSON only: "
                  '[{"start":123,"end":170,"score":9,"reason":"one line why"}]\n\nTranscript:\n' + chunk)
        try:
            r = await c.chat.completions.create(model=LLM_MODEL, temperature=0.3,
                                                messages=[{"role": "user", "content": prompt}])
            data = _json_slice(r.choices[0].message.content or "", "[", "]") or []
        except Exception as e:
            log.warning("highlights LLM: %s", e)
            continue
        for m in data if isinstance(data, list) else []:
            v = _valid(m, duration) if isinstance(m, dict) else None
            if v:
                found.append(v)
    found.sort(key=lambda x: x["score"], reverse=True)
    top = []
    for m in found:                                    # drop overlaps
        if all(m["end"] <= t["start"] or m["start"] >= t["end"] for t in top):
            top.append(m)
        if len(top) == 5:
            break
    return top


async def generate_metadata(text: str, lang="en") -> dict:
    c = _c()
    if not c:
        return {}
    language = "Hinglish (Hindi written in Roman script)" if lang == "hinglish" else "English"
    prompt = (f"Write YouTube Shorts metadata in {language} for a clip with this transcript. "
              'Return JSON only: {"title":"catchy, <70 chars","description":"2 short lines",'
              '"hashtags":["#tag1", "... exactly 10 hashtags"]}\n\nTranscript:\n' + text[:4000])
    try:
        r = await c.chat.completions.create(model=LLM_MODEL, temperature=0.7,
                                            messages=[{"role": "user", "content": prompt}])
        d = _json_slice(r.choices[0].message.content or "", "{", "}")
        return d if isinstance(d, dict) else {}
    except Exception as e:
        log.warning("metadata LLM: %s", e)
        return {}
