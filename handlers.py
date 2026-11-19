import asyncio, logging, uuid, re
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import TMP_DIR, ADMIN_IDS, FORCE_JOIN_CHANNEL
from database import *
from downloader import fetch_info, download_section, get_chapters, download_full
from transcriber import transcribe
from subtitles import build_srt
from editor import (cut_precise, make_shorts, burn_subs, audio_only,
                    change_speed, remove_silence, apply_watermark)
from delivery import send_clip
from progress import ProgressBar
from queue_manager import rate_limited, job_slot
from keyboards import (main_kb, CANCEL_KB, preview_kb, settings_kb,
                       reframe_kb, subs_kb, history_kb)
from utils import (parse_time, fmt_time, extract_video_id,
                   extract_t_from_url, normalize_url, cache_key, human_size)
import llm

log = logging.getLogger(__name__)
STATE: dict[int, dict] = {}

# ── Cleanup ─────────────────────────────────────────────
async def cleanup_task():
    from delivery import enforce_temp_limit, cleanup_links
    while True:
        await asyncio.sleep(600)
        try: enforce_temp_limit(); cleanup_links()
        except Exception: pass

# ── Basic commands ──────────────────────────────────────
async def start(update, ctx):
    uid = update.effective_user.id
    if is_banned(uid): return await update.message.reply_text("🚫 Banned.")
    get_user(uid)
    await update.message.reply_text(
        "👋 Send a YouTube link, or use a button.\n`/cut <url> <start> <end>` also works.",
        parse_mode="Markdown", reply_markup=main_kb())

async def help_cmd(update, ctx):
    await update.message.reply_text(
        "✂️ *YouTube Clipper Bot*\n\n"
        "Send a YouTube URL → pick start/end → confirm.\n"
        "`/cut <url> 00:10:30 00:12:00`\n\n"
        "📱 Shorts: 9:16 blur/crop/zoom\n"
        "💬 Subs: Groq Whisper karaoke\n"
        "📥 Delivery: ≤50 MB Telegram · else link\n"
        "🔥 Auto Highlights · 📑 Chapters · 🔍 Search · /multi",
        parse_mode="Markdown", reply_markup=main_kb())

async def ping(update, ctx): await update.message.reply_text("🏓 Pong")

async def cut_command(update, ctx):
    a = ctx.args
    if len(a) < 3:
        return await update.message.reply_text("Usage: `/cut <url> <start> <end>`", parse_mode="Markdown")
    s, e = parse_time(a[1]), parse_time(a[2])
    if s is None or e is None or e <= s:
        return await update.message.reply_text("❌ Bad times. SS / MM:SS / HH:MM:SS")
    STATE[update.effective_user.id] = {"step": "await_quality", "url": normalize_url(a[0]),
                                        "start": s, "end": e, "quality": "best", "mode": "cut"}
    await _show_preview(update, ctx)

# ── Message router (single entry) ───────────────────────
BUTTON_HANDLERS = {}

async def route_message(update, ctx):
    uid = update.effective_user.id
    if is_banned(uid): return
    text = update.message.text
    if text == "❌ Cancel":
        STATE.pop(uid, None)
        return await update.message.reply_text("❌ Cancelled.", reply_markup=main_kb())
    if text in BUTTON_HANDLERS:
        return await BUTTON_HANDLERS[text](update, ctx)
    st = STATE.get(uid, {})
    step = st.get("step")
    if step == "await_start": return await _on_start(update, ctx)
    if step == "await_end": return await _on_end(update, ctx)
    if step == "await_keyword": return await _on_keyword(update, ctx)
    if step == "await_multi_ranges": return await _on_multi_ranges(update, ctx)
    return await _on_url(update, ctx)

