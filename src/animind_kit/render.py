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


def chapters(doc: dict, clip_seconds: list[float], video: str = "", beat_info: dict | None = None) -> dict:
    """播放器用的章节表：每个 beat 一章，起止时间按各段实际时长累加（拼接用 -c copy，成片时间轴就是各段首尾相接）。

    暂停点（预测、想一想、分段）在这一段末尾画面定住的那 hold 秒开始：旁白之后补的 hold 秒总在段尾。
    style.segmented 为真时每个 beat 末尾都是分段点。
    beat_info（墨线后端给出）里有每段的子步定格（step），写成章节的 steps，并汇总进顶层 stops。
    """
    beat_info = beat_info or {}
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
        chapter["steps"] = [{"time": round(start + st["time"], 3), "hold": st["hold"],
                             "resume": round(start + st["time"] + st["hold"], 3), "at": st["at"]}
                            for st in beat_info.get(beat["id"], {}).get("steps", [])]
        out.append(chapter)
        t = end
    return {"id": meta.get("id"), "title": meta.get("title"), "video": video, "duration": round(t, 3),
            "segmented": segmented, "chapters": out, "stops": stops(out)}


def stops(chapters_list: list[dict]) -> list[dict]:
    """所有停顿点按时间排好：子步定格（视频里已经定住，连续播放时可跳过 time..resume）、
    预测 / 想一想暂停（播放器在 time 暂停，带题目引用 app_item）、分段点（beat 末尾，分段模式下播放器暂停）。"""
    out = []
    for c in chapters_list:
        for st in c["steps"]:
            out.append({"time": st["time"], "kind": "step", "beat": c["id"], "hold": st["hold"], "resume": st["resume"]})
        if "pause" in c:
            p = c["pause"]
            item = {"time": p["time"], "kind": p["type"], "beat": c["id"], "hold": p["hold"], "resume": c["end"]}
            if p.get("prompt"):
                item["prompt"] = p["prompt"]
            if "app_item" in p:
                item["app_item"] = p["app_item"]
            out.append(item)
        if c["segment_end"]:
            out.append({"time": c["end"], "kind": "segment_end", "beat": c["id"]})
    return sorted(out, key=lambda x: x["time"])


def write_chapters(doc: dict, clip_seconds: list[float], out: Path, beat_info: dict | None = None,
                   extra: dict | None = None) -> Path:
    path = chapters_path(out)
    data = chapters(doc, clip_seconds, out.name, beat_info)
    data.update(extra or {})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_subtitles(doc: dict, out: Path, clip_seconds: list[float], beat_info: dict, theme, burn: bool, log) -> dict:
    """按字幕模式（subtitles.mode）导出：都写 <名>.vtt / <名>.srt（双语时每条两行）。

    burned（默认）：<名>.mp4 的画面里烧进字幕；另存 <名>.clean.mp4（画面干净，带软字幕轨）。
    soft：<名>.mp4 封装软字幕轨；burn（--burn-subtitles）时另出 <名>.burned.mp4。none：什么都不出。
    """
    import shutil

    from . import subtitles

    mode = subtitles.mode(doc)
    if mode == "none":
        return {}
    starts = [sum(clip_seconds[:i]) for i in range(len(clip_seconds))]
    cues = subtitles.build(doc, starts, beat_info)
    vtt, srt = subtitles.write(cues, out)
    bilingual = any(c.en for c in cues)
    info = {"mode": mode, "vtt": vtt.name, "srt": srt.name, "cues": len(cues), "bilingual": bilingual}
    work = out.parent / f".build-{out.stem}-subtitles"
    if mode == "burned":
        clean = out.with_name(f"{out.stem}.clean.mp4")
        shutil.copyfile(out, clean)
        subtitles.mux_soft(clean, srt)
        burning = out.with_name(f"{out.stem}.burning.mp4")
        subtitles.burn(out, cues, burning, theme.BG, theme.FG, work)
        burning.replace(out)
        info.update(burned=out.name, clean=clean.name, embedded_in=clean.name)
        log(f"字幕 {len(cues)} 条{'（中英双语）' if bilingual else ''} → 已烧进 {out.name}；"
            f"{vtt.name}、{srt.name}；画面干净的 {clean.name} 带软字幕轨")
        return info
    subtitles.mux_soft(out, srt)
    info["embedded"] = "mov_text"
    if burn:
        burned = out.with_name(f"{out.stem}.burned.mp4")
        subtitles.burn(out, cues, burned, theme.BG, theme.FG, work)
        info["burned"] = burned.name
    log(f"字幕 {len(cues)} 条 → {vtt.name}、{srt.name}（已封装为软字幕轨）" + ("，烧录副本 " + info["burned"] if burn else ""))
    return info


