"""Small pure helpers: time parsing, URL handling, formatting."""
import re
import hashlib
from urllib.parse import urlparse, parse_qs

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_time(v: str):
    """'SS' | 'MM:SS' | 'HH:MM:SS' -> seconds (int) or None if invalid."""
    v = (v or "").strip()
    if not v:
        return None
    try:
        parts = [int(p) for p in v.split(":")]
    except ValueError:
        return None
    if any(p < 0 for p in parts):
        return None
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def fmt_time(s: float) -> str:
    s = int(s)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _parse(url: str):
    url = (url or "").strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    try:
        p = urlparse(url)
    except ValueError:
        return None
    return p if p.scheme in ("http", "https") else None


def extract_video_id(url: str):
    """Return the 11-char YouTube video id, or None. Only real YouTube hosts are accepted."""
    p = _parse(url)
    if not p:
        return None
    host = (p.hostname or "").lower()
    vid = None
    if host in _SHORT_HOSTS:
        vid = p.path.lstrip("/").split("/")[0]
    elif host in _YT_HOSTS:
        if p.path == "/watch":
            vid = parse_qs(p.query).get("v", [None])[0]
        else:
            parts = p.path.split("/")
            if len(parts) >= 3 and parts[1] in ("shorts", "live", "embed"):
                vid = parts[2]
    return vid if vid and _ID_RE.match(vid) else None


def _parse_t(raw: str):
    raw = raw.strip().lower()
    if raw.isdigit():
        return int(raw)
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", raw)
    if m and any(m.groups()):
        h, mi, s = (int(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + s
    return None


def extract_t_from_url(url: str):
    """?t=90 / &t=1m30s / &start=90 -> seconds, else None."""
    p = _parse(url)
    if not p:
        return None
    qs = parse_qs(p.query)
    for k in ("t", "start"):
        if k in qs:
            v = _parse_t(qs[k][0])
            if v is not None:
                return v
    return None


def normalize_url(url: str) -> str:
    """Canonical https://www.youtube.com/watch?v=ID (keeps unknown input unchanged)."""
    vid = extract_video_id(url)
    return f"https://www.youtube.com/watch?v={vid}" if vid else (url or "").strip()


def cache_key(url, start, end, quality, shorts, subs, style, reframe="-", lang="-") -> str:
    raw = f"{normalize_url(url)}|{start}|{end}|{quality}|{bool(shorts)}|{bool(subs)}|{style}|{reframe}|{lang}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def human_size(b: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"