# ── URL / time flow ─────────────────────────────────────
async def _on_url(update, ctx):
    text = update.message.text.strip()
    url = normalize_url(text)
    if not extract_video_id(url):
        return await update.message.reply_text(
            "❌ Send a valid YouTube link or use a button.", reply_markup=main_kb())
    uid = update.effective_user.id
    st = STATE.setdefault(uid, {"mode": "cut", "quality": "best"})
    st["url"] = url
    mode = st.get("mode", "cut")
    if mode in ("cut", "short"):
        st["start"] = extract_t_from_url(text) or 0
        st["step"] = "await_start"
        STATE[uid] = st
        await update.message.reply_text(
            f"Start time? (default `{fmt_time(st['start'])}` — send `-` to keep)",
            parse_mode="Markdown", reply_markup=CANCEL_KB)
    elif mode == "highlights": await _run_highlights(update, ctx, url)
    elif mode == "chapters": await _run_chapters(update, ctx, url)
    elif mode == "search":
        st["step"] = "await_keyword"; STATE[uid] = st
        await update.message.reply_text("🔍 Send keyword to search.", reply_markup=CANCEL_KB)
    elif mode == "full": await _run_full(update, ctx, url)
    elif mode == "multi":
        st["step"] = "await_multi_ranges"; STATE[uid] = st
        await update.message.reply_text(
            "Send ranges, one per line:\n`00:10 00:30`\n`01:00 01:45`",
            parse_mode="Markdown", reply_markup=CANCEL_KB)

async def _on_start(update, ctx):
    uid = update.effective_user.id; st = STATE[uid]
    text = update.message.text.strip()
    if text and text != "-":
        v = parse_time(text)
        if v is None: return await update.message.reply_text("❌ Bad format.")
        st["start"] = v
    st["step"] = "await_end"; STATE[uid] = st
    await update.message.reply_text(f"✅ Start: `{fmt_time(st['start'])}`\nEnd time?",
                                    parse_mode="Markdown", reply_markup=CANCEL_KB)

async def _on_end(update, ctx):
    uid = update.effective_user.id; st = STATE[uid]
    v = parse_time(update.message.text.strip())
    if v is None: return await update.message.reply_text("❌ Bad format.")
    if v <= st["start"]: return await update.message.reply_text("❌ End must be > start.")
    st["end"] = v; st["step"] = "await_quality"; STATE[uid] = st
    await _show_preview(update, ctx)

