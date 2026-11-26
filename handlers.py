"""Telegram handlers: commands, menu buttons, callbacks and the job pipeline."""
import logging
import re
import shutil
import time
import uuid
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from config import (TMP_DIR, ADMIN_IDS, FORCE_JOIN_CHANNEL, MAX_ANALYZE_MIN, MAX_UPLOAD_MIN,
                    LOCAL_BOT_API_URL)
from database import (get_user, set_user, is_banned, ban_user, add_history, get_history,
                      get_history_by_id, get_stats, global_stats, all_user_ids, cache_get, cache_put,
                      add_favorite, remove_favorite, is_favorite, get_favorites, is_allowed, allow_user, get_autopost, set_autopost)
from downloader import fetch_info, download_section, get_chapters, download_full, download_audio
from transcriber import transcribe, transcribe_long
from subtitles import (build_ass, build_srt, cfg_fingerprint, STYLE_NAMES, PRESETS, resolve, load_cfg)
from autocolor import palettes_for_video
from substudio import studio_callback, open_studio
from editor import render, audio_only, change_speed, probe_resolution, probe_info
from delivery import send_clip, links_for, link_base, cleanup_job_dir, SHARED
from progress import ProgressBar
from queue_manager import rate_limited, job_slot
from keyboards import (main_kb, CANCEL_KB, preview_kb, settings_kb, history_kb, after_clip_kb)
from utils import (parse_time, fmt_time, extract_video_id, extract_t_from_url,
                   normalize_url, cache_key, human_size)
import llm

try:                                    # newer python-telegram-bot versions only
    from telegram import CopyTextButton
except ImportError:                     # pragma: no cover
    CopyTextButton = None

from autopipeline import run_auto, handle_auto_callback, edit_title
from keyboards import autopost_kb

log = logging.getLogger(__name__)
STATE: dict[int, dict] = {}             # per-user conversation state
_TCACHE: dict[str, tuple[float, dict]] = {}   # video id -> (time, transcript)
MAX_MULTI = 10


# ═════════════ small helpers ═════════════
async def _ack(q, text=None, alert=False):
    try:
        await q.answer(text, show_alert=alert)
    except Exception:
        pass


async def _edit(q, text, markup=None):
    """Edit a message whether it is text or a photo with caption."""
    try:
        await q.edit_message_text(text, reply_markup=markup)
    except Exception:
        try:
            await q.edit_message_caption(caption=text, reply_markup=markup)
        except Exception:
            pass


def _new_state(uid, **kw) -> dict:
    st = {"mode": "cut", "quality": get_user(uid).get("quality") or "best"}
    st.update(kw)
    return st


def _find_url(text: str) -> str:
    m = re.search(r"(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/\S+", text or "")
    return m.group(0) if m else (text or "").strip()


async def _joined(update, ctx) -> bool:
    """Force-join gate (FORCE_JOIN_CHANNEL). Fails open if the bot cannot check."""
    if not FORCE_JOIN_CHANNEL:
        return True
    uid = update.effective_user.id
    if uid in ADMIN_IDS:
        return True
    ch = FORCE_JOIN_CHANNEL if FORCE_JOIN_CHANNEL.startswith(("@", "-")) else "@" + FORCE_JOIN_CHANNEL
    try:
        m = await ctx.bot.get_chat_member(ch, uid)
        if m.status not in ("left", "kicked"):
            return True
    except Exception as e:
        log.warning("force-join check failed (%s) — allowing: %s", ch, e)
        return True
    kb = None
    if ch.startswith("@"):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join channel", url=f"https://t.me/{ch[1:]}")]])
    await ctx.bot.send_message(update.effective_chat.id,
                               f"🔒 Bot use karne ke liye pehle {ch} join karo, phir dobara try karo.",
                               reply_markup=kb)
    return False


async def _gate(update, ctx) -> bool:
    uid = update.effective_user.id
    if not is_allowed(uid):
        if update.callback_query:
            await _ack(update.callback_query, "🔒 Owner se access lo.", True)
        elif update.effective_message:
            await update.effective_message.reply_text("🔒 Ye private bot hai. Owner ki permission ke baad hi use kar sakte ho. Apna ID dekhne ke liye /id bhejo.")
        return False
    if is_banned(uid):
        if update.callback_query:
            await _ack(update.callback_query, "🚫 Banned", True)
        elif update.effective_message:
            await update.effective_message.reply_text("🚫 Banned.")
        return False
    if not await _joined(update, ctx):
        if update.callback_query:
            await _ack(update.callback_query)
        return False
    return True


# ═════════════ basic commands ═════════════
async def start(update, ctx):
    if not await _gate(update, ctx):
        return
    get_user(update.effective_user.id)
    await update.message.reply_text(
        "👋 Send a YouTube link, or use a button.\n`/cut <url> <start> <end>` also works.",
        parse_mode="Markdown", reply_markup=main_kb())


async def help_cmd(update, ctx):
    if not await _gate(update, ctx): return
    await update.effective_message.reply_text(
        "✂️ *YouTube Clipper Bot*\n\n"
        "Send a YouTube URL → pick start/end → confirm.\n"
        "`/cut <url> 00:10:30 00:12:00`\n"
        "`/multi <url>` → many ranges at once\n\n"
        "📱 Shorts: 9:16 blur/crop/zoom\n"
        "💬 Subs: Groq Whisper karaoke\n"
        "📤 Apna video bhejo → typography subtitles (16:9 / 9:16), colours video se auto\n"
        "📥 Delivery: ≤50 MB Telegram · else link\n"
        "🔥 Auto Highlights · 📑 Chapters · 🔍 Search",
        parse_mode="Markdown", reply_markup=main_kb())


async def ping(update, ctx):
    if not await _gate(update, ctx): return
    await update.effective_message.reply_text("🏓 Pong")


async def cut_command(update, ctx):
    if not await _gate(update, ctx):
        return
    a = ctx.args
    if len(a) < 3:
        return await update.message.reply_text("Usage: `/cut <url> <start> <end>`", parse_mode="Markdown")
    if not extract_video_id(a[0]):
        return await update.message.reply_text("❌ Valid YouTube link bhejo.")
    s, e = parse_time(a[1]), parse_time(a[2])
    if s is None or e is None or e <= s:
        return await update.message.reply_text("❌ Bad times. SS / MM:SS / HH:MM:SS (end > start)")
    uid = update.effective_user.id
    STATE[uid] = _new_state(uid, step="await_quality", url=normalize_url(a[0]), start=s, end=e)
    await _show_preview(update, ctx)


async def multi_command(update, ctx):
    if not await _gate(update, ctx):
        return
    uid = update.effective_user.id
    st = _new_state(uid, mode="multi", step="await_url")
    STATE[uid] = st
    if ctx.args and extract_video_id(ctx.args[0]):
        st["url"] = normalize_url(ctx.args[0])
        st["step"] = "await_multi_ranges"
        return await update.message.reply_text(_MULTI_HELP, parse_mode="Markdown", reply_markup=CANCEL_KB)
    await update.message.reply_text("🎞 Multi-clip: send the YouTube link.", reply_markup=CANCEL_KB)


