"""Viral signal from audio loudness (laughter, shouting, hype) using ffmpeg only — free and local."""
import asyncio, logging, subprocess
log = logging.getLogger(__name__)
SR = 8000


def _scan(path: str, bin_seconds: int):
    import numpy as np
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunk = bin_seconds * SR * 2
    rms = []
    try:
        while True:                                   # stream bin by bin -> low memory even for 3 h audio
            buf = p.stdout.read(chunk)
            if not buf or len(buf) < SR:              # drop the tiny tail
                break
            x = np.frombuffer(buf[: len(buf) // 2 * 2], dtype=np.int16).astype(np.float32)
            rms.append(float(np.sqrt(np.mean(x * x))))
    finally:
        p.stdout.close(); p.kill(); p.wait()
    return rms


async def audio_energy(path, bin_seconds: int = 10) -> list[dict]:
    """[{start, end, score, reason}] for bins clearly louder than the video's normal level; [] on failure."""
    try:
        rms = await asyncio.to_thread(_scan, str(path), bin_seconds)
        if len(rms) < 6:
            return []
        import numpy as np
        a = np.array(rms)
        base = float(np.median(a)) or 1.0
        ratio = a / base
        cutoff = max(1.4, float(np.percentile(ratio, 85)))   # louder than usual AND in the top 15 %
        peak = float(ratio.max()) or 1.0
        return [{"start": i * bin_seconds, "end": (i + 1) * bin_seconds, "score": float(r / peak),
                 "reason": "Loud / energetic audio"} for i, r in enumerate(ratio) if r >= cutoff]
    except Exception as e:
        log.info("Audio energy scan failed (%s)", type(e).__name__)
        return []
