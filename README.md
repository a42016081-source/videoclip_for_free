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
   - `MONGODB_URI` (Atlas — settings/history ko restart ke baad rakhne ke liye)
   - `ADMIN_IDS` me apna numeric Telegram user ID rakho; doosron ko `/allow <id>` se add karo
   - `RENDER_EXTERNAL_URL` (deploy ke baad milega, e.g. `https://xyz.onrender.com`; phir redeploy)
   - `FORCE_JOIN_CHANNEL` (optional, e.g. `@mychannel` — bot ko channel me admin banao)
   - `COOKIES_FILE=/etc/secrets/cookies.txt` (agar bot-check aaye, neeche "Cookies" dekho)
5. Deploy. Build 5–10 min. Logs me `Bot starting…` aur `Bot is running.` aana chahiye.
6. Telegram me `/start` → keyboard aa jayega.
7. UptimeRobot add karo → `https://your-app.onrender.com/health` @ 5 min (backup pinger).

> **Note:** Render Free plan par disk persistent nahi hota — redeploy/restart par History, Favorites,
> Settings (`bot.db`) reset ho jate hain. Free instance ~512 MB RAM wala hai: Shorts + subtitles
> ke liye `MAX_JOBS=1` rakhna safe hai.

## 🎨 Subtitle Studio

Menu button **🎨 Subtitle Studio** (also inside ⚙️ Settings). Every option is a button; `auto` = the style's own default.
`👁 Preview` sends a sample image, `♻️ Reset` clears your overrides.

**11 styles:** Classic, Bold Yellow, Minimal, Hormozi (active word highlighted, UPPERCASE), Pop (one big word at a time),
Marker box (active word on a coloured marker), Neon glow, Karaoke fill (smooth colour sweep), Comic fun, News bar,
**Typography (auto colours)** — words pop in one by one, important words (numbers, long words, `!`/`?`) are bigger and coloured, small words stay white.

**Options:** Font · Size (S/M/L/XL) · Position (top / middle / safe-above-Shorts-UI / bottom) · Words per line (1-6) ·
Case (Normal / UPPER / lower / Title) · Highlight colour · Text colour · Outline (thin → extra thick) ·
Background (none / shadow / dark box) · Animation (none / pop-in / fade-in) · Subs ON/OFF · Shorts ON/OFF.

**Fonts:** Noto Sans always works. The Dockerfile also installs Montserrat, Poppins, Bebas Neue, Anton, Comic Neue, Oswald
(best effort). On GitHub Actions / VPS without Docker: put any `.ttf` / `.otf` files in a `fonts/` folder next to `bot.py`
(e.g. Anton, Montserrat-ExtraBold, Poppins-Bold) — they appear in the font list automatically
(recognised names: Noto Sans, Montserrat, Poppins, Anton, Bebas Neue, Oswald, Comic Neue, Comic Relief, Archivo Black, Noto Serif).
Subtitles need word timestamps (Groq Whisper) for the word-highlight styles.

## 🎬 Auto colours (colours picked from the video)

Highlight colour option **🎬 video** (Subtitle Studio → 🌈 Highlight → `video`); the *Typography* style has it ON by default.
ffmpeg takes a tiny thumbnail every 3 s, finds the dominant hue and builds a palette of bright colours that contrast with
it (complementary + triadic hues). Subtitles use the palette of the scene they appear in, so colours change scene by scene.
Works for every style, no extra Python packages. Pick any fixed colour in the Studio to switch it off.

## 🖥 16:9 and 📤 Upload your own video

* **YouTube clips:** Shorts **OFF** = 16:9 (1920x1080 type size), Shorts **ON** = 9:16. Typography works in both.
* **📤 Upload Video + Subs** (menu button or `/upload`, or just send a video): choose **16:9 / 9:16 / Original**, the style
  (default Typography) and *Auto colours from video* ON/OFF → ✅. 16:9 of a non-16:9 video = fitted + blurred side bars.
  Output keeps `.srt` export and speed buttons.
* **Telegram limit:** a bot can download at most **20 MB** from Telegram (cloud Bot API). Send it as a normal *video*
  (Telegram compresses it) or set `LOCAL_BOT_API_URL` for files up to 2 GB. `MAX_UPLOAD_MIN` (default 20) = longest upload.

