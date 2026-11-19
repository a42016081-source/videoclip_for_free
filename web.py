import asyncio, logging
import aiohttp
from aiohttp import web
from config import PORT, RENDER_EXTERNAL_URL, PING_INTERVAL
from delivery import SHARED, cleanup_links, enforce_temp_limit
log = logging.getLogger(__name__)

async def health(r): return web.Response(text="OK")

async def download(r):
    tok = r.match_info["token"]
    e = SHARED.get(tok)
    if not e or not e[0].exists(): raise web.HTTPNotFound(text="Link expired")
    return web.FileResponse(e[0], headers={"Content-Disposition": f'attachment; filename="{e[0].name}"'})

async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/dl/{token}", download)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Web on :%s", PORT)

async def self_ping_loop():
    if not RENDER_EXTERNAL_URL: return
    url = RENDER_EXTERNAL_URL.rstrip("/") + "/health"
    async with aiohttp.ClientSession() as s:
        while True:
            try:
                async with s.get(url, timeout=10) as r: await r.text()
            except Exception: pass
            await asyncio.sleep(PING_INTERVAL)

async def cleanup_loop():
    while True:
        await asyncio.sleep(600)
        try: enforce_temp_limit(); cleanup_links()
        except Exception as e: log.warning("cleanup: %s", e)