_MULTI_HELP = f"Send ranges, one per line (max {MAX_MULTI}):\n`00:10 00:30`\n`01:00 01:45`"


# ═════════════ message router (single entry) ═════════════
BUTTON_HANDLERS = {}


async def route_message(update, ctx):
    uid = update.effective_user.id
    if not await _gate(update, ctx):
        return
    text = update.message.text or ""
    if text == "❌ Cancel":
        STATE.pop(uid, None)
        return await update.message.reply_text("❌ Cancelled.", reply_markup=main_kb())
    if text in BUTTON_HANDLERS:
        return await BUTTON_HANDLERS[text](update, ctx)
    step = STATE.get(uid, {}).get("step")
    if step == "await_auto_title":
        return await edit_title(uid, text, ctx)
    if step == "await_auto_schedule":
        from datetime import datetime
        try:
            dt=datetime.fromisoformat(text.replace("Z","+00:00"))
            if dt.tzinfo is None: raise ValueError("timezone required")
            st=STATE.pop(uid,{}); item_id=st["auto_job_id"]; from autopipeline import set_schedule; return await set_schedule(uid,item_id,dt.isoformat(),ctx)
        except Exception:
            return await update.message.reply_text("❌ ISO time bhejo, jaise 2026-12-01T18:30:00+05:30")
    if step == "await_auto_url":
        STATE.pop(uid,None); return await run_auto(update,ctx,_find_url(text))
    if step == "await_video":
        return await update.message.reply_text("📤 Video file bhejo (Telegram me normal video ya file). Cancel: ❌ Cancel",
                                               reply_markup=CANCEL_KB)
    if step == "await_start":
        return await _on_start(update, ctx)
    if step == "await_end":
        return await _on_end(update, ctx)
    if step == "await_keyword":
        return await _on_keyword(update, ctx)
    if step == "await_multi_ranges":
        return await _on_multi_ranges(update, ctx)
    return await _on_url(update, ctx)


# ═════════════ URL / time flow ═════════════
async def _on_url(update, ctx):
    text = update.message.text.strip()
    url = normalize_url(_find_url(text))
    if not extract_video_id(url):
        return await update.message.reply_text(
            "❌ Send a valid YouTube link or use a button.", reply_markup=main_kb())
    uid = update.effective_user.id
    st = STATE.get(uid) or _new_state(uid)
    st.setdefault("quality", get_user(uid).get("quality") or "best")
    st["url"] = url
    STATE[uid] = st
    mode = st.get("mode", "cut")
    if mode in ("cut", "short"):
        st["start"] = extract_t_from_url(text) or 0
        st["step"] = "await_start"
        await update.message.reply_text(
            f"Start time? (default `{fmt_time(st['start'])}` — send `-` to keep)",
            parse_mode="Markdown", reply_markup=CANCEL_KB)
    elif mode == "highlights":
        STATE.pop(uid, None)
        await _run_highlights(update, ctx, url)
    elif mode == "chapters":
        STATE.pop(uid, None)
        await _run_chapters(update, ctx, url)
    elif mode == "search":
        st["step"] = "await_keyword"
        await update.message.reply_text("🔍 Send keyword to search.", reply_markup=CANCEL_KB)
    elif mode == "full":
        STATE.pop(uid, None)
        await _run_full(update, ctx, url)
    elif mode == "multi":
        st["step"] = "await_multi_ranges"
        await update.message.reply_text(_MULTI_HELP, parse_mode="Markdown", reply_markup=CANCEL_KB)


async def _on_start(update, ctx):
    uid = update.effective_user.id
    st = STATE[uid]
    text = update.message.text.strip()
    if text and text != "-":
        v = parse_time(text)
        if v is None:
            return await update.message.reply_text("❌ Bad format. SS / MM:SS / HH:MM:SS")
        st["start"] = v
    st["step"] = "await_end"
    await update.message.reply_text(f"✅ Start: `{fmt_time(st['start'])}`\nEnd time?",
                                    parse_mode="Markdown", reply_markup=CANCEL_KB)


async def _on_end(update, ctx):
    uid = update.effective_user.id
    st = STATE[uid]
    v = parse_time(update.message.text.strip())
    if v is None:
        return await update.message.reply_text("❌ Bad format. SS / MM:SS / HH:MM:SS")
    if v <= st["start"]:
        return await update.message.reply_text("❌ End must be > start.")
    st["end"] = v
    st["step"] = "await_quality"
    await _show_preview(update, ctx)


async def _show_preview(update, ctx):
    """Thumbnail + title + duration + quality/toggle buttons. Works for messages AND callbacks."""
    uid = update.effective_user.id
    st = STATE[uid]
    msg = update.effective_message
    try:
        info = await fetch_info(st["url"])
    except Exception as e:
        STATE.pop(uid, None)
        return await msg.reply_text(f"❌ {str(e)[:300]}", reply_markup=main_kb())
    dur = info.get("duration") or 0
    if not info.get("is_live") and dur and st["end"] > dur:
        st["step"] = "await_end"           # let the user simply type a new end time
        return await msg.reply_text(f"❌ End ({fmt_time(st['end'])}) video ki length {fmt_time(dur)} se zyada hai.")
    st["dur"] = dur
    user = get_user(uid)
    length = st["end"] - st["start"]
    lines = [f"🎬 {(info.get('title') or '?')[:80]}",
             f"⏱ {fmt_time(st['start'])} → {fmt_time(st['end'])} ({fmt_time(length)})"]
    if info.get("is_live"):
        lines.append("🔴 Live stream — time stream ke start se count hoga (DVR wale hisse se cut hoga).")
    if (user["shorts"] or st.get("force_shorts")) and length > 60:
        lines.append("⚠️ Shorts limit 60 sec hai — clip lamba hai, phir bhi ban jayega.")
    if length > 300 or user["subs"]:
        lines.append("⏳ Is job me kuch minute lag sakte hain (free server).")
    lines.append("\nChoose quality & start:")
    text = "\n".join(lines)
    kb = preview_kb(st.get("quality", "best"), user)
    thumb = info.get("thumbnail")
    if thumb:
        try:
            return await msg.reply_photo(thumb, caption=text, reply_markup=kb)
        except Exception as e:
            log.info("thumbnail failed: %s", e)
    await msg.reply_text(text, reply_markup=kb)


# ═════════════ callback router ═════════════
QUALITIES = {"360", "480", "720", "1080", "best", "audio"}


def _is_preview(q) -> bool:
    try:
        return any(b.callback_data == "conf:start" for row in q.message.reply_markup.inline_keyboard for b in row)
    except Exception:
        return False


