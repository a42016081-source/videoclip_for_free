"""Sending results: Telegram file (<=50 MB, or 2 GB with a local Bot API) / Gofile / self-hosted /dl link."""
import logging
import secrets
import shutil
import time
from pathlib import Path
import httpx
from telegram import InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from config import RENDER_EXTERNAL_URL, MAX_TEMP_MB, TMP_DIR, LOCAL_BOT_API_URL
from database import get_user
log = logging.getLogger(__name__)

# token -> (path, expires_at, owner_uid)
SHARED: dict[str, tuple[Path, float, int]] = {}
LINK_TTL = 45 * 60
TMP_MAX_AGE = 2 * 3600


def tg_limit_mb() -> int:
    return 1990 if LOCAL_BOT_API_URL else 50


def link_base() -> str | None:
    return RENDER_EXTERNAL_URL.rstrip("/") if RENDER_EXTERNAL_URL else None


def register_link(path: Path, uid: int = 0) -> str:
    tok = secrets.token_urlsafe(24)
    SHARED[tok] = (path, time.time() + LINK_TTL, uid)
    return tok


def links_for(uid: int) -> list[tuple[str, Path, float]]:
    now = time.time()
    return [(t, p, e) for t, (p, e, u) in SHARED.items() if u == uid and e > now and p.exists()]


def cleanup_links():
    now = time.time()
    for t in list(SHARED):
        p, e, _ = SHARED[t]
        if now > e or not p.exists():
            SHARED.pop(t, None)


async def upload_gofile(path: Path) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30)) as c:
            urls = ["https://upload.gofile.io/uploadfile"]
            try:
                srv = (await c.get("https://api.gofile.io/servers")).json()
                urls.append(f"https://{srv['data']['servers'][0]['name']}.gofile.io/contents/uploadfile")
            except Exception:
                pass
            for url in urls:
                try:
                    with open(path, "rb") as f:
                        r = await c.post(url, files={"file": (path.name, f)})
                    d = r.json()
                    if d.get("status") == "ok":
                        return d["data"]["downloadPage"]
                except Exception as e:
                    log.warning("Gofile (%s): %s", url, e)
    except Exception as e:
        log.warning("Gofile: %s", e)
    return None


# ── temp-space management ───────────────────────────────
def enforce_temp_limit():
    files = [p for p in TMP_DIR.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    limit = MAX_TEMP_MB * 1024 * 1024
    if total <= limit:
        return
    for f in sorted(files, key=lambda p: p.stat().st_mtime):      # oldest first
        if total <= limit:
            break
        try:
            sz = f.stat().st_size
            f.unlink()
            total -= sz
        except OSError:
            pass


def cleanup_old_tmp(max_age: int = TMP_MAX_AGE):
    """Remove job folders nobody touched for `max_age` seconds (leftovers / expired outputs)."""
    now = time.time()
    for d in TMP_DIR.iterdir():
        try:
            if d.is_dir() and now - d.stat().st_mtime > max_age:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def cleanup_job_dir(job_dir: Path | None, keep: tuple = ()):
    """Delete a job's intermediate files; `keep` = final outputs that stay (cache / history / links)."""
    if not job_dir or not Path(job_dir).exists():
        return
    keep_set = {Path(k).resolve() for k in keep if k}
    if not keep_set:
        shutil.rmtree(job_dir, ignore_errors=True)
        return
    for p in sorted(Path(job_dir).rglob("*"), reverse=True):
        try:
            if p.is_file() and p.resolve() not in keep_set:
                p.unlink()
            elif p.is_dir() and not any(p.iterdir()):
                p.rmdir()
        except OSError:
            pass


# ── sending ─────────────────────────────────────────────
async def _tg_send(bot, chat_id, path: Path, caption: str, markup):
    kw = dict(chat_id=chat_id, caption=caption, reply_markup=markup,
              read_timeout=300, write_timeout=300, connect_timeout=30)
    with open(path, "rb") as f:
        doc = InputFile(f, filename=path.name)
        if path.suffix == ".mp4":
            await bot.send_video(video=doc, supports_streaming=True, **kw)
        elif path.suffix == ".mp3":
            await bot.send_audio(audio=doc, **kw)
        else:
            await bot.send_document(document=doc, **kw)


async def send_clip(bot, chat_id, uid, path: Path, caption: str, quality_label: str = ""):
    """Deliver `path` according to the user's delivery setting. Returns 'telegram' | 'gofile' | 'selfhost'."""
    mode = (get_user(uid).get("delivery") or "auto")
    size_mb = path.stat().st_size / (1024 * 1024)
    base = link_base()
    link_url = f"{base}/dl/{register_link(path, uid)}" if base else None

    if mode != "link" and size_mb <= tg_limit_mb():
        try:
            markup = (InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Get Link", url=link_url)]])
                      if link_url else None)
            await _tg_send(bot, chat_id, path, caption, markup)
            return "telegram"
        except Exception as e:
            log.warning("Telegram send failed: %s", e)
    elif mode == "telegram":
        caption += f"\n\n⚠️ File {size_mb:.0f} MB > {tg_limit_mb()} MB Telegram limit — link bheja ja raha hai."

    gofile = await upload_gofile(path)
    if gofile:
        await bot.send_message(chat_id, f"{caption}\n\n🌐 Gofile link (Gofile ke rules ke hisaab se expire hota hai).",
                               reply_markup=InlineKeyboardMarkup(
                                   [[InlineKeyboardButton("⬇️ Download (Gofile)", url=gofile)]]))
        return "gofile"

    if link_url:
        await bot.send_message(chat_id, f"{caption}\n\n⏳ Link 45 min me expire ho jayega.",
                               reply_markup=InlineKeyboardMarkup(
                                   [[InlineKeyboardButton("⬇️ Download", url=link_url)]]))
        return "selfhost"
    raise RuntimeError("Delivery failed: Telegram upload fail hua, Gofile down hai aur "
                       "RENDER_EXTERNAL_URL set nahi hai (self-hosted link ke liye zaroori).")
