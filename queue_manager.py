import asyncio, time
from collections import defaultdict
from config import MAX_JOBS, RATE_LIMIT_PER_HOUR, ADMIN_IDS

_sem = asyncio.Semaphore(MAX_JOBS)
_hits = defaultdict(list)

def rate_limited(uid):
    if uid in ADMIN_IDS: return False
    now = time.time()
    _hits[uid] = [t for t in _hits[uid] if now - t < 3600]
    if len(_hits[uid]) >= RATE_LIMIT_PER_HOUR: return True
    _hits[uid].append(now); return False

class job_slot:
    async def __aenter__(self): await _sem.acquire(); return self
    async def __aexit__(self, *a): _sem.release()