async def route_callback(update, ctx):
    q = update.callback_query
    uid = update.effective_user.id
    d = q.data or ""
    if not await _gate(update, ctx):
        return
    if not d.startswith(("fav:", "spd:", "srt:")):
        await _ack(q)
    try:
        if d.startswith("q:"):
            if d[2:] in QUALITIES:
                st = STATE.get(uid)
                if st:
                    st["quality"] = d[2:]
                try:
                    await q.edit_message_reply_markup(preview_kb(d[2:], get_user(uid)))
                except Exception:
                    pass
        elif d.startswith("toggle:"):
            key = d[7:]
            if key not in ("shorts", "subs"):
                return
            user = get_user(uid)
            set_user(uid, **{key: 0 if user[key] else 1})
            user = get_user(uid)
            kb = (preview_kb(STATE.get(uid, {}).get("quality", "best"), user)
                  if _is_preview(q) else settings_kb(user))
            try:
                await q.edit_message_reply_markup(kb)
            except Exception:
                pass
        elif d == "conf:start":
            await _run_job(update, ctx)
        elif d == "conf:cancel":
            if _is_preview(q):
                STATE.pop(uid, None)
            await _edit(q, "❌ Cancelled.")
        elif d.startswith("sub:"):
            await studio_callback(update, ctx)
        elif d.startswith("up:"):
            await _upload_callback(update, ctx)
        elif d.startswith("set:"):
            _, key, val = d.split(":")
            await _apply_setting(update, ctx, key, val)
        elif d.startswith("chap:"):
            await _cut_chapter(update, ctx, int(d[5:]))
        elif d == "hl:all":
            await _cut_all_highlights(update, ctx)
        elif d.startswith("hl:"):
            await _cut_highlight(update, ctx, int(d[3:]))
        elif d.startswith("re:"):
            await _recut(update, ctx, int(d[3:]))
        elif d.startswith("fav:"):
            await _toggle_fav(update, int(d[4:]))
        elif d.startswith("spd:"):
            _, hid, speed = d.split(":")
            await _apply_speed(update, ctx, int(hid), float(speed))
        elif d.startswith("srt:"):
            await _send_srt(update, ctx, int(d[4:]))
        elif d.startswith("srch:"):
            await _cut_search(update, ctx, int(d[5:]))
        elif d.startswith("auto:"):
            await _auto_settings_callback(update,ctx)
    except ValueError:
        log.warning("bad callback data: %r", d)


_CYCLE = {
    "quality": ["360", "480", "720", "1080", "best"],
    "delivery": ["auto", "telegram", "link"],
    "reframe": ["blur", "crop", "zoom"],
    "sub_style": STYLE_NAMES,
    "sub_lang": ["auto", "hi", "en"],
    "bot_lang": ["en", "hinglish"],
}


async def _apply_setting(update, ctx, key, val):
    q = update.callback_query
    uid = update.effective_user.id
    opts = _CYCLE.get(key)
    if not opts:
        return
    user = get_user(uid)
    if val == "cycle":
        cur = user.get(key)
        val = opts[(opts.index(cur) + 1) % len(opts)] if cur in opts else opts[0]
    elif val not in opts:
        return
    set_user(uid, **{key: val})
    try:
        await q.edit_message_reply_markup(settings_kb(get_user(uid)))
    except Exception:
        pass


async def _toggle_fav(update, hid):
    q = update.callback_query
    uid = update.effective_user.id
    row = get_history_by_id(hid)
    if not row or row["user_id"] != uid:
        return await _ack(q, "Not found")
    if is_favorite(uid, hid):
        remove_favorite(uid, hid)
        await _ack(q, "☆ Removed from favorites")
    else:
        add_favorite(uid, hid)
        await _ack(q, "⭐ Saved to favorites")


# ═════════════ job pipeline ═════════════
async def _run_job(update, ctx, edit=True):
    """Confirm button -> start the job in `STATE`."""
    q = update.callback_query
    uid = update.effective_user.id
    st = STATE.get(uid)
    if not st or st.get("step") != "await_quality":
        if q and edit:
            await _edit(q, "Nothing to run.")
        return
    STATE.pop(uid, None)                    # pop NOW: a double-tap must not start two jobs
    if q and edit:
        await _edit(q, "▶️ Job started…")
    await _execute_job(uid, update.effective_chat.id, ctx.bot, st)


async def _caption(path: Path, requested: str, dur: float) -> str:
    res = await probe_resolution(path)
    size = human_size(path.stat().st_size)
    if path.suffix == ".mp3":
        return f"🎵 MP3 · {size} · ⏱ {fmt_time(dur)}"
    return f"✅ Quality: {res or '?'} (requested: {requested})\n📦 {size} · ⏱ {fmt_time(dur)}"


async def _after_clip(bot, chat_id, hid, video=True, has_srt=False):
    try:
        await bot.send_message(chat_id, "What next?", reply_markup=after_clip_kb(hid, video, has_srt))
        await bot.send_message(chat_id, "👇 Menu", reply_markup=main_kb())
    except Exception as e:
        log.warning("after_clip: %s", e)


async def _execute_job(uid, chat_id, bot, st) -> str:
    """Download → (transcribe) → ONE ffmpeg pass → deliver. Returns 'ok' | 'failed' | 'limited'."""
    if rate_limited(uid):
        await bot.send_message(chat_id, "⏳ Rate limit: 10 clips/hour. Try later.", reply_markup=main_kb())
        return "limited"
    user = get_user(uid)
    url, s, e = st["url"], int(st["start"]), int(st["end"])
    dur = e - s
    quality = st.get("quality") or user.get("quality") or "best"
    audio = quality == "audio"
    shorts = (bool(user["shorts"]) or bool(st.get("force_shorts"))) and not audio
    subs = bool(user["subs"]) and not audio
    style, reframe, lang = user["sub_style"], user["reframe"], user["sub_lang"]

    ck = cache_key(url, s, e, quality, shorts, subs, cfg_fingerprint(style, user.get("sub_cfg")),
                   reframe if shorts else "-", lang if subs else "-")
    cached = cache_get(ck)
    if cached:
        p = Path(cached)
        try:
            await send_clip(bot, chat_id, uid, p, "♻️ Cached\n" + await _caption(p, quality, dur), quality)
            hid = add_history(uid, url, s, e, quality, str(p))
            await _after_clip(bot, chat_id, hid, True, p.with_suffix(".srt").exists())
            return "ok"
        except Exception as ex:
            log.warning("cached send failed, re-processing: %s", ex)

    msg = await bot.send_message(chat_id, "⏳ Queued…")
    bar = ProgressBar(msg, dur)

    async def on_queue(pos):
        await bar.finish(f"⏳ Queue position: {pos} — thoda wait karo…")

    job_dir = TMP_DIR / uuid.uuid4().hex
    out = None
    keep = ()
    try:
        async with job_slot(on_queue):
            job_dir.mkdir(parents=True, exist_ok=True)

            async def pcb(c, stage):
                await bar.update(current=c, stage=stage, force=True)

            src = await download_section(url, s, e, quality, job_dir, progress_cb=pcb)

            if audio:
                out = job_dir / "audio.mp3"
                await bar.update(stage="Encoding")
                await audio_only(src, out, dur)
            else:
                ass = srt_text = transcript = None
                if subs:
                    await bar.update(stage="Transcribing", force=True)
                    try:
                        tr = await transcribe(src, None if lang == "auto" else lang)
                        if tr["words"]:
                            palettes = None
                            if resolve(style, user.get("sub_cfg"), shorts)["auto"]:      # colours "from video"
                                await bar.update(stage="Picking colours", force=True)
                                palettes = await palettes_for_video(src, dur)
                            ass = job_dir / "subs.ass"
                            ass.write_text(build_ass(tr["words"], style, vertical=shorts, cfg=user.get("sub_cfg"),
                                                     palettes=palettes), encoding="utf-8")
                            srt_text = build_srt(tr["words"])
                            transcript = tr["text"]
                        else:
                            await bot.send_message(chat_id, "ℹ️ Clip me speech detect nahi hui — bina subtitles ke bana raha hoon.")
                    except Exception as ex:
                        log.warning("transcription failed: %s", ex)
                        await bot.send_message(chat_id, "⚠️ Subtitles nahi ban paye — bina subtitles ke bana raha hoon.")
                out = job_dir / ("short.mp4" if shorts else "clip_out.mp4")
                await bar.update(stage="Encoding", force=True)
                await render(src, out, dur, reframe if shorts else None, ass, bar)
                if srt_text:
                    out.with_suffix(".srt").write_text(srt_text, encoding="utf-8")
                if transcript:
                    out.with_suffix(".txt").write_text(transcript, encoding="utf-8")

            await bar.update(stage="Uploading", current=dur, force=True)
            await send_clip(bot, chat_id, uid, out, await _caption(out, quality, dur), quality)
            cache_put(ck, str(out))
            hid = add_history(uid, url, s, e, quality, str(out))
            keep = (out, out.with_suffix(".srt"), out.with_suffix(".txt"))
            cleanup_job_dir(job_dir, keep)             # drop the big intermediates right away
        await bar.finish("✅ Done")
        await _after_clip(bot, chat_id, hid, not audio, out.with_suffix(".srt").exists())
        return "ok"
    except Exception as ex:
        log.exception("job failed")
        cleanup_job_dir(job_dir)
        await bar.finish(f"❌ Failed: {str(ex)[:300]}")
        await bot.send_message(chat_id, "Try again.", reply_markup=main_kb())
        return "failed"