async def _show_preview(update, ctx):
    uid = update.effective_user.id; st = STATE[uid]
    try: info = await fetch_info(st["url"])
    except Exception as e: return await update.message.reply_text(f"❌ {str(e)[:300]}")
    dur = info.get("duration") or 0
    if st["end"] > dur:
        return await update.message.reply_text(f"❌ End > duration {fmt_time(dur)}")
    title = (info.get("title") or "?")[:80]
    thumb = info.get("thumbnail")
    user = get_user(uid)
    text = (f"🎬 *{title}*\n⏱ {fmt_time(st['start'])} → {fmt_time(st['end'])} "
            f"({fmt_time(st['end'] - st['start'])})\n\nChoose quality & start:")
    kb = preview_kb(st.get("quality", "best"), user)
    if thumb:
        await update.message.reply_photo(thumb, caption=text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

# ── Callback router ─────────────────────────────────────
async def route_callback(update, ctx):
    q = update.callback_query; await q.answer()
    d = q.data; uid = update.effective_user.id
    if d.startswith("q:"):
        st = STATE.get(uid)
        if st: st["quality"] = d[2:]; STATE[uid] = st
        user = get_user(uid)
        await q.edit_message_reply_markup(preview_kb(d[2:], user))
    elif d.startswith("toggle:"):
        key = d[7:]; user = get_user(uid)
        set_user(uid, **{key: 0 if user[key] else 1})
        user = get_user(uid)
        try: await q.edit_message_reply_markup(preview_kb(STATE.get(uid, {}).get("quality", "best"), user))
        except Exception: pass
    elif d == "conf:start": await _run_job(update, ctx)
    elif d == "conf:cancel":
        STATE.pop(uid, None)
        try: await q.edit_message_text("❌ Cancelled.")
        except Exception: pass
    elif d.startswith("set:"):
        _, key, val = d.split(":")
        await _apply_setting(update, ctx, key, val)
    elif d.startswith("chap:"):
        await _cut_chapter(update, ctx, int(d[5:]))
    elif d.startswith("hl:"):
        await _cut_highlight(update, ctx, int(d[3:]))
    elif d == "hl:all": await _cut_all_highlights(update, ctx)
    elif d.startswith("re:"): await _recut(update, ctx, int(d[3:]))
    elif d.startswith("fav:"):
        hid = int(d[4:])
        if is_favorite(uid, hid): remove_favorite(uid, hid)
        else: add_favorite(uid, hid)
        await q.answer("Updated", show_alert=False)
    elif d.startswith("spd:"):
        await _apply_speed(update, ctx, float(d[4:]))
    elif d.startswith("srch:"):
        await _cut_search(update, ctx, int(d[5:]))

async def _apply_setting(update, ctx, key, val):
    q = update.callback_query; uid = update.effective_user.id; user = get_user(uid)
    cycle = {
        "quality": ["360", "480", "720", "1080", "best"],
        "delivery": ["auto", "telegram", "link"],
        "reframe": ["blur", "crop", "zoom"],
        "sub_style": ["classic", "bold", "minimal"],
        "sub_lang": ["auto", "hi", "en"],
        "bot_lang": ["en", "hinglish"],
    }
    if val == "cycle":
        opts = cycle.get(key, ["?"]); cur = user.get(key, opts[0])
        idx = opts.index(cur) if cur in opts else -1
        val = opts[(idx + 1) % len(opts)]
    set_user(uid, **{key: val})
    user = get_user(uid)
    try: await q.edit_message_reply_markup(settings_kb(user))
    except Exception: pass

# ── Job execution ───────────────────────────────────────
async def _run_job(update, ctx):
    uid = update.effective_user.id; st = STATE.get(uid)
    if not st or st.get("step") != "await_quality":
        if update.callback_query: await update.callback_query.edit_message_text("Nothing to run.")
        return
    if rate_limited(uid):
        msg = "⏳ Rate limit: 10 clips/hour. Try later."
        if update.callback_query: await update.callback_query.edit_message_text(msg)
        return
    user = get_user(uid)
    url, s, e = st["url"], st["start"], st["end"]
    quality = st.get("quality", "best")
    shorts = bool(user["shorts"]) or st.get("force_shorts")
    subs = bool(user["subs"])
    style, reframe = user["sub_style"], user["reframe"]

    ck = cache_key(url, s, e, quality, shorts, subs, style)
    cached = cache_get(ck)
    if cached:
        await send_clip(update, ctx, Path(cached),
                        f"♻️ Cached · {human_size(Path(cached).stat().st_size)}", quality)
        STATE.pop(uid, None); return

    chat_id = update.effective_chat.id
    msg = await ctx.bot.send_message(chat_id, "⏳ Queued…")
    bar = ProgressBar(msg, e - s)

    async with job_slot():
        job_dir = TMP_DIR / uuid.uuid4().hex; job_dir.mkdir(parents=True, exist_ok=True)
        try:
            async def pcb(c, s_): await bar.update(current=c, stage=s_)
            src = await download_section(url, s, e, quality, job_dir, progress_cb=pcb)
            await bar.update(stage="Downloaded")

            if quality == "audio":
                mp3 = job_dir / "audio.mp3"; await audio_only(src, mp3)
                await send_clip(update, ctx, mp3, f"🎵 Audio · {human_size(mp3.stat().st_size)}", "MP3")
                add_history(uid, url, s, e, quality, str(mp3))
                await bar.finish("✅ Done"); STATE.pop(uid, None)
                await ctx.bot.send_message(chat_id, "Choose next:", reply_markup=main_kb())
                return

            cut = job_dir / "cut.mp4"
            await cut_precise(src, cut, s, e, bar)
            out = cut

            if subs:
                trans = await transcribe(cut)
                words = trans.get("words") or []
                if words:
                    subbed = job_dir / "subbed.mp4"
                    await burn_subs(cut, subbed, words, style, s, bar, e - s)
                    out = subbed

            if shorts:
                short = job_dir / "short.mp4"
                await make_shorts(out, short, reframe, bar, e - s)
                out = short

            await bar.update(stage="Uploading")
            size = human_size(out.stat().st_size)
            await send_clip(update, ctx, out,
                            f"✅ {quality} · {size}\n⏱ {fmt_time(e - s)}", quality)
            cache_put(ck, str(out))
            hid = add_history(uid, url, s, e, quality, str(out))
            await bar.finish("✅ Done")
            await ctx.bot.send_message(chat_id,
                f"⭐ Save? / Get metadata?",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⭐ Favorite", callback_data=f"fav:{hid}"),
                    InlineKeyboardButton("📋 Metadata", callback_data=f"meta:{hid}"),
                ]]), reply_markup_force_reply=False)
            await ctx.bot.send_message(chat_id, "Choose next:", reply_markup=main_kb())
            STATE.pop(uid, None)
        except Exception as e:
            log.exception("job")
            await bar.finish(f"❌ Failed: {str(e)[:300]}")
            await ctx.bot.send_message(chat_id, "Try again.", reply_markup=main_kb())
            STATE.pop(uid, None)

