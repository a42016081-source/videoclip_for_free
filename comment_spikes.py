"""Viral signal from comment timestamps ("5:32 😂"), fetched with yt-dlp (no API key / quota needed)."""
import json, logging, math, re
from collections import defaultdict
log = logging.getLogger(__name__)

_TS = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?([0-5]?\d):([0-5]\d)(?![\d:])")


def _seconds(m) -> int:
    h = int(m.group(1) or 0)
    return h * 3600 + int(m.group(2)) * 60 + int(m.group(3))


async def comment_spikes(url: str, duration: float, bin_seconds: int = 10, max_comments: int = 400) -> list[dict]:
    """[{start, end, score, reason}] bins where many (liked) comments point at the same moment; [] if unavailable."""
    try:
        from downloader import _run, _cookie_args          # reuse cookies + cookie persistence
        cmd = ["yt-dlp", "--skip-download", "--write-comments", "--dump-single-json", "--no-playlist",
               "--no-warnings", "--extractor-args",
               f"youtube:max_comments={max_comments},{max_comments},0,0,0;comment_sort=top",
               *_cookie_args(), "--", url]
        r = await _run(cmd, 240)
        if r.returncode != 0 or not r.stdout.strip():
            log.info("Comments unavailable: %s", (r.stderr or "")[-200:])
            return []
        comments = json.loads(r.stdout).get("comments") or []
        bins = defaultdict(float)
        for c in comments:
            text = str(c.get("text") or "")
            weight = 1.0 + math.log1p(max(0, int(c.get("like_count") or 0)))   # liked comments count more
            seen = set()
            for m in _TS.finditer(text):
                t = _seconds(m)
                if 0 <= t < duration:
                    seen.add(int(t // bin_seconds) * bin_seconds)
            for b in seen:                                                       # one vote per comment per bin
                bins[b] += weight
        if not bins:
            return []
        vals = sorted(bins.values())
        floor = max(2.0, vals[int(len(vals) * .6)])                              # ignore one-off timestamps
        peak = vals[-1] or 1
        return [{"start": t, "end": t + bin_seconds, "score": v / peak, "reason": "Comment timestamps"}
                for t, v in bins.items() if v >= floor]
    except Exception as e:
        log.info("Comment scan failed (%s)", type(e).__name__)
        return []