# ═════════════ upload your own video + subtitles ═════════════
TG_DL_LIMIT_MB = 2000 if LOCAL_BOT_API_URL else 20       # what a bot may download from Telegram


async def upload_command(update, ctx):
    if not await _gate(update, ctx):
        return
    uid = update.effective_user.id
    STATE[uid] = _new_state(uid, mode="upload", step="await_video")
    lim = f"{TG_DL_LIMIT_MB} MB" if not LOCAL_BOT_API_URL else "2 GB"
    await update.effective_message.reply_text(
        "📤 Apna video bhejo — main usme typography subtitles laga dunga.\n\n"
        f"• Max size: {lim} (Telegram ki limit) · max {MAX_UPLOAD_MIN} min\n"
        "• Tip: video ko normal *video* ki tarah bhejo (File/Document nahi) — Telegram use compress kar deta hai.\n"
        "• Output: 16:9 · 9:16 · Original — aap agle step me chunoge.",
        parse_mode="Markdown", reply_markup=CANCEL_KB)


async def on_video(update, ctx):
    """A video (or video file) was sent: remember it and show the options."""
    if not await _gate(update, ctx):
        return
    uid = update.effective_user.id
    m = update.message
    v = m.video or m.document
    if not v:
        return
    size_mb = (v.file_size or 0) / (1024 * 1024)
    if size_mb > TG_DL_LIMIT_MB:
        return await m.reply_text(
            f"❌ Ye file {size_mb:.0f} MB ki hai — Telegram bots sirf {TG_DL_LIMIT_MB} MB tak download kar sakte hain.\n\n"
            "Kya karein:\n• Video ko Telegram me normal *video* ki tarah (compressed) bhejo, ya chhota clip kaat ke bhejo\n"
            "• Ya server par LOCAL_BOT_API_URL set karo (2 GB tak ki files) — README dekho.",
            parse_mode="Markdown", reply_markup=main_kb())
    dur = getattr(v, "duration", None)
    if dur and dur > MAX_UPLOAD_MIN * 60:
        return await m.reply_text(f"❌ Video {fmt_time(dur)} ki hai — max {MAX_UPLOAD_MIN} min allowed.",
                                  reply_markup=main_kb())
    name = getattr(v, "file_name", None) or "video.mp4"
    st = _new_state(uid, mode="upload", step="upload_opts", file_id=v.file_id, file_name=name,
                    size_mb=size_mb, duration=dur or 0, ratio="16:9", style="typo", auto=True,
                    w=getattr(v, "width", 0) or 0, h=getattr(v, "height", 0) or 0)
    STATE[uid] = st
    await m.reply_text(_upload_text(st), reply_markup=_upload_kb(st))


def _upload_text(st) -> str:
    info = f"{st['size_mb']:.1f} MB"
    if st.get("duration"):
        info += f" · ⏱ {fmt_time(st['duration'])}"
    if st.get("w") and st.get("h"):
        info += f" · {st['w']}x{st['h']}"
    label = PRESETS[st["style"]]["label"]
    ratio = {"16:9": "16:9 (landscape)", "9:16": "9:16 (Shorts/Reels)", "orig": "Original"}[st["ratio"]]
    return (f"📤 {st['file_name'][:40]}\n{info}\n\n"
            f"Output: {ratio}\nStyle: {label}\n"
            f"Colours: {'🎬 video se auto' if st['auto'] else 'style ke default'}\n\n"
            "Options chuno, phir ✅ dabao.")


def _upload_kb(st) -> InlineKeyboardMarkup:
    B = InlineKeyboardButton
    rows = [[B(("✅ " if st["ratio"] == x else "") + lbl, callback_data=f"up:ratio:{x}")
             for x, lbl in (("16:9", "🖥 16:9"), ("9:16", "📱 9:16"), ("orig", "🎞 Original"))],
            [B("🎨 Style: " + PRESETS[st["style"]]["label"].split(" (")[0], callback_data="up:style")],
            [B(f"🎬 Auto colours from video: {'ON' if st['auto'] else 'OFF'}", callback_data="up:auto")],
            [B("✅ Add subtitles", callback_data="up:go"), B("❌ Cancel", callback_data="up:cancel")]]
    return InlineKeyboardMarkup(rows)


async def _upload_callback(update, ctx):
    q = update.callback_query
    uid = update.effective_user.id
    parts = (q.data or "").split(":", 2)             # maxsplit: the ratio value itself contains a colon ("9:16")
    act = parts[1] if len(parts) > 1 else ""
    st = STATE.get(uid)
    if act == "cancel":
        STATE.pop(uid, None)
        return await _edit(q, "❌ Cancelled.")
    if not st or st.get("step") != "upload_opts":
        return await _edit(q, "⌛ Ye request purani ho gayi — video dobara bhejo.")
    if act == "ratio" and len(parts) == 3 and parts[2] in ("16:9", "9:16", "orig"):
        st["ratio"] = parts[2]
    elif act == "style":
        st["style"] = STYLE_NAMES[(STYLE_NAMES.index(st["style"]) + 1) % len(STYLE_NAMES)]
        st["auto"] = bool(PRESETS[st["style"]].get("auto", False)) or st["auto"]
    elif act == "auto":
        st["auto"] = not st["auto"]
    elif act == "go":
        STATE.pop(uid, None)                       # pop NOW: a double-tap must not start two jobs
        await _edit(q, "▶️ Job started…")
        return await _execute_upload(uid, update.effective_chat.id, ctx.bot, st)
    else:
        return
    try:
        await q.edit_message_text(_upload_text(st), reply_markup=_upload_kb(st))
    except Exception:
        pass


