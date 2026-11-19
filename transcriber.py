import asyncio, logging, subprocess
from pathlib import Path
import httpx
from config import GROQ_API_KEY
log = logging.getLogger(__name__)

async def _extract_audio(video: Path) -> Path:
    wav = video.with_suffix(".wav")
    cmd = ["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
           "-acodec", "pcm_s16le", str(wav)]
    await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=600)
    return wav

async def _groq(wav: Path) -> dict:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    data = {"model": "whisper-large-v3-turbo", "response_format": "verbose_json",
            "timestamp_granularities[]": ["word", "segment"]}
    async with httpx.AsyncClient(timeout=600) as c:
        with open(wav, "rb") as f:
            r = await c.post("https://api.groq.com/openai/v1/audio/transcriptions",
                             headers=headers, data=data,
                             files={"file": (wav.name, f, "audio/wav")})
    r.raise_for_status()
    return r.json()

def _fallback(wav: Path) -> dict:
    from faster_whisper import WhisperModel
    m = WhisperModel("base", device="cpu", compute_type="int8")
    segs, info = m.transcribe(str(wav), word_timestamps=True)
    words, segments = [], []
    for s in segs:
        segments.append({"start": s.start, "end": s.end, "text": s.text})
        for w in s.words or []:
            words.append({"word": w.word, "start": w.start, "end": w.end})
    return {"language": info.language, "segments": segments, "words": words}

async def transcribe(video: Path) -> dict:
    wav = await _extract_audio(video)
    if GROQ_API_KEY:
        try: return await _groq(wav)
        except Exception as e: log.warning("Groq failed: %s", e)
    return await asyncio.to_thread(_fallback, wav)