# ── Shortcuts ───────────────────────────────────────────
async def _btn_cut(u, c):
    STATE[u.effective_user.id] = {"step": "await_start", "mode": "cut", "quality": "best"}
    await u.message.reply_text("✂️ Send the YouTube link.", reply_markup=CANCEL_KB)

async def _btn_audio(u, c):
    STATE[u.effective_user.id] = {"step": "await_start", "mode": "cut", "quality": "audio"}
    await u.message.reply_text("🎵 Send the YouTube link.", reply_markup=CANCEL_KB)

async def _btn_short(u, c):
    STATE[u.effective_user.id] = {"step": "await_start", "mode": "short",
                                   "quality": "best", "force_shorts": True}
    await u.message.reply_text("📱 Shorts ON. Send the YouTube link.", reply_markup=CANCEL_KB)

async def _btn_live(u, c):
    STATE[u.effective_user.id] = {"step": "await_start", "mode": "cut", "quality": "best"}
    await u.message.reply_text("🔴 Send live/VOD link.", reply_markup=CANCEL_KB)

async def _btn_highlights(u, c):
    STATE[u.effective_user.id] = {"step": "await_start", "mode": "highlights"}
    await u.message.reply_text("🔥 Send link — I'll find 5 viral moments.", reply_markup=CANCEL_KB)

async def _btn_chapters(u, c):
    STATE[u.effective_user.id] = {"step": "await_start", "mode": "chapters"}
    await u.message.reply_text("📑 Send link — I'll list chapters.", reply_markup=CANCEL_KB)

async def _btn_search(u, c):
    STATE[u.effective_user.id] = {"step": "await_start", "mode": "search"}
    await u.message.reply_text("🔍 Send link first.", reply_markup=CANCEL_KB)

async def _btn_full(u, c):
    STATE[u.effective_user.id] = {"step": "await_start", "mode": "full"}
    await u.message.reply_text("📥 Send link — full download.", reply_markup=CANCEL_KB)

async def _btn_history(u, c):
    rows = get_history(u.effective_user.id)
    if not rows: return await u.message.reply_text("No history yet.")
    lines = [f"• {fmt_time(r['start'])}–{fmt_time(r['end'])} · {r['quality']}" for r in rows[:10]]
    await u.message.reply_text("📜 *History*\n" + "\n".join(lines),
                                parse_mode="Markdown", reply_markup=history_kb(rows))

