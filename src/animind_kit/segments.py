"""分段播放（墨线后端）：残影 ghost、子步定格 step、常驻 pin。语义见 scene-spec 设计稿 §7.5。

- ghost：入场那一刻给目标拍一张"快照"——把它所有可见的路径叶子按当时的世界矩阵拷出来，
  一律改成 DIM 色（文字的 BG 底色除外）、整体不透明度 0.3（可配），画在原对象后面；之后原对象怎么变，残影都不动。
  和 focus（描边框 + 兄弟元素 0.45 不透明度、颜色不变）、mark（淡金底色）都不一样：残影是灰的。
- step：子步结束。segmented 时在这一刻定格 hold 秒：画面时间轴在这里停住（Warped 时间线），
  旁白在同一时刻插入等长的静音；之后所有动作、句子、书签的时刻顺延。
- pin：记下这个对象常驻；布局求解器把它当固定占位，clear 不收它 pin 之前的装饰。
"""

from __future__ import annotations

import copy
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from moxian.anim import Animation, Restyle
from moxian.core import Group, Node, PathNode, Style, parent_matrix_of

from .regions import GAPS
from .theme import Theme

GHOST_OPACITY = 0.3
GHOST_GAP = "md"


class GhostIn(Animation):
    """残影入场：begin() 时才拍快照（编译时目标还没播到这一步），然后从 0 淡到目标不透明度。"""

    def __init__(self, holder: Group, source: Node, root: Node, theme: Theme, args: dict, duration: float = 0.5):
        super().__init__(holder, duration)
        self.holder, self.source, self.root, self.theme = holder, source, root, theme
        self.place = args.get("place", "stay")
        self.gap = GAPS[args.get("gap", GHOST_GAP)]
        self.factor = float(args.get("scale", 1.0))
        self.opacity = float(args.get("opacity", GHOST_OPACITY))

    def begin(self) -> None:
        super().begin()
        self.holder.children = snapshot(self.source, self.root, self.theme)
        self.holder.matrix = np.eye(3)
        self.holder.set_style(opacity=0.0)
        box = self.holder.bbox()
        if box is None:
            return
        if self.factor != 1.0:
            self.holder.scale(self.factor)
        b = self.holder.bbox()
        if self.place == "above":
            self.holder.shift([box.center[0] - b.center[0], box.y1 + self.gap - b.y0])
        elif self.place == "below":
            self.holder.shift([box.center[0] - b.center[0], box.y0 - self.gap - b.y1])
        elif self.place == "left_of":
            self.holder.shift([box.x0 - self.gap - b.x1, box.center[1] - b.center[1]])
        elif self.place == "right_of":
            self.holder.shift([box.x1 + self.gap - b.x0, box.center[1] - b.center[1]])

    def interpolate(self, alpha: float) -> None:
        self.holder.set_style(opacity=self.opacity * alpha)


def snapshot(source: Node, root: Node, theme: Theme) -> list[Node]:
    """source 此刻可见的路径叶子（世界坐标），改成 DIM 色。mark 垫的淡金底色不拷：灰底块只会添乱。

    文字垫的底色（填 BG、不描边：刻度、箭头名字下面那块）保持 BG 色：改成 DIM 就成了一个个灰方块；
    留着 BG 色，残影自己的网格线照样从残影的字底下过。
    """
    out: list[Node] = []
    for node, m, style in source.walk(parent_matrix_of(root, source)):
        if not isinstance(node, PathNode) or node.path.is_empty() or not style.visible():
            continue
        if style.fill == theme.MARK and style.stroke is None:
            continue
        pad = style.fill == theme.BG and style.stroke is None
        out.append(PathNode(
            path=copy.deepcopy(node.path), matrix=m.copy(),
            style=Style(fill=(theme.BG if pad else theme.DIM) if style.fill else None, fill_opacity=style.fill_opacity,
                        stroke=theme.DIM if style.stroke else None, stroke_width=style.stroke_width,
                        stroke_opacity=style.stroke_opacity, opacity=style.opacity),
        ))
    return out


