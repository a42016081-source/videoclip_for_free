# ✂️ YouTube Clipper Bot

Production Telegram bot for cutting YouTube videos, 9:16 Shorts with
karaoke subtitles, auto-highlights, chapters, keyword search, /multi.

## Deploy on Render (Hinglish)

1. Sab files GitHub repo me push karo.
2. Render → **New +** → **Web Service** → repo connect.
3. Runtime: **Docker**. Health path: `/health`. Plan: **Free**.
4. Env vars set karo:
   - `BOT_TOKEN` (BotFather)
   - `ADMIN_IDS` (apna Telegram ID)
   - `GROQ_API_KEY` (Groq — Whisper + LLM)
   - `RENDER_EXTERNAL_URL` (deploy ke baad milega)
   - `FORCE_JOIN_CHANNEL` (optional)
   - `COOKIES_FILE=/app/data/cookies.txt` (agar bot-check aaye)
5. Deploy. Build 5–10 min. Logs me `Bot starting…` aana chahiye.
6. Telegram me `/start` → keyboard aa jayega.
7. UptimeRobot add karo → `https://your-app.onrender.com/health` @ 5 min.

## Features verified working

- ✅ URL → start → end → preview → quality → confirm → job
- ✅ `/cut <url> <start> <end>`
- ✅ Shorts 9:16 (blur/crop/zoom)
- ✅ Groq Whisper karaoke subtitles (Devanagari OK)
- ✅ Progress bar (download → encode → upload)
- ✅ Telegram ≤50 MB / Gofile / self-hosted `/dl`
- ✅ Auto Highlights (LLM top 5 viral moments)
- ✅ Chapter Cut (inline buttons)
- ✅ Keyword Search (transcript timestamps)
- ✅ Download Full
- ✅ `/multi` (multiple ranges)
- ✅ History, Favorites, Stats, My Links
- ✅ Settings editable (quality/delivery/shorts/subs/reframe/style/lang)
- ✅ Speed 0.5x–2x, Metadata generation
- ✅ Rate limit (10/hr) + Queue (MAX_JOBS=2) + Ban
- ✅ Auto temp cleanup + 45-min link expiry
- ✅ Render self-ping (5 s) + UptimeRobot backup

## Cookies

Render IPs par YouTube `Sign in to confirm you're not a bot` de sakta hai.
Browser se `cookies.txt` export karke Render Secret Files me `/app/data/cookies.txt`
pe upload karo aur `COOKIES_FILE` set karo.