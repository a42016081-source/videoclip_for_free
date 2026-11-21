"""Job queue (MAX_JOBS at a time) + per-user hourly rate limit."""
import asyncio
import time
from collections import defaultdict
from config import MAX_JOBS, RATE_LIMIT_PER_HOUR, ADMIN_IDS

_sem = asyncio.Semaphore(MAX_JOBS)
_hits: dict[int, list[float]] = defaultdict(list)
_waiting = 0


def rate_limited(uid) -> bool:
    """True if the user is over the limit; otherwise records this job."""
    if uid in ADMIN_IDS:
        return False
    now = time.time()
    _hits[uid] = [t for t in _hits[uid] if now - t < 3600]
    if len(_hits[uid]) >= RATE_LIMIT_PER_HOUR:
        return True
    _hits[uid].append(now)
    return False


class job_slot:
    """`async with job_slot(on_queue)` — on_queue(position) is awaited if the user has to wait."""

    def __init__(self, on_queue=None):
        self.on_queue = on_queue

    async def __aenter__(self):
        global _waiting
        if _sem.locked() and self.on_queue:
            try:
                await self.on_queue(_waiting + 1)
            except Exception:
                pass
        _waiting += 1
        try:
            await _sem.acquire()
        finally:
            _waiting -= 1
        return self

    async def __aexit__(self, *a):
        _sem.release()
