import asyncio, subprocess, logging
from pathlib import Path
from subtitles import build_ass
log = logging.getLogger(__name__)

async def _run_prog(cmd, total, bar, stage):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE)
    async for raw in proc.stdout:
        line = raw.decode(errors="ignore").strip()
        if line.startswith("out_time_ms="):
            try: await bar.update(current=int(line.split("=")[1]) / 1_000_000, stage=stage)
            except ValueError: pass
    await proc.wait()
    if proc.returncode != 0:
        err = (await proc.stderr.read()).decode(errors="ignore")[:500]
        raise RuntimeError(err)

async def cut_precise(src, dst, start, end, bar):
    dur = end - start
    cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", str(src), "-t", str(dur),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
           "-progress", "pipe:1", str(dst)]
    await _run_prog(cmd, dur, bar, "Encoding")

async def make_shorts(src, dst, style, bar, dur):
    if style == "crop":
        vf = "scale=-2:1920,crop=1080:1920,setsar=1"
    elif style == "zoom":
        vf = "scale=1080:-2,crop=1080:1920,zoompan=z='min(zoom+0.0015,1.5)':d=1:s=1080x1920"
    else:
        vf = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
              "crop=1080:1920,boxblur=40:4,setsar=1[bg];"
              "[0:v]scale=1080:-2[fg];"
              "[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto,format=yuv420p[v]")
    if style in ("crop", "zoom"):
        cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", vf, "-map", "0:a?",
               "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-c:a", "aac", "-b:a", "128k", "-progress", "pipe:1", str(dst)]
    else:
        cmd = ["ffmpeg", "-y", "-i", str(src), "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
               "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-c:a", "aac", "-b:a", "128k", "-progress", "pipe:1", str(dst)]
    await _run_prog(cmd, dur, bar, "Shorts encode")

async def burn_subs(src, dst, words, style, offset, bar, dur):
    ass = src.with_suffix(".ass")
    ass.write_text(build_ass(words, style, offset), encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", f"ass={ass}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-c:a", "copy", "-progress", "pipe:1", str(dst)]
    await _run_prog(cmd, dur, bar, "Subtitles")

async def change_speed(src, dst, speed, bar, dur):
    cmd = ["ffmpeg", "-y", "-i", str(src),
           "-filter_complex", f"[0:v]setpts={1/speed}*PTS[v];[0:a]atempo={speed}[a]",
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "18", "-progress", "pipe:1", str(dst)]
    await _run_prog(cmd, dur / speed, bar, "Speed")

async def audio_only(src, dst):
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vn", "-c:a", "libmp3lame", "-b:a", "192k", str(dst)]
    await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=600)

async def remove_silence(src, dst):
    cmd = ["ffmpeg", "-y", "-i", str(src),
           "-af", "silenceremove=start_periods=1:start_threshold=-50dB:stop_periods=-1:stop_threshold=-50dB:stop_duration=0.6",
           "-c:v", "copy", str(dst)]
    await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=600)

async def add_music(src, music, dst, volume=0.3):
    cmd = ["ffmpeg", "-y", "-i", str(src), "-i", str(music),
           "-filter_complex", f"[1:a]volume={volume}[m];[0:a][m]amix=inputs=2:duration=first[a]",
           "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", str(dst)]
    await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=600)

async def apply_watermark(src, dst, text):
    cmd = ["ffmpeg", "-y", "-i", str(src),
           "-vf", f"drawtext=text='{text}':fontcolor=white@0.7:fontsize=32:x=w-tw-20:y=h-th-20",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "copy", str(dst)]
    await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=600)
