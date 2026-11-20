"""Subtitle engine: style presets + per-user overrides -> .ass (burned in) and .srt export.

A style is a PRESET (look + animation mode) plus optional user OVERRIDES stored as JSON in
users.sub_cfg. Anything the user leaves on "auto" falls back to the preset's own value.
"""
import asyncio
import hashlib
import json
import logging
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from config import BASE
from autocolor import palette_at, palette_from_frame, DEFAULT_PALETTE

log = logging.getLogger(__name__)
FONTS_DIR = BASE / "fonts"          # drop extra .ttf/.otf files here, they show up in the font list

# ───────────── option tables (also used by the Telegram UI) ─────────────
COLORS = {"white": "FFFFFF", "yellow": "FFD400", "green": "39FF14", "cyan": "00E5FF", "pink": "FF2D95",
          "orange": "FF7A00", "red": "FF2D2D", "purple": "B455FF", "black": "000000"}
COLOR_EMOJI = {"white": "⚪", "yellow": "🟡", "green": "🟢", "cyan": "🔵", "pink": "🌸",
               "orange": "🟠", "red": "🔴", "purple": "🟣", "black": "⚫", "video": "🎬"}
SIZES = {"s": 0.8, "m": 1.0, "l": 1.25, "xl": 1.5}
OUTLINES = {"thin": 0.5, "normal": 1.0, "thick": 1.5, "xthick": 2.0}
POSITIONS = ("top", "middle", "safe", "bottom")        # safe = above the Shorts UI
CASES = ("normal", "upper", "lower", "title")
BGS = ("none", "shadow", "box")
ANIMS = ("none", "pop", "fade")
WORDS = (1, 2, 3, 4, 5, 6)
FONT_CANDIDATES = ("Noto Sans", "Montserrat", "Poppins", "Anton", "Bebas Neue", "Oswald",
                   "Comic Neue", "Comic Relief", "Archivo Black", "Noto Serif")
_HEAVY_FONTS = {"Anton", "Bebas Neue", "Archivo Black"}   # already heavy: no fake bold

# mode: sweep (karaoke \k) | fill (smooth \kf) | word (active word coloured) | box (active word on a
# coloured marker) | single (one big word at a time) | neon (word + glow layer)
PRESETS = {
    "classic":  dict(label="Classic",  mode="sweep",  font="Noto Sans", size=64, words=5, case="normal", bold=1,
                     italic=0, outline=4, shadow=2, color="white", hi="white", pos="safe", bg="shadow", anim="none"),
    "bold":     dict(label="Bold Yellow", mode="sweep", font="Noto Sans", size=82, words=3, case="normal", bold=1,
                     italic=0, outline=6, shadow=3, color="white", hi="yellow", pos="safe", bg="shadow", anim="none"),
    "minimal":  dict(label="Minimal",  mode="sweep",  font="Noto Sans", size=50, words=6, case="normal", bold=0,
                     italic=0, outline=2, shadow=0, color="white", hi="white", pos="safe", bg="none", anim="none"),
    "hormozi":  dict(label="Hormozi (word highlight)", mode="word", font="Montserrat", size=92, words=3,
                     case="upper", bold=1, italic=0, outline=8, shadow=0, color="white", hi="green",
                     pos="safe", bg="none", anim="none"),
    "pop":      dict(label="Pop (1 word)", mode="single", font="Anton", size=140, words=1, case="upper", bold=0,
                     italic=0, outline=9, shadow=0, color="white", hi="yellow", pos="middle", bg="none", anim="pop"),
    "box":      dict(label="Marker box", mode="box",   font="Montserrat", size=84, words=3, case="upper", bold=1,
                     italic=0, outline=6, shadow=0, color="white", hi="yellow", pos="safe", bg="none", anim="none"),
    "neon":     dict(label="Neon glow", mode="neon",   font="Poppins", size=80, words=3, case="upper", bold=1,
                     italic=0, outline=2, shadow=0, color="white", hi="cyan", pos="safe", bg="none", anim="none"),
    "karaoke":  dict(label="Karaoke fill", mode="fill", font="Noto Sans", size=76, words=4, case="normal", bold=1,
                     italic=0, outline=5, shadow=2, color="white", hi="orange", pos="safe", bg="shadow", anim="none"),
    "comic":    dict(label="Comic fun", mode="word",   font="Comic Neue", size=88, words=3, case="upper", bold=1,
                     italic=1, outline=9, shadow=2, color="white", hi="pink", pos="safe", bg="none", anim="pop",
                     rot=-3),
    # Typography: words pop in one after another, important words are bigger and coloured. With auto=True the
    # colours are picked from the VIDEO itself (see autocolor.py) and change scene by scene.
    "typo":     dict(label="Typography (auto colours)", mode="typo", font="Montserrat", size=104, words=3,
                     case="upper", bold=1, italic=0, outline=7, shadow=0, color="white", hi="yellow",
                     auto=True, pos="safe", bg="none", anim="pop", kland=0.82),
    "news":     dict(label="News bar", mode="sweep",   font="Noto Sans", size=56, words=6, case="normal", bold=1,
                     italic=0, outline=14, shadow=0, color="white", hi="yellow", pos="bottom", bg="box", anim="none"),
}
STYLE_NAMES = list(PRESETS)


