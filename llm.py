import json
from openai import AsyncOpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

def _c():
    return AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL) if LLM_API_KEY else None

async def find_highlights(text: str) -> list[dict]:
    c = _c()
    if not c: return []
    prompt = f"""You are a viral clip detector. Pick top 5 moments.
Return JSON only: [{{"start":12.3,"end":45.6,"score":9,"reason":"..."}}]
Transcript: {text[:12000]}"""
    try:
        r = await c.chat.completions.create(model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}], temperature=0.3)
        raw = r.choices[0].message.content or "[]"
        return json.loads(raw[raw.find("["):raw.rfind("]") + 1])
    except Exception: return []

async def generate_metadata(text: str, lang="en") -> dict:
    c = _c()
    if not c: return {}
    prompt = f"""Generate Shorts metadata in {lang}. JSON: {{"title":"...","description":"...","hashtags":["#..."]}}"""
    try:
        r = await c.chat.completions.create(model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt + "\n" + text[:4000]}], temperature=0.7)
        raw = r.choices[0].message.content or "{}"
        return json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except Exception: return {}