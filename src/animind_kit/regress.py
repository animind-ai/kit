"""截图回归：不带音频渲染示例（时间按字数估算，结果确定），取每个 beat 的末帧，和基准图比像素差。

    animind-kit regress                 # 比对，有差异时退出码 1
    animind-kit regress --update        # 用当前渲染结果覆盖基准图

基准图放在 tests/baseline/<平台>/<示例名>/<beat>.png（480p，平台是 darwin / linux）。
不同平台的字体光栅化有细微差别，所以各存一套；某个平台还没有基准图时只提示（status=new），不判失败，
把 build/regress/ 里的渲染结果检查过后放进对应目录即可。
阈值：平均每通道差 < 2（0–255），差超过 24 的像素 < 1%。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import skia

from .render import render

KIT = Path(__file__).resolve().parents[2]
EXAMPLES = KIT.parent / "scene-spec" / "examples"
BASELINE = KIT / "tests" / "baseline" / sys.platform
WORK = KIT / "build" / "regress"
MEAN_LIMIT = 2.0
PIXEL_LIMIT = 24
FRACTION_LIMIT = 0.01


@dataclass
class FrameDiff:
    example: str
    beat: str
    mean: float
    fraction: float
    status: str  # ok / changed / new / missing

    def __str__(self) -> str:
        return f"{self.status:8} {self.example}/{self.beat}: 平均差 {self.mean:.2f}，变化像素 {self.fraction:.2%}"


def load_png(path: Path) -> np.ndarray:
    image = skia.Image.open(str(path))
    return image.convert(colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kUnpremul_AlphaType).toarray()[:, :, :3].astype(np.int16)


def compare(a: Path, b: Path) -> tuple[float, float]:
    x, y = load_png(a), load_png(b)
    if x.shape != y.shape:
        return 255.0, 1.0
    d = np.abs(x - y)
    return float(d.mean()), float((d.max(axis=2) > PIXEL_LIMIT).mean())


def end_frames(spec: Path, out_dir: Path, log) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / f"{spec.stem}.mp4"
    summary = render(spec, video, None, "l", log=lambda *_: None, backend="moxian")
    frames = {}
    parts = video.parent / f".build-{spec.stem}-moxian" / "parts"
    for beat in summary["beats"]:
        png = out_dir / f"{beat}.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.2", "-i", str(parts / f"{beat}.mp4"),
                        "-frames:v", "1", str(png)], check=True)
        frames[beat] = png
    log(f"渲染 {spec.name}：{len(frames)} 段，版式问题 {len(summary['layout_problems'])} 条")
    return frames


def run(update: bool = False, log=print) -> list[FrameDiff]:
    results: list[FrameDiff] = []
    for spec in sorted(EXAMPLES.glob("*.scene.yaml")):
        name = spec.name.removesuffix(".scene.yaml")
        frames = end_frames(spec, WORK / name, log)
        base_dir = BASELINE / name
        if update:
            if base_dir.exists():
                shutil.rmtree(base_dir)
            base_dir.mkdir(parents=True)
            for beat, png in frames.items():
                shutil.copy(png, base_dir / f"{beat}.png")
            log(f"已更新基准图：{base_dir}")
            continue
        for beat, png in frames.items():
            base = base_dir / f"{beat}.png"
            if not base.exists():
                results.append(FrameDiff(name, beat, 0.0, 0.0, "new"))
                continue
            mean, fraction = compare(base, png)
            changed = mean >= MEAN_LIMIT or fraction >= FRACTION_LIMIT
            results.append(FrameDiff(name, beat, mean, fraction, "changed" if changed else "ok"))
        for base in sorted(base_dir.glob("*.png")) if base_dir.exists() else []:
            if base.stem not in frames:
                results.append(FrameDiff(name, base.stem, 0.0, 0.0, "missing"))
    if results:
        (WORK / "report.json").write_text(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=1), encoding="utf-8")
    return results
