"""截图回归：不带音频渲染示例（时间按字数估算，结果确定），取每个 beat 的末帧，和基准图比像素差。

    animind-kit regress                 # 比对，有差异时退出码 1
    animind-kit regress --update        # 用当前渲染结果覆盖基准图
    animind-kit regress -j 4            # 4 个进程并行渲染各示例（默认 CPU 核数，最多 8；-j 1 串行）；结果和串行一样

基准图放在 tests/baseline/<平台>/<示例名>/<beat>.png（480p，平台是 darwin / linux）。
不同平台的字体光栅化有细微差别，所以各存一套；某个平台还没有基准图时只提示（status=new），不判失败，
把 build/regress/ 里的渲染结果检查过后放进对应目录即可。
阈值：平均每通道差 < 2（0–255），差超过 24 的像素 < 1%。
"""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
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
DEFAULT_JOBS_CAP = 8


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
    """渲染（不用 beat 缓存：regress 守的就是渲染器本身，缓存键万一漏了什么，命中旧分段会把回归藏起来），取每个 beat 的末帧。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / f"{spec.stem}.mp4"
    summary = render(spec, video, None, "l", log=lambda *_: None, backend="moxian", cache=False)
    frames = {}
    parts = video.parent / f".build-{spec.stem}-moxian" / "parts"
    for beat in summary["beats"]:
        png = out_dir / f"{beat}.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.2", "-i", str(parts / f"{beat}.mp4"),
                        "-frames:v", "1", str(png)], check=True)
        frames[beat] = png
    log(f"渲染 {spec.name}：{len(frames)} 段，版式问题 {len(summary['layout_problems'])} 条")
    return frames


def default_jobs() -> int:
    """默认并行数：CPU 核数，最多 DEFAULT_JOBS_CAP 个（每个进程常驻几百 MB，另带一个 ffmpeg 编码进程）。"""
    return max(1, min(os.cpu_count() or 1, DEFAULT_JOBS_CAP))


def _render_example(spec: Path, work: Path) -> tuple[dict[str, Path], list[str]]:
    """渲染一个示例（在子进程里也行）：返回末帧和要打印的日志行，由主进程按示例顺序打印。"""
    lines: list[str] = []
    frames = end_frames(spec, work / spec.name.removesuffix(".scene.yaml"), lines.append)
    _publish_tex()
    return frames, lines


# 墨线的公式缓存（.moxian-cache/tex，相对当前目录）先查 svg 在不在、不在才编译并直接写到那里：
# 两个进程同时编译同一个公式时，一个可能读到另一个写了一半的 svg。所以并行时每个子进程用自己的目录
# （开始时硬链接一份共享缓存里已有的 svg），每渲染完一个示例把新编译出的 svg 原子地（临时文件 + 改名）放回共享缓存。
_TEX_SHARED: Path | None = None
_TEX_PRIVATE: Path | None = None


def _worker_init(shared: str, jobs_dir: str) -> None:
    global _TEX_SHARED, _TEX_PRIVATE
    from moxian.text import tex

    _TEX_SHARED = Path(shared)
    _TEX_PRIVATE = Path(jobs_dir) / str(os.getpid())
    _TEX_PRIVATE.mkdir(parents=True, exist_ok=True)
    for svg in _TEX_SHARED.glob("*.svg") if _TEX_SHARED.exists() else []:
        try:
            os.link(svg, _TEX_PRIVATE / svg.name)
        except OSError:
            shutil.copyfile(svg, _TEX_PRIVATE / svg.name)
    tex.CACHE = _TEX_PRIVATE


def _publish_tex() -> None:
    if _TEX_SHARED is None or _TEX_PRIVATE is None:
        return
    _TEX_SHARED.mkdir(parents=True, exist_ok=True)
    for svg in _TEX_PRIVATE.glob("*.svg"):
        target = _TEX_SHARED / svg.name
        if target.exists():
            continue
        tmp = _TEX_SHARED / f".{svg.name}.{os.getpid()}.tmp"
        shutil.copyfile(svg, tmp)
        os.replace(tmp, target)


def _rendered(specs: list[Path], work: Path, jobs: int):
    """按 specs 的顺序产出每个示例的 (末帧, 日志行)。jobs=1 在本进程里逐个渲染（和以前一样）。"""
    if jobs <= 1 or len(specs) <= 1:
        for spec in specs:
            yield _render_example(spec, work)
        return
    from moxian.text import tex

    shared = tex.CACHE.resolve()
    jobs_dir = shared.parent / "tex-jobs"
    # spawn：不从已经加载了 skia / ffmpeg 管道的父进程 fork；各子进程互不共享状态
    pool = ProcessPoolExecutor(max_workers=min(jobs, len(specs)), mp_context=multiprocessing.get_context("spawn"),
                               initializer=_worker_init, initargs=(str(shared), str(jobs_dir)))
    try:
        yield from pool.map(_render_example, specs, [work] * len(specs))  # map 按提交顺序给结果；某个示例出错就在它的位置抛出
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        shutil.rmtree(jobs_dir, ignore_errors=True)


def run(update: bool = False, log=print, jobs: int | None = None) -> list[FrameDiff]:
    """jobs：并行渲染的进程数（默认 default_jobs()，1 就是串行）。比对、打印、写 report.json 都在主进程里按示例顺序做，
    所以结果、输出和串行时一模一样；各示例的渲染目录（build/regress/<示例名>）本来就互不相干。"""
    jobs = default_jobs() if jobs is None else max(1, jobs)
    results: list[FrameDiff] = []
    specs = sorted(EXAMPLES.glob("*.scene.yaml"))
    for spec, (frames, lines) in zip(specs, _rendered(specs, WORK, jobs)):
        for line in lines:
            log(line)
        name = spec.name.removesuffix(".scene.yaml")
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
