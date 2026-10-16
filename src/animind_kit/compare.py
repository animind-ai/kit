"""对照：同一份 scene-spec 用两个后端各渲染一遍，计时，并把每个 beat 的末帧左右拼在一起。

用法：animind-kit compare 场景文件 --audio 目录 --out 目录 [--quality l|m]
产物：<out>/manim.mp4、<out>/moxian.mp4、<out>/frames/<beat>.png（左 Manim、右墨线）、<out>/report.json
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .render import render


def end_frame(clip: Path, png: Path) -> bool:
    if not clip.exists():
        return False
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.6", "-i", str(clip), "-frames:v", "1", str(png)], check=True)
    return png.exists()


def side_by_side(left: Path, right: Path, out: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(left), "-i", str(right),
         "-filter_complex", "[0:v]scale=-2:540[l];[1:v]scale=-2:540[r];[l][r]hstack=inputs=2", str(out)],
        check=True,
    )


def compare(spec: Path, audio: Path | None, out: Path, quality: str = "l", log=print) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "frames").mkdir(exist_ok=True)
    report: dict = {"spec": str(spec), "quality": quality, "backends": {}}
    for backend in ("manim", "moxian"):
        t0 = time.time()
        summary = render(spec, out / f"{backend}.mp4", audio, quality, log=log, backend=backend)
        report["backends"][backend] = {
            "seconds_to_render": round(time.time() - t0, 1),
            "video_seconds": summary["seconds"],
            "layout_problems": summary["layout_problems"],
            "size_mb": round((out / f"{backend}.mp4").stat().st_size / 1e6, 2),
        }
    beats = [b["id"] for b in __import__("scene_spec.loader", fromlist=["load_file"]).load_file(spec)["beats"]]
    report["frames"] = []
    for beat in beats:
        frames = []
        for backend in ("manim", "moxian"):
            clip = out / f".build-{spec.stem}-{backend}" / "parts" / f"{beat}.mp4"
            png = out / "frames" / f"{beat}-{backend}.png"
            frames.append(png if end_frame(clip, png) else None)
        if all(frames):
            side_by_side(frames[0], frames[1], out / "frames" / f"{beat}.png")
            report["frames"].append(str(out / "frames" / f"{beat}.png"))
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    m, x = report["backends"]["manim"], report["backends"]["moxian"]
    log(f"Manim {m['seconds_to_render']}s / 墨线 {x['seconds_to_render']}s；版式问题 {len(m['layout_problems'])} / {len(x['layout_problems'])}；对照图 {len(report['frames'])} 张在 {out / 'frames'}")
    return report
