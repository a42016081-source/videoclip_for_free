ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,80,80,220,1
Style: BoldYellow,Noto Sans,80,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,2,80,80,280,1
Style: Minimal,Noto Sans,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,80,80,180,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
STYLES = {"classic": "Default", "bold": "BoldYellow", "minimal": "Minimal"}

def _ts(sec):
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def build_ass(words, style="classic", offset=0.0) -> str:
    name = STYLES.get(style, "Default")
    lines = [ASS_HEADER]
    chunk = []
    for w in words:
        chunk.append(w)
        if len(chunk) >= 3:
            lines.append(_line(chunk, name, offset)); chunk = []
    if chunk: lines.append(_line(chunk, name, offset))
    return "\n".join(lines)

def _line(chunk, style, offset):
    start = chunk[0]["start"] - offset; end = chunk[-1]["end"] - offset
    parts = []
    for w in chunk:
        cs = int(max((w["end"] - w["start"]) * 100, 1))
        parts.append(r"{\k%d}%s" % (cs, w["word"]))
    return f"Dialogue: 0,{_ts(start)},{_ts(end)},{style},,0,0,0,,{' '.join(parts)}"

def build_srt(words, offset=0.0) -> str:
    out, idx, chunk = [], 1, []
    for w in words:
        chunk.append(w)
        if w["word"].endswith((".", "?", "!")) or len(chunk) >= 8:
            s = chunk[0]["start"] - offset; e = chunk[-1]["end"] - offset
            out.append(f"{idx}\n{_srt(s)} --> {_srt(e)}\n{' '.join(x['word'] for x in chunk)}\n")
            idx += 1; chunk = []
    if chunk:
        s = chunk[0]["start"] - offset; e = chunk[-1]["end"] - offset
        out.append(f"{idx}\n{_srt(s)} --> {_srt(e)}\n{' '.join(x['word'] for x in chunk)}\n")
    return "\n".join(out)

def _srt(sec):
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"