async def _btn_favorites(u, c):
    rows = get_favorites(u.effective_user.id)
    if not rows: return await u.message.reply_text("No favorites yet.")
    lines = [f"• {fmt_time(r['start'])}–{fmt_time(r['end'])} · {r['quality']}" for r in rows[:10]]
    await u.message.reply_text("⭐ *Favorites*\n" + "\n".join(lines),
                                parse_mode="Markdown", reply_markup=history_kb(rows))

async def _btn_links(u, c):
    from delivery import SHARED
    if not SHARED: return await u.message.reply_text("No active links.")
    await u.message.reply_text(f"🔗 Active links: {len(SHARED)} (expire in 45 min)")

async def _btn_settings(u, c):
    user = get_user(u.effective_user.id)
    await u.message.reply_text("⚙️ Settings — tap to change:", reply_markup=settings_kb(user))

async def _btn_stats(u, c):
    s = get_stats(u.effective_user.id)
    await u.message.reply_text(f"📊 Clips: *{s['clips']}*\nLast: {s['last_clip'] or '—'}",
                                parse_mode="Markdown")

BUTTON_HANDLERS.update({
    "✂️ Cut Video": _btn_cut, "🎵 Audio Only": _btn_audio,
    "📱 Make Short (9:16)": _btn_short, "🔴 Live Stream Clip": _btn_live,
    "🔥 Auto Highlights": _btn_highlights, "📑 Chapter Cut": _btn_chapters,
    "🔍 Keyword Search": _btn_search, "📥 Download Full": _btn_full,
    "📜 My History": _btn_history, "⭐ Favorites": _btn_favorites,
    "🔗 My Links": _btn_links, "⚙️ Settings": _btn_settings,
    "📊 My Stats": _btn_stats, "❓ Help": lambda u, c: help_cmd(u, c),
    "🏓 Ping/Status": lambda u, c: ping(u, c),
})

# ── Highlights / Chapters / Search / Full ───────────────
async def _run_highlights(update, ctx, url):
    uid = update.effective_user.id
    msg = await update.message.reply_text("🔥 Analyzing transcript…")
    try:
        info = await fetch_info(url); dur = info.get("duration") or 60
        job_dir = TMP_DIR / uuid.uuid4().hex; job_dir.mkdir(parents=True, exist_ok=True)
        src = await download_section(url, 0, min(dur, 60), "best", job_dir)
        trans = await transcribe(src)
        text = " ".join(s["text"] for s in trans.get("segments", []))
        moments = await llm.find_highlights(text)
        if not moments: return await msg.edit_text("❌ No highlights found.")
        STATE[uid] = {"highlights": moments, "url": url}
        buttons = [[InlineKeyboardButton(
            f"✂️ {fmt_time(m['start'])}→{fmt_time(m['end'])} ⭐{m.get('score', 0)}",
            callback_data=f"hl:{i}")] for i, m in enumerate(moments[:5])]
        buttons.append([InlineKeyboardButton("✂️ Cut all", callback_data="hl:all")])
        await msg.edit_text("🔥 Top moments:", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await msg.edit_text(f"❌ {str(e)[:300]}")

async def _cut_highlight(update, ctx, idx):
    uid = update.effective_user.id
    st = STATE.get(uid) or {}
    hl = st.get("highlights") or []
    if idx >= len(hl): return
    m = hl[idx]
    STATE[uid] = {"step": "await_quality", "url": st["url"],
                  "start": int(m["start"]), "end": int(m["end"]),
                  "quality": "best", "mode": "cut"}
    await update.callback_query.message.edit_text("Preparing…")
    await _show_preview(update, ctx)

async def _cut_all_highlights(update, ctx):
    uid = update.effective_user.id; st = STATE.get(uid) or {}
    hl = st.get("highlights") or []
    for m in hl[:5]:
        STATE[uid] = {"step": "await_quality", "url": st["url"],
                      "start": int(m["start"]), "end": int(m["end"]),
                      "quality": "best", "mode": "cut"}
        await ctx.bot.send_message(uid, f"Job: {fmt_time(m['start'])}→{fmt_time(m['end'])}")
        await _run_job_from_state(uid, ctx)
    await ctx.bot.send_message(uid, "✅ All highlights processed.", reply_markup=main_kb())

async def _run_job_from_state(uid, ctx):
    st = STATE.get(uid)
    if not st: return
    # Reuse _run_job logic without update — minimal inline
    from telegram import Update
    fake = type("U", (), {"effective_user": type("X", (), {"id": uid}),
                          "effective_chat": type("C", (), {"id": uid})})()
    await _run_job(fake, ctx)

async def _run_chapters(update, ctx, url):
    try:
        chapters = await get_chapters(url)
        if not chapters: return await update.message.reply_text("📑 No chapters in this video.")
        STATE[update.effective_user.id] = {"chapters": chapters, "url": url}
        buttons = [[InlineKeyboardButton(
            f"{c.get('title', '?')[:40]} ({fmt_time(c['start_time'])}→{fmt_time(c['end_time'])})",
            callback_data=f"chap:{i}")] for i, c in enumerate(chapters[:10])]
        await update.message.reply_text("📑 Tap a chapter:",
                                        reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)[:300]}")

