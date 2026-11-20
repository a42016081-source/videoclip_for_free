"""yt-dlp wrappers (all blocking work runs in a thread so the bot never freezes)."""
import asyncio
import json
import logging
import os
import shutil
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
    args = ["--cookies", COOKIES_FILE] if COOKIES_FILE and Path(COOKIES_FILE).exists() else []
    # GitHub Actions / bare VPS: /etc/yt-dlp.conf (Dockerfile) does not exist, so enable node explicitly.
    if shutil.which("node"):
        args += ["--js-runtimes", "node"]
    return args


# PRECISE_CUTS=1 -> yt-dlp re-encodes the section (frame-accurate but VERY slow). Default off:
# the render step re-encodes the clip anyway, so a second re-encode only wastes time.
PRECISE_CUTS = os.getenv("PRECISE_CUTS", "0") == "1"


def _friendly(stderr: str) -> str:
    err = (stderr or "").strip()
    low = err.lower()
    if "confirm you" in low and "bot" in low:
        return ("YouTube ne bot-check lagaya (Render IP). Admin ko cookies.txt set karni hogi "
                "(README → Cookies).")
    if "private video" in low:
        return "Ye video private hai."
    if "requested format is not available" in low:
        return ("YouTube ne is quality/format ko nahi diya (formats block). yt-dlp update ya cookies.txt "
                "check karo.")
    if "cannot be partially downloaded" in low:
        return "Ye format partial download support nahi karta."
    if "video unavailable" in low or "this video is not available" in low:
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
    """Download ONLY [start, end] seconds. The returned file's timeline starts at 0.

    Live streams: yt-dlp CANNOT combine --live-from-start with --download-sections (the YouTube
    live-from-start DASH downloader is not supported by ffmpeg sectioning -> "cannot be partially
    downloaded"). So for live we use the HLS (m3u8) formats, which ffmpeg can seek in (DVR window).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    info = await fetch_info(url)
    live = bool(info.get("is_live"))
    h = {"360": 360, "480": 480, "720": 720, "1080": 1080}.get(quality, 1080)   # "best" = max 1080p

    if quality == "audio":
        formats = ["ba[protocol^=m3u8]/ba/b"] if live else ["ba/b"]
    elif live:
        formats = [f"b[protocol^=m3u8][height<={h}]", "b[protocol^=m3u8]",
                   "bv*[protocol^=m3u8]+ba[protocol^=m3u8]", "b"]
    else:
        # H.264 first: fast to decode/encode on a small CPU (AV1/VP9 4K is what makes jobs take 1 hour)
        formats = [f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]", f"bv*[height<={h}]+ba/b[height<={h}]",
                   "bv*+ba/b", "b"]
    if progress_cb:
        await progress_cb(0, "Downloading")

    timeout = max(1800, int((end - start) * 4))
    errors = []                                   # keep EVERY attempt's error, show the first real one
    for fmt in formats:
        cmd = ["yt-dlp", "--download-sections", f"*{int(start)}-{int(end)}",
               "--no-playlist", "--no-warnings", "-f", fmt, "-o", str(out_dir / "clip.%(ext)s"),
               *_cookie_args()]
        if PRECISE_CUTS:
            cmd.append("--force-keyframes-at-cuts")
        if quality != "audio":
            cmd += ["--merge-output-format", "mp4", "--remux-video", "mp4"]
        cmd += ["--", url]
        r = await _run(cmd, timeout)
        if r.returncode == 0:
            p = _pick(out_dir, "clip")
            if p:
                return p
            errors.append("No file produced")
        else:
            errors.append(r.stderr or "")
            log.error("yt-dlp failed (live=%s fmt=%r): %s", live, fmt, (r.stderr or "")[-600:])
        _clear(out_dir, "clip")

    # Prefer a specific error over the generic "format not available" of the last fallback format
    specific = [e for e in errors if e and "requested format is not available" not in e.lower()]
    best = (specific or errors or [""])[0]
    if live:
        raise RuntimeError("Live stream clip fail hua: " + _friendly(best)[:250] +
                           "\n(Tip: time stream ke start se count hota hai; stream me DVR on hona chahiye.)")
    raise RuntimeError(_friendly(best))


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
