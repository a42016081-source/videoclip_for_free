"""Speech-to-text: Groq Whisper (primary) -> faster-whisper on CPU (fallback)."""
import asyncio
import logging
import subprocess
from pathlib import Path
import httpx
from config import GROQ_API_KEY
log = logging.getLogger(__name__)

CHUNK_S = 600                     # long videos are transcribed in 10-minute pieces (Groq 25 MB limit)
_MODEL = None


async def _to_mp3(src: Path, dst: Path, extra=()):
    """Mono 16 kHz low-bitrate mp3: tiny file, ideal for Whisper (<= 25 MB for ~100 min)."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
           "-c:a", "libmp3lame", "-b:a", "32k", *extra, str(dst)]
    r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError("Audio extract failed: " + r.stderr[-300:])


async def _groq(audio: Path, language) -> dict:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    data = {"model": "whisper-large-v3-turbo", "response_format": "verbose_json",
            "timestamp_granularities[]": ["word", "segment"]}
    if language and language != "auto":
        data["language"] = language
    last = None
    for attempt in range(3):
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30)) as c:
            with open(audio, "rb") as f:
                r = await c.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                 headers=headers, data=data, files={"file": (audio.name, f, "audio/mpeg")})
        if r.status_code in (429, 500, 502, 503):
            last = f"HTTP {r.status_code}"
            await asyncio.sleep(3 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Groq busy ({last})")


def _fallback(audio: Path, language) -> dict:
    global _MODEL
    from faster_whisper import WhisperModel
    if _MODEL is None:
        _MODEL = WhisperModel("base", device="cpu", compute_type="int8")
    segs, info = _MODEL.transcribe(str(audio), word_timestamps=True,
                                   language=None if language in (None, "auto") else language)
    words, segments = [], []
    for s in segs:
        segments.append({"start": s.start, "end": s.end, "text": s.text})
        for w in s.words or []:
            words.append({"word": w.word, "start": w.start, "end": w.end})
    return {"language": info.language, "segments": segments, "words": words}


def _norm(res: dict) -> dict:
    words = [{"word": str(w.get("word", "")).strip(), "start": float(w["start"]), "end": float(w["end"])}
             for w in (res.get("words") or []) if str(w.get("word", "")).strip()]
    segs = [{"start": float(s["start"]), "end": float(s["end"]), "text": str(s.get("text", "")).strip()}
            for s in (res.get("segments") or [])]
    text = (res.get("text") or " ".join(s["text"] for s in segs)).strip()
    return {"language": res.get("language"), "words": words, "segments": segs, "text": text}


async def _transcribe_file(audio: Path, language, allow_fallback=True) -> dict:
    if GROQ_API_KEY:
        try:
            return _norm(await _groq(audio, language))
        except Exception as e:
            log.warning("Groq failed: %s", e)
            if not allow_fallback:
                raise
    elif not allow_fallback:
        raise RuntimeError("GROQ_API_KEY set nahi hai — lambi video ka transcript nahi ban sakta.")
    return _norm(await asyncio.to_thread(_fallback, audio, language))


async def transcribe(video: Path, language=None) -> dict:
    """Transcribe a short clip. Returns {words, segments, text, language}."""
    mp3 = video.with_name(video.stem + "_stt.mp3")
    await _to_mp3(video, mp3)
    return await _transcribe_file(mp3, language)


async def transcribe_long(audio_src: Path, language=None) -> dict:
    """Transcribe a whole video's audio in CHUNK_S pieces; timestamps are shifted back to the full timeline."""
    work = audio_src.parent / "chunks"
    work.mkdir(exist_ok=True)
    await _to_mp3(audio_src, work / "c_%04d.mp3",
                  extra=("-f", "segment", "-segment_time", str(CHUNK_S), "-reset_timestamps", "1"))
    chunks = sorted(work.glob("c_*.mp3"))
    if not chunks:
        raise RuntimeError("Audio chunks nahi bane.")
    allow_fb = len(chunks) <= 3          # CPU fallback is hopeless for long videos
    words, segments, texts = [], [], []
    lang = None
    for i, ch in enumerate(chunks):
        off = i * CHUNK_S
        r = await _transcribe_file(ch, language, allow_fallback=allow_fb)
        lang = lang or r["language"]
        words += [{**w, "start": w["start"] + off, "end": w["end"] + off} for w in r["words"]]
        segments += [{**s, "start": s["start"] + off, "end": s["end"] + off} for s in r["segments"]]
        texts.append(r["text"])
    return {"language": lang, "words": words, "segments": segments, "text": " ".join(texts)}