@dataclass
class SegmentState:
    """一支片子里跨 beat 的分段状态。"""

    ghosts: dict[str, list[tuple[str, Group]]] = field(default_factory=dict)  # 对象 → [(画面上的名字, 残影节点)]
    pinned: set[str] = field(default_factory=set)
    protected: set[int] = field(default_factory=set)  # pin 之前就有的装饰（id），clear 不收
    count: int = 0

    def ghost(self, obj_id: str, source: Node, root: Node, theme: Theme, args: dict) -> tuple[str, Group, GhostIn]:
        self.count += 1
        name = f"{obj_id}~ghost{self.count}"
        holder = Group(name=name)
        holder.z = -2  # 画在所有对象后面
        holder.set_style(opacity=0.0)
        root.add(holder)
        self.ghosts.setdefault(obj_id, []).append((name, holder))
        return name, holder, GhostIn(holder, source, root, theme, args)

    def drop(self, obj_id: str, duration: float) -> tuple[list[str], list[Animation]]:
        """收掉 obj_id 的所有残影：返回 (画面上的名字, 淡出动画)。"""
        gone = self.ghosts.pop(obj_id, [])
        return [n for n, _ in gone], [Restyle(h, duration, opacity=0.0) for _, h in gone]


# ---------- 定格：时间轴映射 ----------

@dataclass
class Freeze:
    at: float  # 逻辑时刻（不含定格的时间轴，也就是旁白原音频的时刻）
    hold: float
    ref: str = ""  # 场景文件里写的时间点，写进 chapters.json 方便对照


def to_video(t: float, freezes: list[Freeze]) -> float:
    """逻辑时刻 → 成片时刻：在它之前（含同一时刻）的定格都要加上。"""
    return t + sum(f.hold for f in freezes if f.at <= t)


def freeze_starts(freezes: list[Freeze]) -> list[float]:
    """每个定格在成片里开始的时刻。"""
    out, extra = [], 0.0
    for f in sorted(freezes, key=lambda f: f.at):
        out.append(f.at + extra)
        extra += f.hold
    return out


def to_logical(v: float, freezes: list[Freeze]) -> float:
    """成片时刻 → 逻辑时刻；落在定格里的时刻都停在定格开始那一刻。"""
    extra = 0.0
    for f in sorted(freezes, key=lambda f: f.at):
        start = f.at + extra
        if v < start:
            break
        if v < start + f.hold:
            return f.at
        extra += f.hold
    return v - extra


class Warped:
    """包一层时间线：render_video 按成片时刻 seek，这里换成逻辑时刻再交给里面的时间线。"""

    def __init__(self, inner, freezes: list[Freeze]):
        self.inner, self.freezes = inner, [f for f in freezes if f.hold > 0]

    @property
    def end(self) -> float:
        return to_video(self.inner.end, self.freezes) if self.freezes else self.inner.end

    def seek(self, t: float) -> bool:
        return self.inner.seek(to_logical(t, self.freezes))

    def frames(self, fps: int = 30, until: float | None = None):
        total = self.end if until is None else max(until, self.end)
        n = int(round(total * fps)) + 1
        for k in range(n):
            yield k / fps


def insert_silence(src: Path, dst: Path, freezes: list[Freeze]) -> Path:
    """在旁白音频的各个定格时刻插入等长静音（逻辑时刻 = 原音频时刻）。"""
    with wave.open(str(src)) as r:
        params = r.getparams()
        frames = r.readframes(r.getnframes())
    width = params.sampwidth * params.nchannels
    out, cursor = [], 0
    for f in sorted(freezes, key=lambda f: f.at):
        cut = min(len(frames), int(round(f.at * params.framerate)) * width)
        out.append(frames[cursor:cut])
        out.append(b"\x00" * (int(round(f.hold * params.framerate)) * width))
        cursor = cut
    out.append(frames[cursor:])
    with wave.open(str(dst), "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(out))
    return dst
