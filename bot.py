"""Entry point: web server (health + /dl links), keep-alive pinger, Telegram polling."""
import asyncio
import logging
import signal
import sys
from telegram import BotCommand
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters)
import config
from database import init_db
from web import start_web, self_ping_loop, cleanup_loop
from autopipeline import resume_pending, handle_auto_callback
from handlers import (
    start, help_cmd, ping, cut_command, multi_command, route_message, route_callback, meta_callback,
    admin_stats, broadcast, ban, unban, upload_command, on_video, auto_command, allow_command, remove_command, id_command,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s — %(message)s", stream=sys.stdout)
logging.getLogger("httpx").setLevel(logging.WARNING)      # don't log every Telegram API call
log = logging.getLogger("bot")


async def update_ytdlp():
    """Auto-update yt-dlp on startup (in the background, so the bot answers immediately)."""
    try:
        p = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "yt-dlp[default]",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(p.wait(), 300)
        log.info("yt-dlp update finished (rc=%s)", p.returncode)
    except Exception as e:
        log.warning("yt-dlp update failed: %s", e)


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Start"),
        BotCommand("cut", "/cut <url> <start> <end>"),
        BotCommand("multi", "Many clips from one video"),
        BotCommand("upload", "Upload your video + add subtitles"),
        BotCommand("help", "Help"),
        BotCommand("ping", "Ping"),
        BotCommand("stats", "Admin stats"),
        BotCommand("broadcast", "Admin broadcast"),
        BotCommand("ban", "Admin ban"),
        BotCommand("unban", "Admin unban"),
        BotCommand("auto", "Find and clip viral moments"),
        BotCommand("allow", "Owner: allow a user"),
        BotCommand("remove", "Owner: remove a user"),
        BotCommand("id", "Show your Telegram ID"),
    ])


async def on_error(update, ctx):
    """Global error handler: log it, tell the user something went wrong."""
    log.error("Unhandled error (%s)", type(ctx.error).__name__)
    try:
        if update is not None and getattr(update, "effective_chat", None):
            await ctx.bot.send_message(update.effective_chat.id, "⚠️ Kuch galat ho gaya. Dobara try karo.")
    except Exception:
        pass


async def supervise(factory, name):
    """Run a background loop; restart it if it ever crashes."""
    while True:
        try:
            await factory()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s crashed — restarting in 5 s", name)
            await asyncio.sleep(5)


def build_app() -> Application:
    b = Application.builder().token(config.BOT_TOKEN)
    # Without this, ONE running job blocks every other update (all users) until it finishes.
    b = b.concurrent_updates(True).read_timeout(60).write_timeout(300).connect_timeout(30)
    if config.LOCAL_BOT_API_URL:                       # local Bot API server -> files up to 2 GB
        base = config.LOCAL_BOT_API_URL.rstrip("/")
        b = b.base_url(f"{base}/bot").base_file_url(f"{base}/file/bot").local_mode(True)
    app = b.build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("cut", cut_command))
    app.add_handler(CommandHandler("multi", multi_command))
    app.add_handler(CommandHandler("upload", upload_command))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("auto", auto_command))
    app.add_handler(CommandHandler("allow", allow_command))
    app.add_handler(CommandHandler("remove", remove_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CallbackQueryHandler(handle_auto_callback, pattern=r"^auto:(post|edit|skip):"))

    app.add_handler(CallbackQueryHandler(meta_callback, pattern=r"^meta:"))
    app.add_handler(CallbackQueryHandler(route_callback, pattern=r"^(?!meta:)"))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, on_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))
    app.add_error_handler(on_error)
    return app


async def main():
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set.")
    db_ok = init_db()
    if not db_ok:
        log.warning("Database is local only; user access and settings reset when this runner is replaced.")
    runner = await start_web()
    tasks = [asyncio.create_task(supervise(self_ping_loop, "self-ping")),
             asyncio.create_task(supervise(cleanup_loop, "cleanup")),
             asyncio.create_task(update_ytdlp())]

    app = build_app()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):        # Render sends SIGTERM on redeploy
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    log.info("Bot starting…")
    await app.initialize()
    await post_init(app)             # run_polling() would do this for us; we drive the loop manually
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    asyncio.create_task(resume_pending(app))
    log.info("Bot is running.")
    try:
        await stop.wait()
    finally:
        log.info("Shutting down…")
        for t in tasks:
            t.cancel()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
