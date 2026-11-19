import time, asyncio

class ProgressBar:
    def __init__(self, msg, total):
        self.msg = msg; self.total = max(total, 1); self.current = 0
        self.stage = "Starting"; self.speed = ""; self.eta = ""
        self._last = 0; self._lock = asyncio.Lock()

    def _bar(self, pct, n=10):
        f = int(n * min(max(pct, 0), 100) / 100)
        return "▓" * f + "░" * (n - f)

    def _render(self):
        pct = min(max(self.current / self.total * 100, 0), 100)
        parts = [self.stage, self._bar(pct), f"{pct:.0f}%"]
        if self.speed: parts.append(f"| {self.speed}")
        if self.eta: parts.append(f"| ETA {self.eta}")
        return "  ".join(parts)

    async def update(self, current=None, stage=None, speed=None, eta=None):
        if current is not None: self.current = current
        if stage is not None: self.stage = stage
        if speed is not None: self.speed = speed
        if eta is not None: self.eta = eta
        now = time.monotonic()
        if now - self._last < 2.0: return
        async with self._lock:
            if time.monotonic() - self._last < 2.0: return
            try:
                await self.msg.edit_text(self._render()); self._last = time.monotonic()
            except Exception: pass

    async def finish(self, text="✅ Done"):
        try: await self.msg.edit_text(text)
        except Exception: pass