# ✂️ YouTube Clipper Bot

Telegram bot: YouTube link → cut any time range (only that part is downloaded) → optional
9:16 Short with karaoke subtitles → delivered as a Telegram file or a download link.

## Deploy on Render (Hinglish)

1. Sab files GitHub repo me push karo (`.env` push mat karna — `.gitignore` me hai).
2. Render → **New +** → **Web Service** → repo connect.
3. Runtime: **Docker**. Health path: `/health`. Plan: **Free**.
4. Env vars set karo:
   - `BOT_TOKEN` (BotFather)
   - `ADMIN_IDS` (apna Telegram ID, comma se alag)
   - `GROQ_API_KEY` (Groq — Whisper + LLM dono ke liye)
   - `RENDER_EXTERNAL_URL` (deploy ke baad milega, e.g. `https://xyz.onrender.com`; phir redeploy)
   - `FORCE_JOIN_CHANNEL` (optional, e.g. `@mychannel` — bot ko channel me admin banao)
   - `COOKIES_FILE=/etc/secrets/cookies.txt` (agar bot-check aaye, neeche "Cookies" dekho)
5. Deploy. Build 5–10 min. Logs me `Bot starting…` aur `Bot is running.` aana chahiye.
6. Telegram me `/start` → keyboard aa jayega.
7. UptimeRobot add karo → `https://your-app.onrender.com/health` @ 5 min (backup pinger).

> **Note:** Render Free plan par disk persistent nahi hota — redeploy/restart par History, Favorites,
> Settings (`bot.db`) reset ho jate hain. Free instance ~512 MB RAM wala hai: Shorts + subtitles
> ke liye `MAX_JOBS=1` rakhna safe hai.

## Commands & menu

`/start`, `/cut <url> <start> <end>`, `/multi [url]`, `/help`, `/ping`
Admin: `/stats`, `/broadcast <text>`, `/ban <id>`, `/unban <id>`
Saare features reply-keyboard buttons se bhi milte hain.

## Features

- URL → start → end → preview → quality → confirm → job; `/cut`; `?t=` in link = default start
- Only the requested section is downloaded (`--download-sections`), then **one** ffmpeg pass
- Shorts 9:16 (blur / crop / zoom), 1080×1920, 30 fps; warning if > 60 s
- Subtitles: Groq Whisper (fallback faster-whisper), word-level karaoke `.ass`, 3 styles,
  placed in the Shorts safe zone, Devanagari font in the image; `.srt` export button
- Progress message (stage, %, ETA), queue position
- Delivery: Telegram ≤ 50 MB (≤ 2 GB with `LOCAL_BOT_API_URL`) / Gofile / self-hosted `/dl/<token>` (45 min)
- Auto Highlights (whole video transcribed in chunks → LLM → top 5 with timestamps), Chapter Cut,
  Keyword Search (±10 s), Download Full, `/multi`
- After every clip: ⭐ Favorite, 📋 Metadata (title/description/10 hashtags), speed 0.5x / 1.5x / 2x
- History (last 20) with Re-cut, Favorites, My Links, Stats
- Settings: quality, delivery, Shorts, subs, reframe style, sub style, sub language, bot language
- Rate limit 10 clips/hour, queue `MAX_JOBS`, ban/unban, force-join channel
- Result cache, temp cleanup (intermediates deleted after each job, old folders after 2 h, `MAX_TEMP_MB`)

### Not implemented yet

Translated subtitles (Hindi ↔ English), Hinglish (romanised) transcription, face-tracking crop,
mute / remove-silence / background-music buttons, GIF/loop output, watermark toggle,
progress bar drawn on the video, download speed in the progress line.

## Cookies

Render IPs par YouTube `Sign in to confirm you're not a bot` de sakta hai.
Browser se `cookies.txt` export karo → Render → **Environment → Secret Files** me
filename `cookies.txt` se upload karo (wo `/etc/secrets/cookies.txt` par milta hai) →
env var `COOKIES_FILE=/etc/secrets/cookies.txt` set karo.
(Bot is file ki writable copy `data/cookies.txt` me bana ke use karta hai.)