def _upload_cfg(user, st) -> dict:
    """The user's Subtitle Studio overrides + the per-upload choice about auto colours."""
    cfg = load_cfg(user.get("sub_cfg"))
    if st["auto"]:
        cfg["hi"] = "video"
    elif cfg.get("hi") in (None, "video"):
        if PRESETS[st["style"]].get("auto"):
            cfg["hi"] = PRESETS[st["style"]]["hi"]          # typography without auto colours -> its fixed colour
        else:
            cfg.pop("hi", None)
    return cfg


async def _execute_upload(uid, chat_id, bot, st) -> str:
    """Download the user's file → transcribe → (pick colours) → ONE ffmpeg pass → deliver."""
    if rate_limited(uid):
        await bot.send_message(chat_id, "⏳ Rate limit: 10 clips/hour. Try later.", reply_markup=main_kb())
        return "limited"
    user = get_user(uid)
    style, ratio, lang = st["style"], st["ratio"], user["sub_lang"]
    cfg = _upload_cfg(user, st)
    msg = await bot.send_message(chat_id, "⏳ Queued…")
    bar = ProgressBar(msg, st.get("duration") or 60)

    async def on_queue(pos):
        await bar.finish(f"⏳ Queue position: {pos} — thoda wait karo…")

    job_dir = TMP_DIR / uuid.uuid4().hex
    out = None
    try:
        async with job_slot(on_queue):
            job_dir.mkdir(parents=True, exist_ok=True)
            await bar.update(stage="Downloading", force=True)
            tgfile = await bot.get_file(st["file_id"])
            suffix = Path(st["file_name"]).suffix.lower()
            src = job_dir / ("input" + (suffix if suffix in (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v") else ".mp4"))
            await tgfile.download_to_drive(src)

            info = await probe_info(src)
            if not info["w"] or not info["h"] or not info["dur"]:
                raise RuntimeError("Ye valid video file nahi lag rahi.")
            dur = info["dur"]
            if dur > MAX_UPLOAD_MIN * 60:
                raise RuntimeError(f"Video {fmt_time(dur)} ki hai — max {MAX_UPLOAD_MIN} min.")
            bar.total = max(dur, 1)

            w, h = info["w"] & ~1, info["h"] & ~1                     # even sizes only (libx264)
            if ratio == "9:16":
                reframe, size = user["reframe"], (1080, 1920)
            elif ratio == "16:9":
                if abs(w / h - 16 / 9) < 0.02:
                    reframe, size = None, (w, h)                      # already 16:9: keep the pixels
                else:
                    reframe, size = "wide", (1920, 1080)              # fit + blurred side bars
            else:
                reframe, size = None, (w, h)

            await bar.update(stage="Transcribing", force=True)
            tr = await transcribe(src, None if lang == "auto" else lang)
            if not tr["words"]:
                raise RuntimeError("Video me speech detect nahi hui — subtitles nahi ban sakte.")

            palettes = None
            if resolve(style, cfg, size=size)["auto"]:
                await bar.update(stage="Picking colours", force=True)
                palettes = await palettes_for_video(src, dur)
            ass = job_dir / "subs.ass"
            ass.write_text(build_ass(tr["words"], style, cfg=cfg, size=size, palettes=palettes), encoding="utf-8")

            out = job_dir / "subtitled.mp4"
            await bar.update(stage="Encoding", force=True)
            await render(src, out, dur, reframe, ass, bar)
            out.with_suffix(".srt").write_text(build_srt(tr["words"]), encoding="utf-8")
            out.with_suffix(".txt").write_text(tr["text"], encoding="utf-8")

            await bar.update(stage="Uploading", current=dur, force=True)
            cap = (f"✅ {PRESETS[style]['label'].split(' (')[0]} · {size[0]}x{size[1]}"
                   + (" · 🎬 auto colours" if palettes else "")
                   + f"\n📦 {human_size(out.stat().st_size)} · ⏱ {fmt_time(dur)}")
            await send_clip(bot, chat_id, uid, out, cap, "upload")
            hid = add_history(uid, "upload:" + st["file_name"][:60], 0, int(dur), "upload", str(out))
            cleanup_job_dir(job_dir, (out, out.with_suffix(".srt"), out.with_suffix(".txt")))
        await bar.finish("✅ Done")
        await _after_clip(bot, chat_id, hid, True, True)
        return "ok"
    except Exception as ex:
        log.exception("upload job failed")
        cleanup_job_dir(job_dir)
        await bar.finish(f"❌ Failed: {str(ex)[:300]}")
        await bot.send_message(chat_id, "Try again.", reply_markup=main_kb())
        return "failed"


# ═════════════ menu buttons ═════════════
async def _prompt(u, mode, text, **extra):
    uid = u.effective_user.id
    STATE[uid] = _new_state(uid, step="await_url", mode=mode, **extra)
    await u.message.reply_text(text, reply_markup=CANCEL_KB)


async def _btn_cut(u, c):
    await _prompt(u, "cut", "✂️ Send the YouTube link.")


async def _btn_audio(u, c):
    await _prompt(u, "cut", "🎵 Send the YouTube link.", quality="audio")


async def _btn_short(u, c):
    await _prompt(u, "short", "📱 Shorts ON. Send the YouTube link.", force_shorts=True)


async def _btn_live(u, c):
    await _prompt(u, "cut", "🔴 Send live/VOD link.")


async def _btn_highlights(u, c):
    await _prompt(u, "highlights", "🔥 Send link — I'll find 5 viral moments.")


async def _btn_chapters(u, c):
    await _prompt(u, "chapters", "📑 Send link — I'll list chapters.")


async def _btn_search(u, c):
    await _prompt(u, "search", "🔍 Send link first.")


async def _btn_full(u, c):
    await _prompt(u, "full", "📥 Send link — full download.")


def _hist_lines(rows):
    return [f"• {fmt_time(r['start'])}–{fmt_time(r['end'])} · {r['quality']}" for r in rows[:10]]


async def _btn_history(u, c):
    rows = get_history(u.effective_user.id)
    if not rows:
        return await u.message.reply_text("No history yet.")
    await u.message.reply_text("📜 History\n" + "\n".join(_hist_lines(rows)), reply_markup=history_kb(rows))


async def _btn_favorites(u, c):
    rows = get_favorites(u.effective_user.id)
    if not rows:
        return await u.message.reply_text("No favorites yet.")
    await u.message.reply_text("⭐ Favorites\n" + "\n".join(_hist_lines(rows)), reply_markup=history_kb(rows))


async def _btn_links(u, c):
    uid = u.effective_user.id
    links = links_for(uid)
    base = link_base()
    if not links or not base:
        return await u.message.reply_text("No active links.")
    now = time.time()
    lines = [f"• {p.name} — {max(int((e - now) // 60), 0)} min left\n{base}/dl/{t}" for t, p, e in links[:10]]
    await u.message.reply_text("🔗 Your active links (expire in 45 min):\n\n" + "\n\n".join(lines),
                               disable_web_page_preview=True)


async def _btn_studio(u, c):
    await open_studio(u.message, u.effective_user.id)


async def _btn_upload(u, c):
    await upload_command(u, c)

async def _btn_auto(u,c):
    await u.message.reply_text("🤖 Auto Post settings — options choose karo. Start karne ke liye /auto <YouTube URL> bhejo.",reply_markup=autopost_kb(get_autopost(u.effective_user.id)))
    STATE[u.effective_user.id]={"step":"await_auto_url"}


async def _btn_settings(u, c):
    await u.message.reply_text("⚙️ Settings — tap to change:", reply_markup=settings_kb(get_user(u.effective_user.id)))


async def _btn_stats(u, c):
    s = get_stats(u.effective_user.id)
    await u.message.reply_text(f"📊 Clips: {s['clips']}\nLast: {s['last_clip'] or '—'}")


async def _btn_help(u, c):
    await help_cmd(u, c)


async def _btn_ping(u, c):
    await ping(u, c)


BUTTON_HANDLERS.update({
    "✂️ Cut Video": _btn_cut, "🎵 Audio Only": _btn_audio,
    "📱 Make Short (9:16)": _btn_short, "🔴 Live Stream Clip": _btn_live,
    "🔥 Auto Highlights": _btn_highlights, "📑 Chapter Cut": _btn_chapters,
    "🔍 Keyword Search": _btn_search, "📥 Download Full": _btn_full,
    "📜 My History": _btn_history, "⭐ Favorites": _btn_favorites,
    "🔗 My Links": _btn_links, "⚙️ Settings": _btn_settings, "🎨 Subtitle Studio": _btn_studio,
    "📊 My Stats": _btn_stats, "❓ Help": _btn_help, "🏓 Ping/Status": _btn_ping,
    "📤 Upload Video + Subs": _btn_upload, "🤖 Auto Post": _btn_auto,
})


# ═════════════ highlights / chapters / search / full ═════════════
def _analyze_error(info) -> str | None:
    if info.get("is_live"):
        return "❌ Live stream abhi analyze nahi ho sakti. Khatam hone ke baad try karo."
    dur = info.get("duration") or 0
    if not dur:
        return "❌ Video ki length pata nahi chali."
    if dur > MAX_ANALYZE_MIN * 60:
        return f"❌ Video bahut lambi hai (max {MAX_ANALYZE_MIN} min)."
    return None


async def _get_transcript(url) -> dict:
    """Whole-video transcript (audio only, chunked). Cached for an hour."""
    vid = extract_video_id(url)
    hit = _TCACHE.get(vid)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    job_dir = TMP_DIR / uuid.uuid4().hex
    try:
        async with job_slot():
            job_dir.mkdir(parents=True, exist_ok=True)
            audio = await download_audio(url, job_dir)
            trans = await transcribe_long(audio)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
    _TCACHE[vid] = (time.time(), trans)
    if len(_TCACHE) > 5:
        _TCACHE.pop(min(_TCACHE, key=lambda k: _TCACHE[k][0]), None)
    return trans


async def _run_highlights(update, ctx, url):
    uid = update.effective_user.id
    if not llm.available():
        return await update.message.reply_text("❌ LLM_API_KEY (ya GROQ_API_KEY) set nahi hai.", reply_markup=main_kb())
    msg = await update.message.reply_text("🔥 Transcript ban raha hai… (lambi video me kuch minute lag sakte hain)",
                                          reply_markup=main_kb())
    try:
        info = await fetch_info(url)
        err = _analyze_error(info)
        if err:
            return await msg.edit_text(err)
        dur = info["duration"]
        trans = await _get_transcript(url)
        moments = await llm.find_highlights(trans["segments"], dur)
        if not moments:
            return await msg.edit_text("❌ No highlights found.")
        STATE[uid] = _new_state(uid, highlights=moments, url=url, dur=dur)
        lines = [f"{i + 1}. {fmt_time(m['start'])}→{fmt_time(m['end'])} ⭐{m['score']} — {m['reason']}"
                 for i, m in enumerate(moments)]
        buttons = [[InlineKeyboardButton(f"✂️ #{i + 1} {fmt_time(m['start'])}→{fmt_time(m['end'])}",
                                         callback_data=f"hl:{i}")] for i, m in enumerate(moments)]
        buttons.append([InlineKeyboardButton("✂️ Cut all", callback_data="hl:all")])
        await msg.edit_text("🔥 Top moments:\n\n" + "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        log.exception("highlights")
        text = f"❌ {type(e).__name__}: {str(e)[:300]}"
        try:
            await msg.edit_text(text)
        except Exception:
            try:
                await update.effective_message.reply_text(text, reply_markup=main_kb())
            except Exception:
                log.exception("highlights: could not report error")


async def _cut_highlight(update, ctx, idx):
    uid = update.effective_user.id
    st = STATE.get(uid) or {}
    hl = st.get("highlights") or []
    if idx >= len(hl):
        return await _ack(update.callback_query, "Expired — dobara Auto Highlights chalao.", True)
    m = hl[idx]
    STATE[uid] = _new_state(uid, step="await_quality", url=st["url"], start=int(m["start"]),
                            end=int(m["end"]), dur=st.get("dur", 0), highlights=hl)
    await _show_preview(update, ctx)


async def _cut_all_highlights(update, ctx):
    uid = update.effective_user.id
    st = STATE.get(uid) or {}
    hl = st.get("highlights") or []
    if not hl:
        return await _ack(update.callback_query, "Expired — dobara Auto Highlights chalao.", True)
    await _edit(update.callback_query, f"⏳ {len(hl)} clips ban rahe hain…")
    chat_id = update.effective_chat.id
    for m in hl[:5]:
        job = _new_state(uid, url=st["url"], start=int(m["start"]), end=int(m["end"]))
        if await _execute_job(uid, chat_id, ctx.bot, job) == "limited":
            break
    await ctx.bot.send_message(chat_id, "✅ Highlights processed.", reply_markup=main_kb())


async def _run_chapters(update, ctx, url):
    uid = update.effective_user.id
    try:
        chapters = await get_chapters(url)
        if not chapters:
            return await update.message.reply_text("📑 No chapters in this video.", reply_markup=main_kb())
        STATE[uid] = _new_state(uid, chapters=chapters, url=url)
        buttons = [[InlineKeyboardButton(
            f"{c.get('title', '?')[:40]} ({fmt_time(c['start_time'])}→{fmt_time(c['end_time'])})",
            callback_data=f"chap:{i}")] for i, c in enumerate(chapters[:10])]
        await update.message.reply_text("📑 Tap a chapter:", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)[:300]}", reply_markup=main_kb())


async def _cut_chapter(update, ctx, idx):
    uid = update.effective_user.id
    st = STATE.get(uid) or {}
    ch = st.get("chapters") or []
    if idx >= len(ch):
        return await _ack(update.callback_query, "Expired — dobara Chapter Cut chalao.", True)
    c = ch[idx]
    STATE[uid] = _new_state(uid, step="await_quality", url=st["url"], start=int(c["start_time"]),
                            end=int(c["end_time"]), chapters=ch)
    await _show_preview(update, ctx)


async def _on_keyword(update, ctx):
    uid = update.effective_user.id
    st = STATE[uid]
    kw = update.message.text.strip().lower()
    if not kw:
        return
    msg = await update.message.reply_text(f"🔍 Searching '{kw}'… (pehli baar transcript banne me time lagta hai)")
    try:
        info = await fetch_info(st["url"])
        err = _analyze_error(info)
        if err:
            return await msg.edit_text(err)
        dur = info["duration"]
        trans = await _get_transcript(st["url"])
        matches = []
        for seg in trans.get("segments", []):
            if kw in seg["text"].lower():
                a, b = int(seg["start"]), int(seg["end"])
                if matches and a - matches[-1][1] <= 20:          # merge neighbours (±10 s windows overlap)
                    matches[-1] = (matches[-1][0], b, matches[-1][2])
                else:
                    matches.append((a, b, seg["text"].strip()))
        if not matches:
            return await msg.edit_text(f"❌ No '{kw}' found.")
        STATE[uid] = _new_state(uid, matches=matches, url=st["url"], dur=dur)
        buttons = [[InlineKeyboardButton(f"✂️ {fmt_time(a)} · {t[:28]}", callback_data=f"srch:{i}")]
                   for i, (a, b, t) in enumerate(matches[:10])]
        await msg.edit_text(f"🔍 {len(matches)} matches (tap = clip ±10 sec):",
                            reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        log.exception("search")
        await msg.edit_text(f"❌ {str(e)[:300]}")


async def _cut_search(update, ctx, idx):
    uid = update.effective_user.id
    st = STATE.get(uid) or {}
    ms = st.get("matches") or []
    if idx >= len(ms):
        return await _ack(update.callback_query, "Expired — dobara search karo.", True)
    a, b, _ = ms[idx]
    dur = st.get("dur") or 0
    end = b + 10
    if dur:
        end = min(end, dur)
    STATE[uid] = _new_state(uid, step="await_quality", url=st["url"], start=max(a - 10, 0), end=end,
                            dur=dur, matches=ms)
    await _show_preview(update, ctx)


async def _on_multi_ranges(update, ctx):
    uid = update.effective_user.id
    st = STATE[uid]
    ranges = []
    for line in (x.strip() for x in update.message.text.splitlines() if x.strip()):
        parts = re.split(r"[\s,]+", line)
        if len(parts) < 2:
            continue
        s, e = parse_time(parts[0]), parse_time(parts[1])
        if s is None or e is None or e <= s:
            continue
        ranges.append((s, e))
    if not ranges:
        return await update.message.reply_text("❌ No valid ranges. Format: `00:10 00:30`", parse_mode="Markdown")
    ranges = ranges[:MAX_MULTI]
    try:
        dur = (await fetch_info(st["url"])).get("duration") or 0
    except Exception as e:
        return await update.message.reply_text(f"❌ {str(e)[:300]}")
    if dur:
        bad = [r for r in ranges if r[1] > dur]
        ranges = [r for r in ranges if r[1] <= dur]
        if bad:
            await update.message.reply_text(f"⚠️ {len(bad)} range(s) video se bahar the ({fmt_time(dur)}) — skip kiye.")
        if not ranges:
            return
    STATE.pop(uid, None)
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"⏳ Processing {len(ranges)} clips…", reply_markup=main_kb())
    for s, e in ranges:
        job = _new_state(uid, url=st["url"], start=s, end=e)
        if await _execute_job(uid, chat_id, ctx.bot, job) == "limited":
            break


async def _run_full(update, ctx, url):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    msg = await update.message.reply_text("📥 Downloading full video…", reply_markup=main_kb())
    job_dir = TMP_DIR / uuid.uuid4().hex
    try:
        async with job_slot():
            path = await download_full(url, job_dir)
            await send_clip(ctx.bot, chat_id, uid, path, f"📥 Full video · {human_size(path.stat().st_size)}", "full")
        await msg.delete()
    except Exception as e:
        log.exception("full download")
        await msg.edit_text(f"❌ {str(e)[:300]}")
    # file stays (links stay valid); cleanup_loop removes old job folders later


async def _recut(update, ctx, hid):
    uid = update.effective_user.id
    row = get_history_by_id(hid)
    if not row or row["user_id"] != uid:
        return await _ack(update.callback_query, "Not found")
    if not extract_video_id(row["url"]):
        return await _ack(update.callback_query, "Upload kiya hua video dobara cut nahi ho sakta — video phir se bhejo.", True)
    STATE[uid] = _new_state(uid, step="await_quality", url=row["url"], start=row["start"],
                            end=row["end"], quality=row["quality"])
    await ctx.bot.send_message(update.effective_chat.id, "🔁 Re-cutting…")
    await _run_job(update, ctx, edit=False)


# ═════════════ post-clip actions ═════════════
async def _apply_speed(update, ctx, hid, speed):
    q = update.callback_query
    uid = update.effective_user.id
    row = get_history_by_id(hid)
    if speed not in (0.5, 1.5, 2.0) or not row or row["user_id"] != uid:
        return await _ack(q, "Not available")
    src = Path(row["file_path"])
    if src.suffix != ".mp4" or not src.exists():
        return await _ack(q, "File ab available nahi hai — Re-cut karo.", True)
    await _ack(q, f"⚡ Applying {speed}x…")
    chat_id = update.effective_chat.id
    dur = max(row["end"] - row["start"], 1)
    dst = src.with_name(f"{src.stem}_{speed}x.mp4")
    msg = await ctx.bot.send_message(chat_id, f"⚡ {speed}x queued…")
    bar = ProgressBar(msg, dur / speed)
    try:
        async with job_slot():
            await change_speed(src, dst, speed, bar, dur)
        await send_clip(ctx.bot, chat_id, uid, dst, f"⚡ {speed}x · {human_size(dst.stat().st_size)}", "speed")
        await msg.delete()
    except Exception as e:
        log.exception("speed")
        await bar.finish(f"❌ {str(e)[:200]}")


async def _send_srt(update, ctx, hid):
    q = update.callback_query
    row = get_history_by_id(hid)
    if not row or row["user_id"] != update.effective_user.id:
        return await _ack(q, "Not found")
    srt = Path(row["file_path"]).with_suffix(".srt")
    if not srt.exists():
        return await _ack(q, "Is clip ke liye subtitles nahi bane the (Settings me Subs ON karo).", True)
    await _ack(q)
    with open(srt, "rb") as f:
        await ctx.bot.send_document(update.effective_chat.id, InputFile(f, filename="subtitles.srt"))


async def _metadata(update, ctx, hid):
    q = update.callback_query
    uid = update.effective_user.id
    row = get_history_by_id(hid)
    if not row or row["user_id"] != uid:
        return await _ack(q, "Not found")
    if not llm.available():
        return await _ack(q, "LLM_API_KEY / GROQ_API_KEY set nahi hai.", True)
    await _ack(q, "📝 Generating…")
    path = Path(row["file_path"])
    txt = path.with_suffix(".txt")
    text = ""
    if txt.exists():
        text = txt.read_text(encoding="utf-8")
    elif path.exists():
        try:
            text = (await transcribe(path))["text"]
            txt.write_text(text, encoding="utf-8")
        except Exception as e:
            log.info("metadata transcript failed: %s", e)
    meta = await llm.generate_metadata(text or f"A clip from {row['url']}", get_user(uid).get("bot_lang", "en"))
    if not meta:
        return await ctx.bot.send_message(update.effective_chat.id, "❌ Metadata generate nahi ho paya.")
    tags = " ".join(str(t) for t in (meta.get("hashtags") or [])[:10])
    title, desc = str(meta.get("title", "")), str(meta.get("description", ""))
    markup = None
    if CopyTextButton:
        try:
            markup = InlineKeyboardMarkup([[InlineKeyboardButton(
                "📋 Copy", copy_text=CopyTextButton(f"{title}\n\n{desc}\n\n{tags}"[:256]))]])
        except Exception:
            markup = None
    await ctx.bot.send_message(update.effective_chat.id, f"📝 {title}\n\n{desc}\n\n{tags}", reply_markup=markup)


async def meta_callback(update, ctx):
    if not await _gate(update, ctx):
        return
    try:
        await _metadata(update, ctx, int(update.callback_query.data.split(":")[1]))
    except ValueError:
        await _ack(update.callback_query)


# ═════════════ admin ═════════════
async def admin_stats(update, ctx):
    if update.effective_user.id not in ADMIN_IDS:
        return
    g = global_stats()
    await update.message.reply_text(
        f"📊 Users: {g['users']} (banned {g['banned']})\n🎬 Clips: {g['clips']}\n🔗 Active links: {len(SHARED)}")


async def broadcast(update, ctx):
    import asyncio
    if update.effective_user.id not in ADMIN_IDS or not ctx.args:
        return
    text = " ".join(ctx.args)
    ids = all_user_ids()
    ok = 0
    for i in ids:
        try:
            await ctx.bot.send_message(i, f"📣 {text}")
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)               # stay under Telegram's flood limit
    await update.message.reply_text(f"📣 Sent to {ok}/{len(ids)}")


async def _ban(update, ctx, flag):
    if update.effective_user.id not in ADMIN_IDS or not ctx.args:
        return
    try:
        ban_user(int(ctx.args[0]), flag)
    except ValueError:
        return await update.message.reply_text("Usage: /ban <user_id>")
    await update.message.reply_text("✅ Banned" if flag else "✅ Unbanned")


async def ban(update, ctx):
    await _ban(update, ctx, True)


async def unban(update, ctx):
    await _ban(update, ctx, False)


async def auto_command(update,ctx):
    if not await _gate(update,ctx): return
    if ctx.args and extract_video_id(ctx.args[0]):
        return await run_auto(update,ctx,normalize_url(ctx.args[0]))
    STATE[update.effective_user.id]={"step":"await_auto_url"}
    await update.message.reply_text("🤖 YouTube VOD / finished live stream ka link bhejo.",reply_markup=CANCEL_KB)

async def allow_command(update,ctx):
    if update.effective_user.id not in ADMIN_IDS: return
    try: target=int(ctx.args[0]); allow_user(target,True)
    except Exception: return await update.message.reply_text("Usage: /allow <telegram_user_id>. User pehle /id bhej sakta hai.")
    await update.message.reply_text(f"✅ User {target} ko access de diya.")

async def remove_command(update,ctx):
    if update.effective_user.id not in ADMIN_IDS: return
    try: target=int(ctx.args[0]); allow_user(target,False)
    except Exception: return await update.message.reply_text("Usage: /remove <telegram_user_id>")
    await update.message.reply_text(f"✅ User {target} ka access hata diya.")

async def id_command(update,ctx):
    await update.message.reply_text(f"Aapka Telegram ID: {update.effective_user.id}")

async def _auto_settings_callback(update,ctx):
    q=update.callback_query; uid=update.effective_user.id; d=q.data
    cfg=get_autopost(uid)
    if d=="auto:settings": pass
    elif d=="auto:reset":
        set_autopost(uid,mode="approval",platforms=[],top_n=5,clip_length=40,weights={"heatmap":.4,"chat":.3,"llm":.3},hashtag_count=10,post_mode="queue")
    elif d=="auto:mode": set_autopost(uid,mode="auto" if cfg.get("mode")=="approval" else "approval")
    elif d=="auto:top": set_autopost(uid,top_n=(int(cfg.get("top_n",5))%10)+1)
    elif d=="auto:length": set_autopost(uid,clip_length=20 if int(cfg.get("clip_length",40))>=60 else int(cfg.get("clip_length",40))+10)
    elif d=="auto:hashtags": set_autopost(uid,hashtag_count=3 if int(cfg.get("hashtag_count",10))>=15 else int(cfg.get("hashtag_count",10))+2)
    elif d=="auto:postmode":
        opts=["now","queue","schedule"]; set_autopost(uid,post_mode=opts[(opts.index(cfg.get("post_mode","queue"))+1)%3])
    elif d=="auto:weights":
        opts=[{"heatmap":.4,"chat":.3,"llm":.3},{"heatmap":.5,"chat":.25,"llm":.25},{"heatmap":.25,"chat":.5,"llm":.25},{"heatmap":.25,"chat":.25,"llm":.5}]
        cur=cfg.get("weights",opts[0]); idx=next((i for i,x in enumerate(opts) if x==cur),0); set_autopost(uid,weights=opts[(idx+1)%len(opts)])
    elif d.startswith("auto:platform:"):
        platform=d.rsplit(":",1)[-1]; plats=list(cfg.get("platforms",[])); plats=plats+[platform] if platform not in plats else [x for x in plats if x!=platform]; set_autopost(uid,platforms=plats)
    await q.answer(); await q.edit_message_reply_markup(autopost_kb(get_autopost(uid)))


async def cookies_command(update, ctx):
    """Admin: send cookies.txt as a file with caption /cookies -> saved to MongoDB, no redeploy needed."""
    import cookiestore
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = update.message
    doc = msg.document
    if not doc:
        await msg.reply_text("🍪 cookies.txt file bhejo aur caption me /cookies likho.")
        return
    f = await doc.get_file()
    text = bytes(await f.download_as_bytearray()).decode("utf-8", "ignore")
    ok = cookiestore.replace(text)
    try:
        await msg.delete()                      # don't leave the cookies in the chat
    except Exception:
        pass
    await ctx.bot.send_message(msg.chat_id, "✅ Cookies update ho gayi (MongoDB me save)." if ok
                               else "❌ Ye valid YouTube cookies.txt (Netscape format) nahi lagti.")
