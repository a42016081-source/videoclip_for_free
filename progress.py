"""One Telegram message that is edited live (at most every 2 s)."""
import time
import asyncio


class ProgressBar:
    def __init__(self, msg, total):
        self.msg = msg
        self.total = max(total, 1)
        self.current = 0
        self.stage = "Starting"
        self.speed = ""
        self.eta = ""
        self._last = 0.0
        self._stage_t0 = time.monotonic()
        self._lock = asyncio.Lock()

    @staticmethod
    def _bar(pct, n=10):
        f = int(n * min(max(pct, 0), 100) / 100)
        return "▓" * f + "░" * (n - f)

    def _auto_eta(self):
        el = time.monotonic() - self._stage_t0
        if self.current > 0 and el > 3 and self.current < self.total:
            rem = el * (self.total - self.current) / self.current
            return f"{int(rem // 60):02d}:{int(rem % 60):02d}"
        return ""

    def _render(self):
        pct = min(max(self.current / self.total * 100, 0), 100)
        parts = [self.stage, self._bar(pct), f"{pct:.0f}%"]
        if self.speed:
            parts.append(f"| {self.speed}")
        eta = self.eta or self._auto_eta()
        if eta:
            parts.append(f"| ETA {eta}")
        return "  ".join(parts)

    async def update(self, current=None, stage=None, speed=None, eta=None, force=False):
        if stage is not None and stage != self.stage:
            self.stage = stage
            self._stage_t0 = time.monotonic()
            self.current = 0 if current is None else current
            current = None
            self.speed = self.eta = ""
            force = True
        if current is not None:
            self.current = current
        if speed is not None:
            self.speed = speed
        if eta is not None:
            self.eta = eta
        if not force and time.monotonic() - self._last < 2.0:
            return
        async with self._lock:
            if not force and time.monotonic() - self._last < 2.0:
                return
            self._last = time.monotonic()      # set first so failed edits can't flood Telegram
            try:
                await self.msg.edit_text(self._render())
            except Exception:
                pass

    async def finish(self, text="✅ Done"):
        try:
            await self.msg.edit_text(text)
        except Exception:
            pass
