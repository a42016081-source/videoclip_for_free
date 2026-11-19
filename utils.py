import re, hashlib
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

def parse_time(v: str):
    v = v.strip()
    if not v: return None
    try: parts = [int(p) for p in v.split(":")]
    except ValueError: return None
    if len(parts) == 1: return parts[0]
    if len(parts) == 2: return parts[0] * 60 + parts[1]
    if len(parts) == 3: return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None

def fmt_time(s: float) -> str:
    s = int(s); h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"

def extract_video_id(url: str):
    p = urlparse(url)
    if "youtu.be" in p.netloc: return p.path.lstrip("/")
    if "youtube.com" in p.netloc:
        if p.path == "/watch": return parse_qs(p.query).get("v", [None])[0]
        if p.path.startswith(("/shorts/", "/live/", "/embed/")): return p.path.split("/")[2]
    return None

def extract_t_from_url(url: str):
    qs = parse_qs(urlparse(url).query)
    for k in ("t", "start"):
        if k in qs:
            r = qs[k][0].rstrip("s")
            if r.isdigit(): return int(r)
    return None

def normalize_url(url: str) -> str:
    p = urlparse(url); qs = parse_qs(p.query)
    keep = {"t": qs["t"]} if "t" in qs else {}
    return urlunparse(p._replace(query=urlencode(keep, doseq=True)))

def cache_key(url, start, end, quality, shorts, subs, style) -> str:
    raw = f"{normalize_url(url)}|{start}|{end}|{quality}|{shorts}|{subs}|{style}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]

def human_size(b: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"