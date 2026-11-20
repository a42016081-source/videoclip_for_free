"""Tiny aiohttp server: /health for Render, /dl/<token> for self-hosted links, self-ping."""
import asyncio
import logging
import time
import aiohttp
from aiohttp import web
from config import PORT, RENDER_EXTERNAL_URL, PING_INTERVAL
from delivery import SHARED, cleanup_links, enforce_temp_limit, cleanup_old_tmp
log = logging.getLogger(__name__)


async def health(r):
    return web.Response(text="OK")


async def download(r):
    e = SHARED.get(r.match_info["token"])
    if not e or time.time() > e[1] or not e[0].exists():
        raise web.HTTPNotFound(text="Link expired")
    return web.FileResponse(e[0], headers={"Content-Disposition": f'attachment; filename="{e[0].name}"'})


async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/dl/{token}", download)
    runner = web.AppRunner(app, access_log=None)     # no log line every 5 s from the self-ping
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Web on :%s", PORT)
    return runner


async def self_ping_loop():
    """Keeps Render awake. Errors are swallowed per-request so the loop never dies."""
    if not RENDER_EXTERNAL_URL:
        return
    url = RENDER_EXTERNAL_URL.rstrip("/") + "/health"
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        while True:
            try:
                async with s.get(url) as r:
                    await r.text()
            except Exception:
                pass
            await asyncio.sleep(max(PING_INTERVAL, 1))


async def cleanup_loop():
    while True:
        await asyncio.sleep(600)
        try:
            cleanup_old_tmp()
            enforce_temp_limit()
            cleanup_links()
        except Exception as e:
            log.warning("cleanup: %s", e)
