"""Word-level karaoke subtitles (.ass) and .srt export."""
import re

# Font sizes / margins for the two canvas types. For 9:16 the captions sit at ~25 % from the
# bottom so they stay clear of the Shorts UI (title, buttons) — the "safe zone".
_LAYOUT = {
    True:  {"res": (1080, 1920), "size": {"classic": 64, "bold": 82, "minimal": 50},
            "marginv": {"classic": 430, "bold": 480, "minimal": 400}, "marginlr": 90},
    False: {"res": (1920, 1080), "size": {"classic": 54, "bold": 70, "minimal": 44},
            "marginv": {"classic": 70, "bold": 90, "minimal": 60}, "marginlr": 120},
}
STYLES = {"classic": "Default", "bold": "BoldYellow", "minimal": "Minimal"}
_CHUNK = {"classic": 5, "bold": 3, "minimal": 6}
_FONT = "Noto Sans"


def _header(vertical: bool) -> str:
    L = _LAYOUT[vertical]
    w, h = L["res"]
    s, mv, lr = L["size"], L["marginv"], L["marginlr"]
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{_FONT},{s['classic']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,{lr},{lr},{mv['classic']},1
Style: BoldYellow,{_FONT},{s['bold']},&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,2,{lr},{lr},{mv['bold']},1
Style: Minimal,{_FONT},{s['minimal']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,{lr},{lr},{mv['minimal']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


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


def _line(chunk, style, offset):
    start = max(chunk[0]["start"] - offset, 0.0)
    end = max(chunk[-1]["end"] - offset, start + 0.05)
    parts = []
    for i, w in enumerate(chunk):
        nxt = chunk[i + 1]["start"] if i + 1 < len(chunk) else w["end"]
        cs = max(int(round((nxt - w["start"]) * 100)), 1)   # include gap to next word -> no drift
        parts.append(r"{\k%d}%s" % (cs, w["word"]))
    return f"Dialogue: 0,{_ts(start)},{_ts(end)},{style},,0,0,0,,{' '.join(parts)}"


def build_ass(words, style="classic", offset=0.0, vertical=True) -> str:
    """`vertical=True` for 9:16 output (1080x1920), False for the original 16:9 frame."""
    key = style if style in STYLES else "classic"
    lines = [_header(vertical)]
    for chunk in _chunks(_clean(words), _CHUNK[key]):
        lines.append(_line(chunk, STYLES[key], offset))
    return "\n".join(lines) + "\n"


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
