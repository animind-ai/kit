"""beat 级增量渲染缓存（墨线后端）：每个 beat 按内容算一个 SHA256，parts/<beat>.mp4 旁边的 <beat>.hash 对得上就不再渲染。

键里放的是这个 beat 的像素和音轨可能依赖的一切，宁可多放（多一次重渲）也不能漏（拿到旧画面）：

- 环境：缓存格式版本 FORMAT、Python 版本与平台、animind-kit / moxian / scene-spec / zh-math-reading / skia-python /
  numpy 的版本、ffmpeg -version 的第一行，以及这四个本地包（可编辑安装，版本号不随改动变）全部源文件的内容；
- 渲染设置：分辨率、帧率、画质档、主题（解析后的全部色值）、语言、有没有旁白目录；
- 文档里 beats 之外的全部内容（对象、组件、data、style——含字幕模式，它决定字号下限和画面缩放）；
- 往后看的整篇分析：每个对象占着哪些 beat（区域用量、叠放高度都按它算）、有没有用到镜头（决定要不要垫底色）；
- 外部文件：文档里每个 src（clip 素材、引用的数据文件；目录就是其中全部文件）的内容哈希；
- 之前所有 beat 的定义和旁白文件（链式）：编译器里有不在对象树上的状态（组件内部计数、叠加层、残影、move 改过的定义），
  前面任何一个 beat 改了，后面的 beat 一律重渲；
- 这个 beat 的定义、旁白文件（<beat>.wav、<beat>.timing.json 的内容）、解析出的时间（句子、书签、动作批次、定格、时长、采样点）；
- 这个 beat 排版之后、开播之前的画面状态：对象树里每个节点的世界矩阵、样式、路径、z、fixed / keep_size，
  片段节点的全部参数（含它在这个 beat 时钟上的 start），镜头，场景参数的当前值。系统字体变了、
  什么输入漏算了，只要它改变了开播时的画面，这里就会不同。

只缓存 render_beat（Skia 逐帧光栅化 + ffmpeg 编码）；命中的 beat 照样编时间线、按帧推进、做版式检查（不画），
所以后面的 beat 的起始状态、版式问题、时间报告和全量渲染完全一样。
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from importlib import import_module, metadata
from pathlib import Path

import numpy as np

FORMAT = 1  # 键的组成或 mp4 的产出方式变了就加一，旧缓存全部作废
PACKAGES = ("animind_kit", "moxian", "scene_spec", "zh_math_reading")
DISTS = ("animind-kit", "moxian", "scene-spec", "zh-math-reading", "skia-python", "numpy")


class Hasher:
    """往 SHA256 里按顺序喂带标签的字段；标签和长度一起写进去，字段之间不会串。"""

    def __init__(self) -> None:
        self.h = hashlib.sha256()

    def add(self, label: str, value: bytes | str) -> Hasher:
        data = value.encode("utf-8") if isinstance(value, str) else value
        self.h.update(f"{label}:{len(data)}:".encode())
        self.h.update(data)
        return self

    def json(self, label: str, value) -> Hasher:
        return self.add(label, canonical(value))

    def hexdigest(self) -> str:
        return self.h.hexdigest()


def canonical(value) -> str:
    """确定的 JSON：键排序；集合排序；dataclass 展开；numpy 转列表；其余取 repr。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_plain)


def _plain(value):
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=canonical)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return repr(value)


# ---------- 文件 ----------

_file_memo: dict[tuple[str, int, int], str] = {}


def file_digest(path: Path) -> str:
    """文件内容的 SHA256；同一进程里按 (路径, 大小, mtime_ns) 记住，不重复读。目录：其中全部文件（相对路径 + 内容）。"""
    path = Path(path)
    if path.is_dir():
        h = Hasher()
        for f in sorted(p for p in path.rglob("*") if p.is_file()):
            h.add("name", f.relative_to(path).as_posix()).add("file", file_digest(f))
        return h.hexdigest()
    if not path.exists():
        return "missing"
    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    if key not in _file_memo:
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                h.update(chunk)
        _file_memo[key] = h.hexdigest()
    return _file_memo[key]


def sources(value, root: Path) -> dict[str, str]:
    """文档里每个 src（字符串）指向的文件或目录 → 内容哈希。找不到的记成 missing（之后出现了键就会变）。"""
    out: dict[str, str] = {}

    def visit(v) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "src" and isinstance(x, str) and "\x00" not in x:
                    out[x] = _src_digest(root, x)
                else:
                    visit(x)
        elif isinstance(v, list):
            for x in v:
                visit(x)

    visit(value)
    return out


