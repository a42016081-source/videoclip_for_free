"""yt-dlp wrappers (all blocking work runs in a thread so the bot never freezes)."""
import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from config import COOKIES_FILE
log = logging.getLogger(__name__)

_INFO_CACHE: dict[str, tuple[float, dict]] = {}
_JUNK = {".part", ".ytdl", ".temp"}


async def _run(cmd, timeout=1800):
    return await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=timeout)


def _cookie_args():
    return ["--cookies", COOKIES_FILE] if COOKIES_FILE and Path(COOKIES_FILE).exists() else []


def _friendly(stderr: str) -> str:
    err = (stderr or "").strip()
    low = err.lower()
    if "confirm you" in low and "bot" in low:
        return ("YouTube ne bot-check lagaya (Render IP). Admin ko cookies.txt set karni hogi "
                "(README → Cookies).")
    if "private video" in low:
        return "Ye video private hai."
    if "video unavailable" in low or "not available" in low:
        return "Ye video available nahi hai (deleted / region-blocked)."
    errs = [ln for ln in err.splitlines() if ln.startswith("ERROR")]
    return (errs[-1] if errs else err[-400:])[:400] or "Unknown yt-dlp error"


async def fetch_info(url: str) -> dict:
    hit = _INFO_CACHE.get(url)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    cmd = ["yt-dlp", "--dump-single-json", "--no-playlist", "--no-warnings",
           *_cookie_args(), "--", url]
    r = await _run(cmd, 120)
    if r.returncode != 0:
        raise RuntimeError(_friendly(r.stderr))
    info = json.loads(r.stdout)
    _INFO_CACHE[url] = (time.time(), info)
    if len(_INFO_CACHE) > 50:
        _INFO_CACHE.pop(min(_INFO_CACHE, key=lambda k: _INFO_CACHE[k][0]), None)
    return info


def _pick(out_dir: Path, stem: str) -> Path | None:
    files = [p for p in out_dir.glob(f"{stem}.*") if p.suffix not in _JUNK]
    return max(files, key=lambda p: p.stat().st_size) if files else None


def _clear(out_dir: Path, stem: str):
    for p in out_dir.glob(f"{stem}.*"):
        try:
            p.unlink()
        except OSError:
            pass


async def download_section(url, start, end, quality, out_dir, progress_cb=None) -> Path:
    """Download ONLY [start, end] seconds. The returned file's timeline starts at 0."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    heights = {"360": 360, "480": 480, "720": 720, "1080": 1080}
    if quality == "audio":
        formats = ["ba/b"]
    elif quality in heights:
        h = heights[quality]
        formats = [f"bv*[height<={h}]+ba/b[height<={h}]", "bv*+ba/b", "b"]
    else:
        formats = ["bv*+ba/b", "b"]

    info = await fetch_info(url)
    live = bool(info.get("is_live"))
    if progress_cb:
        await progress_cb(0, "Downloading")

    last_err = ""
    for fmt in formats:
        cmd = ["yt-dlp", "--download-sections", f"*{int(start)}-{int(end)}", "--force-keyframes-at-cuts",
               "--no-playlist", "--no-warnings", "-f", fmt, "-o", str(out_dir / "clip.%(ext)s"),
               *_cookie_args()]
        if quality != "audio":
            cmd += ["--merge-output-format", "mp4", "--remux-video", "mp4"]
        if live:
            cmd.insert(1, "--live-from-start")
        cmd += ["--", url]
        r = await _run(cmd)
        if r.returncode == 0:
            p = _pick(out_dir, "clip")
            if p:
                return p
            last_err = "No file produced"
        else:
            last_err = r.stderr
            log.warning("yt-dlp failed with format %r: %s", fmt, (r.stderr or "")[-200:])
        _clear(out_dir, "clip")
    if live:
        raise RuntimeError("Ye stream abhi live hai aur is hisse ko cut nahi kiya ja sakta. "
                           "Stream khatam hone ke baad try karo. (" + _friendly(last_err)[:150] + ")")
    raise RuntimeError(_friendly(last_err))


async def get_chapters(url: str) -> list[dict]:
    return (await fetch_info(url)).get("chapters") or []


async def download_full(url: str, out_dir) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "--no-playlist", "--no-warnings", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
           "-o", str(out_dir / "full.%(ext)s"), *_cookie_args(), "--", url]
    r = await _run(cmd, 3600)
    p = _pick(out_dir, "full")
    if r.returncode != 0 or not p:
        raise RuntimeError(_friendly(r.stderr))
    return p


async def download_audio(url: str, out_dir) -> Path:
    """Whole audio track (small) — used for transcription."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "--no-playlist", "--no-warnings", "-f", "ba/b",
           "-o", str(out_dir / "audio_full.%(ext)s"), *_cookie_args(), "--", url]
    r = await _run(cmd, 3600)
    p = _pick(out_dir, "audio_full")
    if r.returncode != 0 or not p:
        raise RuntimeError(_friendly(r.stderr))
    return p