def beat_info_estimated(doc: dict, audio_dir: Path | None) -> dict:
    """没有子步定格的后端（Manim）：句子时刻直接取 timing.json。"""
    from . import timing

    out = {}
    for beat in doc["beats"]:
        bt = timing.load(beat, audio_dir)
        out[beat["id"]] = {"cues": [{"start": s.start, "end": s.end, "marks": s.marks, "words": s.words} for s in bt.sentences],
                           "steps": []}
    return out


def load_spec(spec: Path, lang: str = "zh") -> dict:
    """读场景文件；lang 不是中文时换成那种语言的版本（scene_spec.localize）。"""
    from scene_spec import LocalizeError, localize

    try:
        return localize(load_file(spec), lang)
    except LocalizeError as exc:
        raise SystemExit(f"{spec.name} 没有完整的 {lang} 版：{exc}") from exc


def inspect(spec: Path, audio_dir: Path | None, out_dir: Path | None, beats: list[str] | None = None,
            quality: str = "m", log=print, lang: str = "zh") -> list:
    """不编码视频的快速版式检查（墨线后端）：打印问题列表；给了 out_dir 就写分镜图。

    采样点和 render 相同（每批动作结束后的第一帧、beat 末帧），quality 只决定帧率（采样落在哪一帧）。
    """
    from .backends.moxian_backend import MoxianCompiler

    doc = load_spec(spec, lang)
    report = check(doc)
    for issue in report.issues:
        log(str(issue))
    if not report.ok:
        raise SystemExit("scene-spec 检查未通过")
    unknown = set(beats or []) - {b["id"] for b in doc["beats"]}
    if unknown:
        raise SystemExit(f"没有这些 beat：{', '.join(sorted(unknown))}")
    theme = load_theme(doc.get("style", {}).get("theme", "paper"))
    compiler = MoxianCompiler(doc, theme, audio_dir, log, quality)
    compiler.inspect(set(beats) if beats else None, out_dir)
    log(f"inspect {spec.name}：版式问题 {len(compiler.problems)} 条" + (f"，分镜图在 {out_dir}" if out_dir else ""))
    return compiler.problems


def render(spec: Path, out: Path, audio_dir: Path | None, quality: str = "l", only: list[str] | None = None, log=print,
           backend: str = "manim", burn_subtitles: bool = False, theme_name: str | None = None,
           subtitles: str | None = None, lang: str = "zh") -> dict:
    """subtitles：覆盖 style.subtitles（burned / soft / none）；版式（字号下限）也按覆盖后的模式算。

    lang：渲染哪种语言的版本（旁白音频要用同一个 lang 合成）；英文版字幕只有英文一行。
    """
    doc = load_spec(spec, lang)
    report = check(doc)
    for issue in report.issues:
        log(str(issue))
    if not report.ok:
        raise SystemExit("scene-spec 检查未通过，不渲染")
    if subtitles:
        doc = {**doc, "style": {**doc.get("style", {}), "subtitles": subtitles}}

    build = out.parent / f".build-{spec.stem}-{backend}"
    theme = load_theme(theme_name or doc.get("style", {}).get("theme", "paper"))

    if backend == "moxian":
        from .backends.moxian_backend import MoxianCompiler

        summary = MoxianCompiler(doc, theme, audio_dir, log, quality).render_all(out, build)
        build.mkdir(parents=True, exist_ok=True)
        info = summary.pop("beat_info")
        subs = export_subtitles(doc, out, summary["clip_seconds"], info, theme, burn_subtitles, log)
        summary["subtitles"] = subs
        summary["chapters"] = str(write_chapters(doc, summary["clip_seconds"], out, info, {"subtitles": subs}))
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
    clip_seconds = [probe_seconds(c) for c in clips]
    info = beat_info_estimated(doc, audio_dir)
    summary["subtitles"] = export_subtitles(doc, out, clip_seconds, info, theme, burn_subtitles, log)
    summary["chapters"] = str(write_chapters(doc, clip_seconds, out, info, {"subtitles": summary["subtitles"]}))
    (build / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"wrote {out} {summary['seconds']}s；版式问题 {len(compiler.problems)} 条")
    return summary
