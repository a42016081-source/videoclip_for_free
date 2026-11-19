import asyncio, json, logging, subprocess
from pathlib import Path
from config import COOKIES_FILE
log = logging.getLogger(__name__)

async def _run(cmd, timeout=1800):
    return await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=timeout)

def _cookie_args():
    return ["--cookies", COOKIES_FILE] if COOKIES_FILE and Path(COOKIES_FILE).exists() else []

async def fetch_info(url: str) -> dict:
    cmd = ["yt-dlp", "--dump-single-json", "--no-playlist", "--no-warnings", *_cookie_args(), url]
    r = await _run(cmd, 120)
    if r.returncode != 0: raise RuntimeError(r.stderr.strip()[:500])
    return json.loads(r.stdout)

async def download_section(url, start, end, quality, out_dir, progress_cb=None) -> Path:
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    fmt = {"360": "bv*[height<=360]+ba/b[height<=360]", "480": "bv*[height<=480]+ba/b[height<=480]",
           "720": "bv*[height<=720]+ba/b[height<=720]", "1080": "bv*[height<=1080]+ba/b[height<=1080]",
           "best": "bv*+ba/b", "audio": "ba/b"}.get(quality, "bv*+ba/b")
    cmd = ["yt-dlp", "--download-sections", f"*{start}-{end}", "--force-keyframes-at-cuts",
           "--no-playlist", "--no-warnings", "-f", fmt, "--merge-output-format", "mp4",
           "--remux-video", "mp4", "-o", str(out_dir / "clip.%(ext)s"), *_cookie_args(), url]
    info = await fetch_info(url)
    if info.get("is_live") and not info.get("was_live"):
        raise RuntimeError("Stream is still live. Wait until it ends.")
    if info.get("is_live") and info.get("was_live"):
        cmd.insert(1, "--live-from-start")
    if progress_cb: await progress_cb(0, "Downloading")
    r = await _run(cmd)
    if r.returncode != 0:
        log.warning("Retry fallback: %s", r.stderr[:200])
        cmd[cmd.index("-f") + 1] = "bv*+ba/b"
        r = await _run(cmd)
    if r.returncode != 0: raise RuntimeError(r.stderr.strip()[:500])
    files = sorted(out_dir.glob("clip.*"), key=lambda p: p.stat().st_size, reverse=True)
    if not files: raise RuntimeError("No file produced")
    return files[0]

async def get_chapters(url: str) -> list[dict]:
    return (await fetch_info(url)).get("chapters") or []

async def download_full(url: str, out_dir) -> Path:
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "--no-playlist", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
           "-o", str(out_dir / "full.%(ext)s"), *_cookie_args(), url]
    r = await _run(cmd, 3600)
    if r.returncode != 0: raise RuntimeError(r.stderr.strip()[:500])
    return sorted(out_dir.glob("full.*"), key=lambda p: p.stat().st_size, reverse=True)[0]