def _src_digest(root: Path, src: str) -> str:
    """src 可以是文件、目录，或 glob（片段的帧序列，如 sim/frame_*.png）。"""
    if any(c in src for c in "*?["):
        h = Hasher()
        try:
            matches = sorted(p for p in root.glob(src) if p.is_file())
        except (ValueError, NotImplementedError):
            matches = []
        for f in matches:
            h.add("name", f.relative_to(root).as_posix()).add("file", file_digest(f))
        return h.hexdigest() if matches else "missing"
    return file_digest(root / src)


# ---------- 环境 ----------


@functools.cache
def code_digest() -> str:
    """四个本地包目录下全部文件（源码、主题和调色板等数据）的内容。它们是可编辑安装，改了代码版本号不变。"""
    h = Hasher()
    for name in PACKAGES:
        try:
            module = import_module(name)
        except ImportError:
            h.add("package", f"{name}=absent")
            continue
        root = Path(module.__file__).parent
        for f in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
            h.add("file", f"{name}/{f.relative_to(root).as_posix()}").add("content", file_digest(f))
    return h.hexdigest()


@functools.cache
def environment() -> str:
    h = Hasher().add("format", str(FORMAT))
    h.add("python", sys.version).add("platform", f"{sys.platform} {platform.machine()}")
    for dist in DISTS:
        try:
            h.add("dist", f"{dist}={metadata.version(dist)}")
        except metadata.PackageNotFoundError:
            h.add("dist", f"{dist}=absent")
    try:
        ffmpeg = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    except (OSError, subprocess.CalledProcessError, IndexError):
        ffmpeg = "unknown"
    h.add("ffmpeg", ffmpeg)
    h.add("code", code_digest())
    return h.hexdigest()


# ---------- 画面状态 ----------


def scene_state(root, camera, params: dict) -> str:
    """对象树此刻会画出什么：每个节点（含看不见的）按绘制顺序的类型、名字、世界矩阵、生效样式、z、
    fixed / keep_size、路径的全部控制点；带外部画面的节点（片段）再加上它全部的标量参数。另加镜头和场景参数。"""
    h = hashlib.sha256()
    for node, world, style in root.walk():
        h.update(f"{type(node).__qualname__}|{node.name}|{node.z!r}|{node.fixed}|{node.keep_size}|{style!r}|".encode())
        h.update(np.ascontiguousarray(world, dtype=float).tobytes())
        path = getattr(node, "path", None)
        if path is not None:
            h.update(f"path{len(path.subpaths)}|".encode())
            for sp in path.subpaths:
                h.update(f"{sp.closed}{len(sp.segments)}|".encode())
                h.update(np.ascontiguousarray(sp.start, dtype=float).tobytes())
                for seg in sp.segments:
                    h.update(np.ascontiguousarray(seg, dtype=float).tobytes())
        if getattr(node, "paint_raster", None) is not None:
            # 片段：src、trim、rate、fit、end、start（这个 beat 时钟上的开始时刻）、plate……矩阵和子节点上面已经算了
            h.update(canonical({k: v for k, v in sorted(vars(node).items())
                                if k not in ("children", "matrix", "style") and not k.startswith("_")}).encode())
    h.update(f"camera|{camera.zoom!r}|".encode())
    h.update(np.ascontiguousarray(camera.center, dtype=float).tobytes())
    h.update(canonical({k: p.value for k, p in params.items()}).encode())
    return h.hexdigest()


# ---------- 旁注文件 ----------


def sidecar(clip: Path) -> Path:
    return clip.with_suffix(".hash")


def hit(clip: Path, key: str) -> bool:
    """clip 存在、旁注文件的键和它一样、文件大小也和写旁注时一样（没被截断或替换）。"""
    note = sidecar(clip)
    if not clip.exists() or not note.exists():
        return False
    try:
        data = json.loads(note.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("format") == FORMAT and data.get("key") == key and data.get("bytes") == clip.stat().st_size


def invalidate(clip: Path) -> None:
    """重渲之前先删旁注：渲染中途失败时留下的半个 mp4 不会被当成命中。"""
    sidecar(clip).unlink(missing_ok=True)


def record(clip: Path, key: str) -> None:
    """mp4 写完之后再写旁注；先写临时文件再改名，不会留下半个旁注。"""
    note = sidecar(clip)
    tmp = note.with_name(f"{note.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"format": FORMAT, "key": key, "bytes": clip.stat().st_size}), encoding="utf-8")
    os.replace(tmp, note)
