"""Auto colours for typography subtitles: look at the VIDEO and pick word colours that stand out on it.

How it works (no extra Python packages, only ffmpeg):
  1. ffmpeg grabs a tiny RGB thumbnail every few seconds (raw bytes on a pipe).
  2. For each thumbnail we find the dominant hue (weighted by saturation x brightness) and the brightness.
  3. From that hue we build a palette of bright colours that contrast with the scene:
     complementary + triadic hues (so subtitles never blend into the background).
  4. build_ass() looks up the palette that belongs to the moment each subtitle appears, so the colours
     follow the video scene by scene.
"""
import asyncio
import colorsys
import logging
from pathlib import Path

log = logging.getLogger(__name__)

TW, TH = 32, 18                      # thumbnail size: 1728 bytes per frame, plenty for a colour estimate
FRAME_BYTES = TW * TH * 3
STEP_S = 3.0                         # one palette per 3 seconds of video
MAX_SAMPLES = 120                    # cap (6 minutes of video at 3 s); longer clips reuse the last palette

# Used when the video cannot be analysed (no frames, ffmpeg error ...): bright, readable, varied.
DEFAULT_PALETTE = ["FFD400", "39FF14", "00E5FF", "FF2D95", "FF7A00"]


def _hex(r, g, b) -> str:
    return "%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))


def palette_from_frame(rgb: bytes, n: int = 5) -> list:
    """Dominant hue of one thumbnail -> `n` hex colours that contrast with it (most contrasting first)."""
    hist = [0.0] * 36                # 10-degree hue buckets
    lum_sum = 0.0
    px = len(rgb) // 3
    for i in range(px):
        r, g, b = rgb[3 * i] / 255, rgb[3 * i + 1] / 255, rgb[3 * i + 2] / 255
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        lum_sum += 0.2126 * r + 0.7152 * g + 0.0722 * b
        hist[min(int(h * 36), 35)] += s * v           # grey / dark pixels count for (almost) nothing
    lum = lum_sum / max(px, 1)
    total = sum(hist)
    if total < px * 0.04:            # (nearly) black & white scene: no dominant hue -> use the default palette
        base = None
    else:
        base = (max(range(36), key=hist.__getitem__) + 0.5) / 36
    if base is None:
        offsets = [0.14, 0.33, 0.50, 0.88, 0.07]      # yellow, green, cyan, pink, orange
        base = 0.0
        hues = offsets
    else:
        # complementary first, then the two triadic hues, then the neighbours of the complement
        hues = [(base + 0.5) % 1, (base + 1 / 3) % 1, (base + 2 / 3) % 1, (base + 0.42) % 1, (base + 0.58) % 1]
    v = 1.0 if lum < 0.7 else 0.92   # bright scene -> slightly deeper colours (the black outline does the rest)
    out = []
    for h in hues[:n]:
        r, g, b = colorsys.hsv_to_rgb(h, 0.92 if lum < 0.7 else 1.0, v)
        if 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.38:   # blue / purple look dark even at full brightness
            r, g, b = (c + (1 - c) * 0.45 for c in (r, g, b))   # -> lighten them so they stay readable
        out.append(_hex(r, g, b))
    return out


async def palettes_for_video(src, duration: float, step: float = STEP_S) -> list:
    """[(t_seconds, [hex colours])] — one entry per `step` seconds. Never raises: falls back to a default palette."""
    n = max(1, min(int(duration / step) + 1, MAX_SAMPLES))
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(Path(src)), "-an",
           "-vf", f"fps=1/{step},scale={TW}:{TH}", "-frames:v", str(n),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.DEVNULL)
        data, _ = await asyncio.wait_for(proc.communicate(), 120)
    except Exception as e:                                       # pragma: no cover
        log.warning("autocolor: ffmpeg failed: %s", e)
        return [(0.0, list(DEFAULT_PALETTE))]
    frames = [data[i:i + FRAME_BYTES] for i in range(0, len(data) - FRAME_BYTES + 1, FRAME_BYTES)]
    if not frames:
        return [(0.0, list(DEFAULT_PALETTE))]
    pal = []
    for k, fr in enumerate(frames):
        try:
            pal.append((k * step, palette_from_frame(fr)))
        except Exception as e:                                   # pragma: no cover
            log.warning("autocolor: frame %d: %s", k, e)
            pal.append((k * step, list(DEFAULT_PALETTE)))
    return pal


def palette_at(palettes, t: float) -> list:
    """Palette in force at time `t` (the last sample that started at or before t)."""
    if not palettes:
        return list(DEFAULT_PALETTE)
    best = palettes[0][1]
    for ts, p in palettes:
        if ts <= t:
            best = p
        else:
            break
    return best
