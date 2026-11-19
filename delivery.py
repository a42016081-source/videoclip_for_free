import asyncio, logging, secrets, time
from pathlib import Path
import httpx
from telegram import InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from config import RENDER_EXTERNAL_URL, MAX_TEMP_MB, TMP_DIR
log = logging.getLogger(__name__)

SHARED: dict[str, tuple[Path, float]] = {}
LINK_TTL = 45 * 60

def register_link(path: Path) -> str:
    tok = secrets.token_urlsafe(24)
    SHARED[tok] = (path, time.time() + LINK_TTL)
    return tok

def cleanup_links():
    now = time.time()
    for t in list(SHARED):
        p, e = SHARED[t]
        if now > e or not p.exists(): SHARED.pop(t, None)

async def upload_gofile(path: Path) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=300) as c:
            srv = (await c.get("https://api.gofile.io/servers")).json()
            server = srv["data"]["servers"][0]["name"]
            with open(path, "rb") as f:
                r = await c.post(f"https://{server}.gofile.io/contents/uploadfile",
                                 files={"file": (path.name, f)})
            d = r.json()
            if d.get("status") == "ok": return d["data"]["downloadPage"]
    except Exception as e:
        log.warning("Gofile: %s", e)
    return None

def enforce_temp_limit():
    total = sum(p.stat().st_size for p in TMP_DIR.rglob("*") if p.is_file())
    limit = MAX_TEMP_MB * 1024 * 1024
    if total <= limit: return
    files = sorted([p for p in TMP_DIR.rglob("*") if p.is_file()], key=lambda p: p.stat().st_mtime)
    for f in files:
        if total <= limit: break
        try:
            sz = f.stat().st_size; f.unlink(); total -= sz
        except Exception: pass

async def send_clip(update, context, path: Path, caption: str, quality_label: str):
    chat_id = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id
    size_mb = path.stat().st_size / (1024 * 1024)
    token = register_link(path)
    base = RENDER_EXTERNAL_URL or "http://localhost:8080"
    link_url = f"{base}/dl/{token}"

    if size_mb <= 50:
        try:
            with open(path, "rb") as f:
                await context.bot.send_video(
                    chat_id=chat_id, video=InputFile(f, filename=path.name),
                    caption=caption, supports_streaming=True, timeout=180,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔗 Get Link", url=link_url)
                    ]]))
            return "telegram"
        except Exception as e:
            log.warning("Telegram send failed: %s", e)

    gofile = await upload_gofile(path)
    if gofile:
        await context.bot.send_message(chat_id=chat_id, text=caption,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Download (Gofile)", url=gofile)]]))
        return "gofile"

    await context.bot.send_message(chat_id=chat_id,
        text=f"{caption}\n\n⏳ Link expires in 45 min.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Download", url=link_url)]]))
    return "selfhost"