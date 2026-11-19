import asyncio, logging, sys
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
import config
from database import init_db
from web import start_web, self_ping_loop, cleanup_loop
from handlers import (
    start, help_cmd, ping, cut_command, route_message, route_callback,
    admin_stats, broadcast, ban, unban, cleanup_task, _metadata,
)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s", stream=sys.stdout)
log = logging.getLogger("bot")

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Start"),
        BotCommand("cut", "/cut <url> <start> <end>"),
        BotCommand("help", "Help"),
        BotCommand("ping", "Ping"),
        BotCommand("stats", "Admin stats"),
        BotCommand("broadcast", "Admin broadcast"),
        BotCommand("ban", "Admin ban"),
        BotCommand("unban", "Admin unban"),
    ])
    try:
        p = await asyncio.create_subprocess_exec("pip", "install", "--upgrade", "yt-dlp",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await p.wait(); log.info("yt-dlp updated")
    except Exception as e: log.warning("yt-dlp: %s", e)

async def meta_cb(update, ctx):
    await _metadata(update, ctx, int(update.callback_query.data.split(":")[1]))

async def main():
    init_db()
    await start_web()
    asyncio.create_task(self_ping_loop())
    asyncio.create_task(cleanup_loop())

    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("cut", cut_command))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))

    app.add_handler(CallbackQueryHandler(route_callback, pattern=r"^(?!meta:)"))
    app.add_handler(CallbackQueryHandler(meta_cb, pattern=r"^meta:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))

    log.info("Bot starting…")
    await app.initialize(); await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    try: await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit): pass
    finally:
        await app.updater.stop(); await app.stop(); await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())