## Commands & menu

`/start`, `/cut <url> <start> <end>`, `/multi [url]`, `/upload`, `/help`, `/ping`
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
- Auto Highlights (whole video transcribed in chunks → LLM → top 5 with timestamps), Viral Score + `/auto` approval/post pipeline, Chapter Cut,
  Keyword Search (±10 s), Download Full, `/multi`
- After every clip: ⭐ Favorite, 📋 Metadata (title/description/10 hashtags), speed 0.5x / 1.5x / 2x
- History (last 20) with Re-cut, Favorites, My Links, Stats
- Settings: quality, delivery, Shorts, subs, reframe style, sub style, sub language, bot language
- Rate limit 10 clips/hour, queue `MAX_JOBS=1`, ban/unban, owner-managed allowlist, force-join channel
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

## Private access (owner allowlist)

Set `ADMIN_IDS` to your Telegram numeric user ID(s). IDs in this setting are the bot owner(s) and can manage access.
An unapproved person can send `/id` to learn their own ID, but cannot use bot features until an owner runs
`/allow <telegram_user_id>`. Remove access with `/remove <telegram_user_id>`. `/ban` remains a separate temporary block.
Keep `ADMIN_IDS` private; if it is empty, no one can administer access.

## Persistent MongoDB

Set `MONGODB_URI` to a MongoDB Atlas connection string and optionally `MONGODB_DB` (defaults to `clipbot`).
The bot creates its indexes automatically. If MongoDB is unavailable or the URI is unset, it uses `data/bot.db`
and warns that local settings/history will reset when an ephemeral runner is replaced. To import that old local DB once,
set `MONGODB_URI` and run `python migrate_sqlite_to_mongo.py` before switching the bot over.
Never paste secrets into source files or commit `.env`.

## Viral clips and Auto Post

Use `/auto <YouTube URL>` or **🤖 Auto Post**. The bot combines the YouTube replay heatmap, chat replay activity
(when `chat-downloader` can read it), and transcript ratings from Groq. The YouTube Data API lookup uses only
`videos.list`; `YOUTUBE_API_KEY` provides duration and live status. A live stream is rejected until it ends.
Settings include approval/auto mode, connected YouTube/Instagram/Facebook platforms, top count, clip duration,
signal weights, hashtag count, and now/queue/schedule delivery. Approval is the default; a clip sent for approval has
Post, Edit title, and Skip buttons. Schedule mode asks for an ISO-8601 date and time after Post is selected.

Set `SHAPELESSAI_API_KEY` with both read and publish scopes (connections/cap require read; creating posts requires publish) and connect the social accounts in ShapelessAI first.
The publisher uses ShapelessAI's documented `GET /api/connections`, `PUT /api/studio/attachments/{filename}`
(to receive a media key), and `POST /api/posts` routes. ShapelessAI enforces the Free plan's daily post allowance;
the bot also limits its API calls to 600 per hour. Posting failures are shown in Telegram and the clip remains there.

## GitHub Actions

The workflow serializes runs to prevent Telegram polling conflicts, uses a 350-minute timeout, and handles SIGTERM
through the existing shutdown path. GitHub cron ticks every 10 minutes and a MongoDB-backed atomic gate admits exactly one scheduled start after 350 minutes; workflow concurrency prevents overlap. Configure repository secrets:
`BOT_TOKEN`, `ADMIN_IDS`, `GROQ_API_KEY`, `YOUTUBE_API_KEY`, `SHAPELESSAI_API_KEY`, `MONGODB_URI`, and optionally
`COOKIES_FILE` (Netscape cookie-file contents). MongoDB is required for access/settings to persist across Actions runs.

## Extra viral signals (free, no API key)

`/auto` now also uses **comment timestamps** (yt-dlp `--write-comments`; comments like "5:32 😂" vote for a moment,
liked comments count more) and **audio energy** (ffmpeg; moments much louder than the video's normal level).
They are blended with heatmap / chat / transcript scores; a signal that is unavailable is skipped and the rest
are re-weighted automatically. Default extra weights: comments 0.25, audio 0.15 (relative, auto-normalised).