async def _cut_chapter(update, ctx, idx):
    uid = update.effective_user.id; st = STATE.get(uid) or {}
    ch = st.get("chapters") or []
    if idx >= len(ch): return
    c = ch[idx]
    STATE[uid] = {"step": "await_quality", "url": st["url"],
                  "start": int(c["start_time"]), "end": int(c["end_time"]),
                  "quality": "best", "mode": "cut"}
    await update.callback_query.message.edit_text("Preparing…")
    await _show_preview(update, ctx)

async def _on_keyword(update, ctx):
    uid = update.effective_user.id; st = STATE[uid]
    kw = update.message.text.strip().lower()
    msg = await update.message.reply_text(f"🔍 Searching '{kw}'…")
    try:
        info = await fetch_info(st["url"]); dur = info.get("duration") or 60
        job_dir = TMP_DIR / uuid.uuid4().hex; job_dir.mkdir(parents=True, exist_ok=True)
        src = await download_section(st["url"], 0, min(dur, 60), "best", job_dir)
        trans = await transcribe(src)
        matches = []
        for seg in trans.get("segments", []):
            if kw in seg["text"].lower():
                matches.append((int(seg["start"]), int(seg["end"])))
        if not matches: return await msg.edit_text(f"❌ No '{kw}' found.")
        STATE[uid] = {"matches": matches, "url": st["url"], "mode": "cut"}
        buttons = [[InlineKeyboardButton(f"✂️ {fmt_time(a)}→{fmt_time(b)}",
                    callback_data=f"srch:{i}")] for i, (a, b) in enumerate(matches[:10])]
        await msg.edit_text(f"🔍 {len(matches)} matches:",
                            reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await msg.edit_text(f"❌ {str(e)[:300]}")

async def _cut_search(update, ctx, idx):
    uid = update.effective_user.id; st = STATE.get(uid) or {}
    ms = st.get("matches") or []
    if idx >= len(ms): return
    a, b = ms[idx]
    STATE[uid] = {"step": "await_quality", "url": st["url"],
                  "start": max(a - 10, 0), "end": b + 10,
                  "quality": "best", "mode": "cut"}
    await update.callback_query.message.edit_text("Preparing…")
    await _show_preview(update, ctx)

async def _on_multi_ranges(update, ctx):
    uid = update.effective_user.id; st = STATE[uid]
    lines = [l.strip() for l in update.message.text.splitlines() if l.strip()]
    ranges = []
    for line in lines:
        parts = re.split(r"[\s,]+", line)
        if len(parts) < 2: continue
        s, e = parse_time(parts[0]), parse_time(parts[1])
        if s is None or e is None or e <= s: continue
        ranges.append((s, e))
    if not ranges: return await update.message.reply_text("❌ No valid ranges.")
    await update.message.reply_text(f"⏳ Processing {len(ranges)} clips…", reply_markup=main_kb())
    for s, e in ranges:
        STATE[uid] = {"step": "await_quality", "url": st["url"], "start": s, "end": e,
                      "quality": "best", "mode": "cut"}
        await _run_job_from_state(uid, ctx)
    STATE.pop(uid, None)

async def _run_full(update, ctx, url):
    uid = update.effective_user.id
    msg = await update.message.reply_text("📥 Downloading full video…")
    try:
        job_dir = TMP_DIR / uuid.uuid4().hex
        path = await download_full(url, job_dir)
        await send_clip(update, ctx, path, f"📥 Full · {human_size(path.stat().st_size)}", "full")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ {str(e)[:300]}")

async def _recut(update, ctx, hid):
    row = get_history_by_id(hid)
    if not row: return
    uid = update.effective_user.id
    STATE[uid] = {"step": "await_quality", "url": row["url"],
                  "start": row["start"], "end": row["end"],
                  "quality": row["quality"], "mode": "cut"}
    await update.callback_query.message.edit_text("Re-cutting…")
    await _run_job(update, ctx)

async def _apply_speed(update, ctx, speed):
    # Applies to last clip in history
    uid = update.effective_user.id
    rows = get_history(uid, 1)
    if not rows: return await update.callback_query.answer("No recent clip")
    src = Path(rows[0]["file_path"])
    if not src.exists(): return await update.callback_query.answer("File deleted")
    dst = src.with_name(src.stem + f"_{speed}x.mp4")
    msg = await ctx.bot.send_message(uid, f"⚡ Applying {speed}x…")
    bar = ProgressBar(msg, 60)
    await change_speed(src, dst, speed, bar, 60)
    await send_clip(update, ctx, dst, f"⚡ {speed}x", "speed")
    await msg.delete()

async def _metadata(update, ctx, hid):
    row = get_history_by_id(hid)
    if not row: return
    trans_text = f"Clip {row['start']}-{row['end']} of {row['url']}"
    meta = await llm.generate_metadata(trans_text)
    if not meta: return await update.callback_query.answer("Metadata unavailable")
    text = f"📝 *{meta.get('title', '')}*\n\n{meta.get('description', '')}\n\n{' '.join(meta.get('hashtags', []))}"
    await update.callback_query.message.reply_text(text, parse_mode="Markdown")

# ── Admin ───────────────────────────────────────────────
async def admin_stats(update, ctx):
    if update.effective_user.id not in ADMIN_IDS: return
    await update.message.reply_text("📊 Admin online.")

async def broadcast(update, ctx):
    if update.effective_user.id not in ADMIN_IDS: return
    if not ctx.args: return
    text = " ".join(ctx.args)
    from database import _conn
    with _conn() as c:
        ids = [r["user_id"] for r in c.execute("SELECT user_id FROM users").fetchall()]
    ok = 0
    for i in ids:
        try: await ctx.bot.send_message(i, f"📣 {text}"); ok += 1
        except Exception: pass
    await update.message.reply_text(f"📣 Sent to {ok}/{len(ids)}")

async def ban(update, ctx):
    if update.effective_user.id not in ADMIN_IDS or not ctx.args: return
    ban_user(int(ctx.args[0]), True); await update.message.reply_text("✅ Banned")

async def unban(update, ctx):
    if update.effective_user.id not in ADMIN_IDS or not ctx.args: return
    ban_user(int(ctx.args[0]), False); await update.message.reply_text("✅ Unbanned")