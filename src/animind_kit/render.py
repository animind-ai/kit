"""渲染：逐 beat 跑 Manim，和旁白合成，拼成一支成片。"""

from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

from scene_spec import check
from scene_spec.loader import load_file

from .theme import theme as load_theme

QUALITY = {"l": "low_quality", "m": "medium_quality", "h": "high_quality"}


def probe_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as handle:
        return handle.getnframes() / handle.getframerate()


def find_video(media: Path, name: str) -> Path:
    matches = [p for p in (media / "videos").rglob(f"{name}.mp4") if "partial_movie" not in p.parts]
    if not matches:
        raise FileNotFoundError(f"没有渲染出 {name}.mp4")
    return max(matches, key=lambda p: p.stat().st_mtime)


SHOWS = ("misconception", "non_example", "landing")


def chapters_path(out: Path) -> Path:
    """成片 x.mp4 旁边的章节文件 x.chapters.json。"""
    return out.with_suffix(".chapters.json")


def chapters(doc: dict, clip_seconds: list[float], video: str = "") -> dict:
    """播放器用的章节表：每个 beat 一章，起止时间按各段实际时长累加（拼接用 -c copy，成片时间轴就是各段首尾相接）。

    暂停点（预测、想一想、分段）在这一段末尾画面定住的那 hold 秒开始：旁白之后补的 hold 秒总在段尾。
    style.segmented 为真时每个 beat 末尾都是分段点。
    """
    style = doc.get("style", {})
    meta = doc.get("meta", {})
    segmented = bool(style.get("segmented", False))
    out, t = [], 0.0
    for beat, seconds in zip(doc["beats"], clip_seconds, strict=True):
        start, end = t, t + seconds
        shows = beat.get("shows", [])
        shows = [shows] if isinstance(shows, str) else shows
        chapter = {"id": beat["id"], "title": beat.get("title", beat["id"]),
                   "start": round(start, 3), "end": round(end, 3),
                   "shows": [k for k in shows if k in SHOWS]}
        if "pause" in beat:
            p = beat["pause"]
            hold = float(p.get("hold", 2))
            pause = {"type": p.get("type"), "prompt": p.get("prompt"), "time": round(max(start, end - hold), 3), "hold": hold}
            if "app_item" in p:
                pause["app_item"] = p["app_item"]
            chapter["pause"] = pause
        chapter["segment_end"] = segmented or beat.get("pause", {}).get("type") == "segment"
        out.append(chapter)
        t = end
    return {"id": meta.get("id"), "title": meta.get("title"), "video": video, "duration": round(t, 3),
            "segmented": segmented, "chapters": out}


def write_chapters(doc: dict, clip_seconds: list[float], out: Path) -> Path:
    path = chapters_path(out)
    path.write_text(json.dumps(chapters(doc, clip_seconds, out.name), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def render(spec: Path, out: Path, audio_dir: Path | None, quality: str = "l", only: list[str] | None = None, log=print,
           backend: str = "manim") -> dict:
    doc = load_file(spec)
    report = check(doc)
    for issue in report.issues:
        log(str(issue))
    if not report.ok:
        raise SystemExit("scene-spec 检查未通过，不渲染")

    build = out.parent / f".build-{spec.stem}-{backend}"
    theme = load_theme(doc.get("style", {}).get("theme", "paper"))

    if backend == "moxian":
        from .backends.moxian_backend import MoxianCompiler

        summary = MoxianCompiler(doc, theme, audio_dir, log, quality).render_all(out, build)
        build.mkdir(parents=True, exist_ok=True)
        summary["chapters"] = str(write_chapters(doc, summary["clip_seconds"], out))
        (build / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"wrote {out} {summary['seconds']}s；版式问题 {len(summary['layout_problems'])} 条")
        return summary

    from manim import tempconfig

    from .backends.manim_backend import Compiler

    media, parts = build / "media", build / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    compiler = Compiler(doc, theme, audio_dir, log)

    beats = doc["beats"]
    names = [b["id"] for b in beats]
    for beat in beats:
        if only and beat["id"] not in only:
            # 没重渲的 beat 也要走一遍状态更新，否则后面的 beat 起始画面不对；这里用极低画质快速过一遍
            pass
        log(f"render {beat['id']}")
        cls = compiler.scene_class(beat)
        with tempconfig({
            "quality": QUALITY[quality], "media_dir": str(media), "disable_caching": True,
            "background_color": theme.BG,
            "output_file": beat["id"], "verbosity": "WARNING", "progress_bar": "none",
        }):
            cls().render()

    clips = []
    for beat in beats:
        video = find_video(media, beat["id"])
        clip = parts / f"{beat['id']}.mp4"
        audio = audio_dir / f"{beat['id']}.wav" if audio_dir else None
        video_seconds = probe_seconds(video)
        if audio and audio.exists():
            target = max(video_seconds, wav_seconds(audio) + 0.28)
            pad = max(0.0, target - video_seconds)
            cmd = [
                "ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(audio),
                "-filter_complex", f"[0:v]tpad=stop_mode=clone:stop_duration={pad:.3f}[v];[1:a]apad=whole_dur={target:.3f}[a]",
                "-map", "[v]", "-map", "[a]", "-t", f"{target:.3f}",
            ]
        else:
            target = video_seconds
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                   "-map", "0:v", "-map", "1:a", "-t", f"{target:.3f}"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(clip)]
        subprocess.run(cmd, check=True)
        clips.append(clip)
        log(f"muxed {beat['id']} {target:.1f}s")

    playlist = parts / "list.txt"
    playlist.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(playlist), "-c", "copy", str(out)], check=True)
    summary = {"out": str(out), "seconds": round(probe_seconds(out), 1), "beats": names, "layout_problems": compiler.problems}
    summary["chapters"] = str(write_chapters(doc, [probe_seconds(c) for c in clips], out))
    (build / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"wrote {out} {summary['seconds']}s；版式问题 {len(compiler.problems)} 条")
    return summary
