"""ffmpeg helpers. Every call is a subprocess, so the event loop never blocks."""
import asyncio
import json
import logging
from subtitles import FONTS_DIR
import os
log = logging.getLogger(__name__)
PRESET = os.getenv("X264_PRESET", "veryfast")   # ultrafast = fastest, bigger file

# 9:16 reframe filter chains (all output 1080x1920).
_CROP = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
_ZOOM = "scale=1296:2304:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
_BLUR = ("[0:v]split=2[a][b];"
         "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:4[bg];"
         "[b]scale=1080:-2[fg];"
         "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p")

# 16:9 (1920x1080) frame for any source: the video is fitted inside, the free area is filled with a blurred copy.
_WIDE = ("[0:v]split=2[a][b];"
         "[a]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,boxblur=40:4[bg];"
         "[b]scale=1920:1080:force_original_aspect_ratio=decrease[fg];"
         "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p")


def _q(p) -> str:
    """Quote a path for use inside an ffmpeg filter argument."""
    return str(p).replace("\\", "/").replace("'", r"\'")


async def _run_prog(cmd, total, bar, stage):
    """Run ffmpeg with `-progress pipe:1`, feed the progress bar, raise on failure.
    stderr is drained concurrently (otherwise a full pipe would deadlock ffmpeg)."""
    cmd = [cmd[0], "-nostats", "-loglevel", "error", *cmd[1:]]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE)
    err_task = asyncio.create_task(proc.stderr.read())
    try:
        async for raw in proc.stdout:
            line = raw.decode(errors="ignore").strip()
            if bar and line.startswith("out_time_ms="):
                try:
                    await bar.update(current=int(line.split("=")[1]) / 1_000_000, stage=stage)
                except ValueError:
                    pass
        await proc.wait()
    except asyncio.CancelledError:
        proc.kill()
        raise
    err = (await err_task).decode(errors="ignore")
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + err[-500:])


async def _run_simple(cmd, timeout=900):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                 stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("ffmpeg timeout")
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + err.decode(errors="ignore")[-500:])


async def render(src, dst, dur, reframe=None, ass=None, bar=None):
    """ONE encode pass: trim to `dur`, optional 9:16 reframe (blur/crop/zoom), optional burned-in .ass.
    `src` is the section yt-dlp already downloaded (its timeline starts at 0)."""
    fdir = f":fontsdir='{_q(FONTS_DIR)}'" if FONTS_DIR.is_dir() else ""
    ass_f = f"ass=filename='{_q(ass)}'{fdir}" if ass else None
    shorts = reframe in ("blur", "crop", "zoom")
    if reframe in ("blur", "wide"):               # blur = 9:16 Short, wide = 16:9 (both with a blurred background)
        graph = (_WIDE if reframe == "wide" else _BLUR) + (f",{ass_f}" if ass_f else "") + "[v]"
        vcmd = ["-filter_complex", graph, "-map", "[v]", "-map", "0:a?"]
    elif shorts:
        vf = (_CROP if reframe == "crop" else _ZOOM) + (f",{ass_f}" if ass_f else "") + ",format=yuv420p"
        vcmd = ["-vf", vf, "-map", "0:v:0", "-map", "0:a?"]
    elif ass_f:
        # even width/height: libx264 + yuv420p refuse odd sizes (user uploads can have any size)
        vcmd = ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2," + ass_f + ",format=yuv420p", "-map", "0:v:0", "-map", "0:a?"]
    else:
        vcmd = ["-pix_fmt", "yuv420p", "-map", "0:v:0", "-map", "0:a?"]
    cmd = ["ffmpeg", "-y", "-i", str(src), *vcmd, "-t", str(dur)]
    if shorts:
        cmd += ["-r", "30"]
    cmd += ["-c:v", "libx264", "-preset", PRESET, "-threads", "0", "-crf", "20" if shorts else "18",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            "-progress", "pipe:1", str(dst)]
    await _run_prog(cmd, dur, bar, "Encoding")


async def change_speed(src, dst, speed, bar=None, dur=60):
    cmd = ["ffmpeg", "-y", "-i", str(src),
           "-vf", f"setpts={1 / speed:.6f}*PTS", "-af", f"atempo={speed}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
           "-progress", "pipe:1", str(dst)]
    await _run_prog(cmd, max(dur / speed, 1), bar, "Speed")


async def audio_only(src, dst, dur=None):
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vn"]
    if dur:
        cmd += ["-t", str(dur)]
    cmd += ["-c:a", "libmp3lame", "-b:a", "192k", str(dst)]
    await _run_simple(cmd, 600)


async def remove_silence(src, dst):
    cmd = ["ffmpeg", "-y", "-i", str(src),
           "-af", "silenceremove=start_periods=1:start_threshold=-50dB:stop_periods=-1:"
                  "stop_threshold=-50dB:stop_duration=0.6",
           "-c:v", "copy", str(dst)]
    await _run_simple(cmd)


async def add_music(src, music, dst, volume=0.3):
    cmd = ["ffmpeg", "-y", "-i", str(src), "-i", str(music),
           "-filter_complex", f"[1:a]volume={volume}[m];[0:a][m]amix=inputs=2:duration=first[a]",
           "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", str(dst)]
    await _run_simple(cmd)


async def apply_watermark(src, dst, text):
    safe = "".join(ch for ch in text if ch.isalnum() or ch in " @._-")[:40]
    cmd = ["ffmpeg", "-y", "-i", str(src),
           "-vf", f"drawtext=text='{safe}':fontcolor=white@0.7:fontsize=32:x=w-tw-20:y=h-th-20",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "copy", str(dst)]
    await _run_simple(cmd)


async def probe_info(path) -> dict:
    """{'w','h','dur'} of a video as it is DISPLAYED (phone videos carry a rotation flag). Missing values are 0."""
    out = {"w": 0, "h": 0, "dur": 0.0}
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-of", "json",
            "-show_entries", "stream=width,height:stream_tags=rotate:stream_side_data=rotation:format=duration",
            str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        raw, _ = await asyncio.wait_for(proc.communicate(), 60)
        d = json.loads(raw.decode() or "{}")
        st = (d.get("streams") or [{}])[0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        rot = st.get("tags", {}).get("rotate")
        for sd in st.get("side_data_list") or []:
            rot = sd.get("rotation", rot)
        try:
            if rot is not None and abs(int(float(rot))) % 180 == 90:
                w, h = h, w
        except (TypeError, ValueError):
            pass
        out.update(w=w, h=h, dur=float((d.get("format") or {}).get("duration") or 0))
    except Exception:
        pass
    return out


async def probe_resolution(path) -> str | None:
    """'1080x1920' for a video file, None if unknown."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        res = out.decode().strip()
        return res if "x" in res else None
    except Exception:
        return None