# ───────────── fonts ─────────────
@lru_cache(maxsize=1)
def available_fonts() -> tuple:
    """Fonts from FONT_CANDIDATES that fontconfig (or ./fonts) really has. Noto Sans is always listed."""
    found = set()
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=20).stdout
        for line in out.splitlines():
            for fam in line.split(","):
                found.add(fam.strip())
    except Exception as e:                                       # pragma: no cover
        log.warning("fc-list failed: %s", e)
    try:
        if FONTS_DIR.is_dir() and shutil.which("fc-scan"):
            files = [str(p) for p in FONTS_DIR.iterdir() if p.suffix.lower() in (".ttf", ".otf")]
            if files:
                out = subprocess.run(["fc-scan", "--format", "%{family}\n", *files],
                                     capture_output=True, text=True, timeout=20).stdout
                for line in out.splitlines():
                    for fam in line.split(","):
                        found.add(fam.strip())
    except Exception as e:                                       # pragma: no cover
        log.warning("fc-scan failed: %s", e)
    fonts = [f for f in FONT_CANDIDATES if f in found]
    return tuple(fonts) if "Noto Sans" in fonts else ("Noto Sans", *fonts)


# ───────────── config helpers ─────────────
def load_cfg(raw) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        d = json.loads(raw or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def dump_cfg(cfg: dict) -> str:
    return json.dumps({k: v for k, v in cfg.items() if v not in (None, "", "auto")}, sort_keys=True)


def cfg_fingerprint(style, raw) -> str:
    """Goes into the result-cache key so a changed subtitle look never serves an old cached clip."""
    return style + ":" + hashlib.sha1(dump_cfg(load_cfg(raw)).encode()).hexdigest()[:8]


def _ass(hexrgb, alpha=0) -> str:                    # style colour  &HAABBGGRR
    r, g, b = hexrgb[0:2], hexrgb[2:4], hexrgb[4:6]
    return f"&H{alpha:02X}{b}{g}{r}"


def _inl(hexrgb) -> str:                             # inline colour  &HBBGGRR&
    return f"&H{hexrgb[4:6]}{hexrgb[2:4]}{hexrgb[0:2]}&"


def resolve(style, cfg=None, vertical=True, size=None) -> dict:
    """Preset + overrides -> everything the renderer needs (pixel sizes already scaled)."""
    key = style if style in PRESETS else "classic"
    S = dict(PRESETS[key])
    S["style"] = key
    o = load_cfg(cfg)
    fonts = available_fonts()
    if o.get("font") in fonts:
        S["font"] = o["font"]
    elif S["font"] not in fonts:
        S["font"] = "Noto Sans"
    if o.get("color") in COLORS:
        S["color"] = o["color"]
    S["auto"] = bool(S.get("auto", False))          # True = highlight colours come from the video
    if o.get("hi") == "video":
        S["auto"] = True
    elif o.get("hi") in COLORS:
        S["hi"], S["auto"] = o["hi"], False          # an explicit colour always wins over the preset's auto
    if o.get("case") in CASES:
        S["case"] = o["case"]
    if o.get("pos") in POSITIONS:
        S["pos"] = o["pos"]
    if o.get("bg") in BGS:
        S["bg"] = o["bg"]
    if o.get("anim") in ANIMS:
        S["anim"] = o["anim"]
    try:
        if 1 <= int(o.get("words", 0)) <= 6:
            S["words"] = int(o["words"])
    except (TypeError, ValueError):
        pass
    if size:                                         # real frame size (upload keeps its own aspect ratio)
        vertical = size[1] > size[0]
    sc = (min(size) / 1080) if size else 1.0         # 1080 = the short side the presets were designed for
    k = sc * (1.0 if vertical else S.get("kland", 0.66))   # 16:9 canvas is 1920x1080 -> smaller type
    S["fs"] = max(round(S["size"] * SIZES.get(o.get("size"), 1.0) * k), 12)
    S["ol"] = round(S["outline"] * OUTLINES.get(o.get("outline"), 1.0) * k, 1)
    S["sh"] = round(S["shadow"] * k, 1)
    if S["font"] in _HEAVY_FONTS:
        S["bold"] = 0
    # (alignment, marginV) — numpad alignment: 2 bottom, 5 middle, 8 top
    S["align"], S["mv"] = {
        True:  {"top": (8, 300), "middle": (5, 0), "safe": (2, 430), "bottom": (2, 170)},
        False: {"top": (8, 60), "middle": (5, 0), "safe": (2, 80), "bottom": (2, 40)},
    }[vertical][S["pos"]]
    S["mv"] = round(S["mv"] * sc)
    S["lr"] = round((80 if vertical else 120) * sc)
    S["vertical"], S["k"] = vertical, k
    return S


# ───────────── .ass building ─────────────
def _header(S, vertical, size=None) -> str:
    w, h = size or ((1080, 1920) if vertical else (1920, 1080))
    sweep = S["mode"] in ("sweep", "fill")
    primary = COLORS[S["hi"] if sweep else S["color"]]   # \k: word turns PRIMARY once spoken
    secondary = COLORS[S["color"]]
    outline_c, back_c, border = "000000", _ass("000000", 0x80), 1
    ol, sh = S["ol"], S["sh"]
    if S["bg"] == "box":
        border, outline_c, back_c = 3, "000000", _ass("000000", 0x40)
        ol, sh = max(ol, 10 * S["k"]), 0
    elif S["bg"] == "shadow" and sh == 0:
        sh = 3 * S["k"]
    if S["mode"] == "neon":
        outline_c = COLORS[S["hi"]]
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: S,{S['font']},{S['fs']},{_ass(primary)},{_ass(secondary)},{_ass(outline_c, 0 if border == 1 else 0x30)},{back_c},{-S['bold']},{S['italic']},0,0,100,100,0,0,{border},{ol},{sh},{S['align']},{S['lr']},{S['lr']},{S['mv']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""", border, ol


def _clean(words):
    out = []
    for w in words or []:
        t = re.sub(r"[{}\\\r\n]", "", str(w.get("word", ""))).strip()
        try:
            s, e = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if t:
            out.append({"word": t, "start": max(s, 0.0), "end": max(e, s)})
    return out


def _case(t, mode):
    return {"upper": t.upper(), "lower": t.lower(), "title": t[:1].upper() + t[1:].lower()}.get(mode, t)


def _ts(sec: float) -> str:
    cs = max(int(round(sec * 100)), 0)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _chunks(words, size):
    chunk = []
    for w in words:
        if chunk and (len(chunk) >= size or w["start"] - chunk[-1]["end"] > 1.0):
            yield chunk
            chunk = []
        chunk.append(w)
        if w["word"].endswith((".", "?", "!", "।")):
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _anim_tags(anim, first, S):
    """Tags that go in front of the first event of a chunk (pop = scale-in, fade = fade-in)."""
    if not first:
        return ""
    if anim == "pop":
        return r"\fscx82\fscy82\t(0,110,\fscx104\fscy104)\t(110,190,\fscx100\fscy100)"
    if anim == "fade":
        return r"\fad(110,0)"
    return ""


_FILLERS = {"about", "their", "there", "which", "would", "could", "should", "these", "those", "where",
            "while", "other", "being", "because", "kyunki", "isliye", "lekin", "wahi", "kuch", "bahut"}


def _is_key(word: str) -> bool:
    """Words worth colouring + enlarging in the typography style: numbers, !/? words and longer content words."""
    core = re.sub(r"[^\w]", "", word, flags=re.UNICODE).lower()
    if not core:
        return False
    if any(ch.isdigit() for ch in core) or word.endswith(("!", "?")):
        return True
    return len(core) >= 5 and core not in _FILLERS


def build_ass(words, style="classic", offset=0.0, vertical=True, cfg=None, size=None, palettes=None) -> str:
    """`vertical=True` for 9:16 output (1080x1920), False for a 16:9 (1920x1080) frame.
    `size=(w, h)` = the real frame size (any aspect ratio) and overrides `vertical`.
    `palettes` = [(t, [hex...])] from autocolor.palettes_for_video(); used when the style's colour is "from video"."""
    S = resolve(style, cfg, vertical, size)
    vertical = S["vertical"]
    head, border, ol = _header(S, vertical, size)
    ws = _clean(words)
    for w in ws:
        w["word"] = _case(w["word"], S["case"])
    mode = S["mode"]
    n = S["words"]
    if mode == "single" and n > 1:
        mode = "word"                                   # user asked for several words at once
    if mode == "single":
        n = 1
    rot = r"\frz%d" % S["rot"] if S.get("rot") else ""
    base_c, hi_c = _inl(COLORS[S["color"]]), _inl(COLORS[S["hi"]])
    t0 = lambda x: max(x - offset, 0.0)
    events, gi = [], 0
    nxt_start = {id(w): (ws[k + 1]["start"] if k + 1 < len(ws) else None) for k, w in enumerate(ws)}

    def hi_at(t):
        """Highlight colour at output time t: the video's most contrasting colour, or the fixed one."""
        if S["auto"] and palettes:
            return _inl(palette_at(palettes, t)[0])
        return hi_c

    def clamp(w, b):
        """Never let an event run into the next word's event (would draw two lines on top of each other)."""
        nx = nxt_start.get(id(w))
        return min(b, nx) if nx is not None and nx > w["start"] + 0.05 else b

    def add(layer, a, b, text):
        a = t0(a)
        b = max(t0(b), a + 0.05)
        events.append(f"Dialogue: {layer},{_ts(a)},{_ts(b)},S,,0,0,0,,{text}")

    if mode == "typo":
        spin = (-2, 2, 0, -1, 1)
        for ci, chunk in enumerate(_chunks(ws, n)):
            keys = [_is_key(w["word"]) for w in chunk]
            if not any(keys):
                keys[max(range(len(chunk)), key=lambda i: len(chunk[i]["word"]))] = True
            if S["auto"] and palettes:
                pal = [_inl(c) for c in palette_at(palettes, t0(chunk[0]["start"]))]
            elif S["auto"]:
                pal = [_inl(c) for c in DEFAULT_PALETTE]
            else:
                pal = [hi_c]
            cols, scs, ki = [], [], 0
            for key in keys:
                cols.append(pal[ki % len(pal)] if key else base_c)
                ki += 1 if key else 0
                scs.append(122 if key else 100)
            end_all = clamp(chunk[-1], chunk[-1]["end"] + 0.15)
            angle = r"\frz%d" % (S["rot"] if S.get("rot") else spin[ci % len(spin)])
            for i, w in enumerate(chunk):
                a = w["start"]
                b = chunk[i + 1]["start"] if i + 1 < len(chunk) else end_all
                parts = []
                for j, x in enumerate(chunk):
                    tag = r"\1c%s\fscx%d\fscy%d" % (cols[j], scs[j], scs[j])
                    if j > i:
                        tag += r"\alpha&HC8&"                     # not spoken yet: faint ghost, keeps the line centred
                    elif j == i and S["anim"] == "pop":
                        sc = scs[j]
                        tag += (r"\alpha&H00&\fscx%d\fscy%d\t(0,110,\fscx%d\fscy%d)\t(110,190,\fscx%d\fscy%d)"
                                % (sc * .6, sc * .6, sc * 1.1, sc * 1.1, sc, sc))
                    elif j == i and S["anim"] == "fade":
                        tag += r"\alpha&HFF&\t(0,150,\alpha&H00&)"
                    else:
                        tag += r"\alpha&H00&"
                    parts.append("{%s%s}%s" % (angle if j == 0 else "", tag, x["word"]))
                add(0, a, b, " ".join(parts))
        return head + "\n".join(events) + "\n"

    for chunk in _chunks(ws, n):
        if mode in ("sweep", "fill"):
            tag = r"\kf" if mode == "fill" else r"\k"
            parts = []
            for i, w in enumerate(chunk):
                nxt = chunk[i + 1]["start"] if i + 1 < len(chunk) else w["end"]
                cs = max(int(round((nxt - w["start"]) * 100)), 1)   # include gap to next word -> no drift
                parts.append("{%s%d}%s" % (tag, cs, w["word"]))
            pre = _anim_tags(S["anim"], True, S) + rot
            if S["auto"] and palettes:
                pre += r"\1c" + hi_at(t0(chunk[0]["start"]))
            add(0, chunk[0]["start"], clamp(chunk[-1], chunk[-1]["end"]), ("{%s}" % pre if pre else "") + " ".join(parts))
            continue

        for i, w in enumerate(chunk):
            a = w["start"]
            last = i + 1 == len(chunk)
            if last or chunk[i + 1]["start"] - w["end"] > 0.4:
                b = w["end"] + 0.12
            else:
                b = chunk[i + 1]["start"]
            b = clamp(w, b)
            pre = _anim_tags(S["anim"], i == 0, S) + rot
            hc = hi_at(t0(a))
            if mode == "single":
                col = hc if gi % 2 else base_c             # alternate colours word by word
                gi += 1
                pre = _anim_tags(S["anim"], True, S) + rot + r"\1c" + col
                add(0, a, b, "{%s}%s" % (pre, w["word"]))
                continue
            if mode == "box":
                act = r"\1c&H000000&\3c%s\bord%.1f" % (hc, ol * 1.9)
                idle = r"\1c%s\3c&H000000&\bord%.1f" % (base_c, ol)
            elif mode == "neon":
                act = r"\1c&HFFFFFF&\1a&H00&"
                idle = r"\1c%s\1a&H70&" % base_c                  # idle words dimmed, active one lit
            else:                                            # word
                act = r"\1c" + hc
                idle = r"\1c" + base_c
            pieces = [("{%s}" % (act if j == i else idle)) + x["word"] for j, x in enumerate(chunk)]
            body = " ".join(pieces)
            if mode == "neon":
                big = ol * 5 + 6
                blur = 10 if vertical else 7
                gl = [("{\\bord%.1f\\3a&H30&}" % big if j == i else "{\\bord2\\3a&HA0&}") + x["word"]
                      for j, x in enumerate(chunk)]
                head_g = r"{\blur%d\3c%s\1a&HFF&%s}" % (blur, hc, rot)
                add(0, a, b, head_g + " ".join(gl))
                add(1, a, b, "{\\blur1%s\\3c%s}" % (pre, hc) + body)
            else:
                add(1, a, b, ("{%s}" % pre if pre else "") + body)
    return head + "\n".join(events) + "\n"


# ───────────── .srt export ─────────────
def _srt_ts(sec):
    ms = max(int(round(sec * 1000)), 0)
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(words, offset=0.0) -> str:
    out = []
    for idx, chunk in enumerate(_chunks(_clean(words), 8), 1):
        s = max(chunk[0]["start"] - offset, 0.0)
        e = max(chunk[-1]["end"] - offset, s + 0.05)
        out.append(f"{idx}\n{_srt_ts(s)} --> {_srt_ts(e)}\n{' '.join(x['word'] for x in chunk)}\n")
    return "\n".join(out)


# ───────────── preview image (Subtitle Studio) ─────────────
_SAMPLE = ["Yeh", "video", "bilkul", "alag", "hai"]


async def render_preview(dst, style="classic", cfg=None, vertical=True) -> Path:
    """One PNG frame of the chosen style on a dark background (needs ffmpeg + libass)."""
    dst = Path(dst)
    words = [{"word": t, "start": 0.2 + i * 0.45, "end": 0.2 + i * 0.45 + 0.4} for i, t in enumerate(_SAMPLE)]
    ass = dst.with_suffix(".ass")
    bg = bytes([0x2f, 0x3e, 0x5c]) * 64                       # same colour as the preview background below
    pal = [(0.0, palette_from_frame(bg))]                     # what auto-colour would pick for this background
    ass.write_text(build_ass(words, style, vertical=vertical, cfg=cfg, palettes=pal), encoding="utf-8")
    w, h = (540, 960) if vertical else (960, 540)
    fdir = f":fontsdir='{FONTS_DIR}'" if FONTS_DIR.is_dir() else ""
    qa = str(ass).replace("\\", "/").replace("'", r"\'")
    vf = f"ass=filename='{qa}'{fdir},scale={w}:{h}"
    src = f"color=c=0x2f3e5c:s={'1080x1920' if vertical else '1920x1080'}:r=10:d=3"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src, "-vf", vf,
           "-ss", "1.35", "-frames:v", "1", str(dst)]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.PIPE)
    _, err = await asyncio.wait_for(proc.communicate(), 60)
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError("preview failed: " + err.decode(errors="ignore")[-300:])
    return dst
