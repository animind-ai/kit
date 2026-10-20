"""墨线后端：把 scene-spec 文档编译成一棵常驻的对象树 + 每个 beat 一条时间线。

和 Manim 后端的区别：对象树跨 beat 常驻，不需要每个 beat 重建画面；
每个 beat 渲染成一段带旁白的 mp4，最后拼接。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import wave
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from moxian.anim import (
    Animation,
    CameraMove,
    Draw,
    Param,
    ParamTween,
    Restyle,
    Shift,
    TransformCopy,
    TransformMatching,
    Wait,
    Write,
    easing,
    fade_in,
    fade_out,
    together,
)
from moxian.anim.camera import focus_on
from moxian.anim.camera import reset as camera_reset
from moxian.anim.timeline import Timeline
from moxian.clip import ClipNode
from moxian.core import RIGHT, BBox, Camera, Group, Node, PathNode, Style, parent_matrix_of
from moxian.encode import render_video
from moxian.path import Path as VPath
from moxian.path import line, rounded_rect
from moxian.render import SkiaRenderer
from moxian.text import Tex, Text
from scene_spec import params as spec_params
from scene_spec import segments as spec_segments
from scene_spec import selectors
from scene_spec.textmarks import parse_object_text

from .. import analysis, beat_cache, lint, segments, subtitles, timing
from ..mx import layout, scan, widgets
from ..mx.readout import Readout
from ..mx.shape import build_shape
from ..mx.text import SCALE, mmath, rich
from ..regions import Region
from ..theme import Theme

DEFAULT_DUR = {
    "show": 0.6, "hide": 0.4, "transform": 1.0, "move": 0.8, "focus": 0.4, "unfocus": 0.4,
    "mark": 0.3, "unmark": 0.3, "underline": 0.4, "circle": 0.5, "cross": 0.4, "clear": 0.4, "set": 0.3,
    "camera": 1.5, "tween": spec_params.DEFAULT_TWEEN, "ghost": 0.5, "unghost": 0.4,
}
CAMERA_ZOOM = 2.0  # camera 只给 target 时的默认放大倍数
CAMERA_MARGIN = 0.5  # fit 时四周留白（画面单位）
CAMERA_EXEMPT_REGIONS = ("top", "footer")  # 这些区域的对象不跟镜头走
WIDGET_DUR = 0.5
LEVELS = {"display": 1.5, "title": 1.25, "body": 1.0, "caption": 0.75}  # scene-spec 的 level → 字号倍数
CUE_LATE = 0.4
SIZES = {"l": (854, 480, 15), "m": (1280, 720, 30), "h": (1920, 1080, 30)}
# 中途采样（标了 sample_alphas 的连续动画播到一半之类的时刻）的问题怎么算：
# - clip（字伸出画面）、overlap（字压字）：错误，和结束帧的问题一样计入版式问题；
# - cross（线扫过字）：只提醒，不计入版式问题（割线转向、平面旋转时线从字上扫过去是常事）；
# - small、covered：不查。字号由排版决定、中途不会变小（镜头推近只会放大）；填充盖住字和线扫过字一样
#   是一闪而过的，报成提醒只会更吵。真停在那里的话结束帧会照常报错。
MID_ERRORS = ("clip", "overlap")
MID_WARNINGS = ("cross",)


@dataclass
class Built:
    node: Node
    definition: dict
    widget: widgets.Widget | None = None
    spans: dict[str, Node] = field(default_factory=dict)
    parts: dict[str, list[Node]] = field(default_factory=dict)
    items: list[Node] = field(default_factory=list)
    placed: bool = False


class _Overlay(Animation):
    """叠加层（强调框、标记底色、圈、划线、叉）的入场。

    几何在 begin() 时才按目标当时的位置和大小算（编译时目标可能还没被布局求解器挪好、
    或者同一批里正在被 move / slide）；算好后登记成"跟随者"，之后目标再怎么动都跟着走。
    """

    def __init__(self, inner: Animation, setup: Callable[[], None]):
        super().__init__(inner.node, inner.duration, inner.ease)
        self.inner, self.setup = inner, setup

    def begin(self) -> None:
        super().begin()
        self.setup()
        self.inner.duration = self.duration
        self.inner.begin()

    def interpolate(self, alpha: float) -> None:
        self.inner.interpolate(alpha)

    def finish(self) -> None:
        self.inner.finish()


class _Detach(Animation):
    """叠加层的退场：淡出结束后不再跟随目标。"""

    def __init__(self, inner: Animation, done: Callable[[], None]):
        super().__init__(inner.node, inner.duration, inner.ease)
        self.inner, self.done = inner, done

    def begin(self) -> None:
        super().begin()
        self.inner.duration = self.duration
        self.inner.begin()

    def interpolate(self, alpha: float) -> None:
        self.inner.interpolate(alpha)

    def finish(self) -> None:
        self.inner.finish()
        self.done()


@dataclass
class _FollowTimeline(Timeline):
    """每推进一帧，先播动画，再让叠加层追上目标的新位置（同一帧里，不落后一帧）。"""

    follow: Callable[[], None] | None = None

    def seek(self, t: float) -> bool:
        changed = super().seek(t)
        if self.follow is not None:
            self.follow()
        return changed


@dataclass
class _Sample:
    time: float  # beat 内的成片时刻
    source: str  # 这一刻是哪个动作刚做完：do[i] 动作 at=书签；中途采样时是正在播的动作
    mid: float | None = None  # 中途采样：那个动画播到的比例（时长的比例）；None 是动作结束 / beat 末尾
    objs: frozenset[str] | None = None  # 中途采样：正在连续变化的对象；只报牵涉到它们的问题（None：不限）


@dataclass
class _Plan:
    inner: _FollowTimeline
    tl: segments.Warped
    narration: Path | None
    until: float
    snapshots: list[tuple[float, dict[str, Node]]]
    samples: list[_Sample]


class _Sampling:
    """包一层时间线：render_video 每 seek 到（越过）一个采样点，就在画这一帧之前做一次版式检查。"""

    def __init__(self, tl, samples: list[_Sample], on_sample: Callable[[_Sample], object]):
        self.tl, self.pending, self.on_sample = tl, list(samples), on_sample

    @property
    def end(self) -> float:
        return self.tl.end

    def frames(self, fps: int = 30, until: float | None = None):
        return self.tl.frames(fps, until)

    def seek(self, t: float) -> bool:
        changed = self.tl.seek(t)
        while self.pending and self.pending[0].time <= t + 1e-9:
            self.on_sample(self.pending.pop(0))
        return changed


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as handle:
        return handle.getnframes() / handle.getframerate()


def probe_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


class MoxianCompiler:
    def __init__(self, doc: dict, theme: Theme, audio_dir: Path | None, log=print, quality: str = "l",
                 spec_dir: Path | None = None):
        self.doc, self.t, self.audio_dir, self.log = doc, theme, audio_dir, log
        self.spec_dir = spec_dir or Path(".")  # clip 的 src 相对场景文件所在目录
        self.quality = quality
        self.width, self.height, self.fps = SIZES[quality]
        self.data = doc.get("data", {})
        # 场景参数跨 beat 常驻：每个 beat 的时间线从它们当时的值出发补间
        self.params = {k: Param(v, k) for k, v in spec_params.initial(doc).items()}
        self.lifetimes = analysis.lifetimes(doc)
        # 字号下限（数轴标注、侧栏、练一练框）：按成片算，烧录字幕时画面整体缩小，下限相应抬高
        self.floors = lint.floors(subtitles.picture_scale(subtitles.mode(doc)))
        self.stack_scale: dict[str, float] = {}
        self.pinned_floor: float | None = None
        self.root = Group(name="root")
        self.camera = Camera()  # 跨 beat 常驻；没用 camera 动作时是原样，渲染逐像素不变
        self.clip_offset: dict[str, float] = {}  # 片段的 start（show 之后再推迟多久开始播）
        self.clip_t0: dict[str, float] = {}  # 片段在整支片子里开始播的时刻（秒），第一次 show 时定
        self.timing_beats: dict[str, dict] = {}  # 时间报告：每个 beat 的句子时刻和带 at 的动作批次
        self.objects: dict[str, Built] = {}
        self.group_of = analysis.group_of(doc)  # group 的成员 → (group, 下标)；成员建在 group 里，不直接挂在根上
        for obj_id, definition in doc.get("objects", {}).items():
            if obj_id in self.group_of:  # group 的成员由 group 递归建
                continue
            built = self.build_object(obj_id, definition)
            built.node.set_style(opacity=0.0)
            self.root.add(built.node)
            self.objects[obj_id] = built
        self.visible: dict[str, Node] = {}
        self.opened: set[int] = set()  # 已经"打开"（亮出来、成员先藏起）的 group 成员节点，见 _open
        self.focus_boxes: dict[str, Node] = {}
        self.dimmed: dict[str, list[Node]] = {}
        self.marks: dict[str, Node] = {}
        self.decorations: list[tuple[str, str, Node]] = []
        # 叠加层 → (挂在哪个节点下, 跟随的目标节点, 叠加层相对目标的世界矩阵)
        self.followers: dict[int, tuple[Node, Node, Node, np.ndarray]] = {}
        self.ignore_pairs: set[frozenset[str]] = set()
        self.problems: list[str] = []
        self.lint_config = lint.Config(min_font=float(doc.get("style", {}).get("min_font", lint.Config.min_font)),
                                       floors=self.floors)
        self.allow = lint.Allow.from_doc(doc)
        self.tracker = lint.Tracker()
        self.mid_tracker = lint.Tracker("mid", by_slot=True)  # 中途采样发现的问题（结束帧没有的），另编号
        self.rest_seen: set[tuple] = set()  # (beat, Problem.ident())：结束帧上见过的问题，中途的不再重复报
        self.mid_pending: list[tuple[lint.Moment, list[lint.Problem]]] = []  # 当前 beat 的，beat 播完再定
        self.warnings: list[str] = []  # 只提醒、不计入版式问题的（中途的 cross）
        self.offset = 0.0  # 当前 beat 在整支片子里的起点（秒）
        self.stats = {"beats": 0, "actions": 0, "cue_late": 0}
        self.seg = segments.SegmentState()  # 残影、常驻（跨 beat）
        self.freezes: list[segments.Freeze] = []  # 当前 beat 的子步定格
        self.beat_info: dict[str, dict] = {}  # 每个 beat 的句子时刻、子步定格（成片时刻，相对段首），给字幕和章节用
        pinned = [k for k, b in self.objects.items() if b.definition.get("pin") == "top"]
        for k in pinned:
            self.place(k)
        if pinned:
            self.pinned_floor = min(self.objects[k].node.bbox().y0 for k in pinned) - 0.3

    # ---------- 对象 ----------

    def min_size(self, kind: str) -> float:
        """某一类文字的字号下限换成 kit 字号（留 4% 余量，排版时的小幅缩放不至于马上掉到下限以下）。"""
        return self.floors.get(kind, 0.0) / SCALE * 1.04

    def build_object(self, obj_id: str, definition: dict, opts: dict | None = None) -> Built:
        built = self._build_object(obj_id, definition, opts)
        # 侧栏里的文字：字号不低于侧栏下限（组件自己标了类别的，例如练一练框，按组件的）
        if analysis.region_static(self.doc, obj_id) == "side" and getattr(built.node, "font_floor", None) is None:
            built.node.font_floor = "side"
        return built

    def _build_object(self, obj_id: str, definition: dict, opts: dict | None = None) -> Built:
        t = self.t
        side = analysis.region_static(self.doc, obj_id) == "side"
        if "widget" in definition:
            cls = widgets.lookup(definition["widget"])
            if cls is None:
                raise NotImplementedError(f"kit（墨线）还没有组件 {definition['widget']!r}（对象 {obj_id}）")
            plan = getattr(cls, "plan_opts", None)  # 组件要先看一眼整篇文档才能排版的（solid 的 orbit 会经过哪些视角）
            opts = {**({"min_size": self.min_size("practice")} if definition["widget"] == "think_box" else {}),
                    **(plan(self.doc, obj_id) if plan else {}), **(opts or {})}
            w = cls(obj_id, definition, t, self.data, self.params, opts=opts)
            return Built(w.node, definition, w, w.built.spans, w.built.parts, w.built.items)
        kind = definition["type"]
        role = definition.get("role")
        level = LEVELS[definition.get("level", "body")]
        floor = self.min_size("side") if side else 0.0
        if kind == "text":
            r = rich(definition["text"], t, size=max(36 * level, floor), color=t.ink(role))
            return Built(r.node, definition, spans=r.spans)
        if kind == "math":
            return Built(mmath(r"\displaystyle " + definition["tex"], max(40 * level, floor), t.ink(role)), definition)
        if kind == "readout":
            return Built(Readout(definition, t, self.params, max(36 * level, floor)).node, definition)
        if kind == "clip":
            return Built(self._build_clip(obj_id, definition), definition)
        if kind == "shape":
            return Built(build_shape(definition, t, max(32 * level, floor), name=obj_id), definition)
        if kind == "group":
            return self._build_group(obj_id, definition)
        raise NotImplementedError(f"kit（墨线）0.1 还不支持 type: {kind}（对象 {obj_id}）")

    def _build_group(self, obj_id: str, d: dict) -> Built:
        """type: group：成员按顺序递归建好（成员自己也可以是 group），在组内排开，整组当一个对象排版。

        成员写了 below / above / left_of / right_of 且指向组内已经排好的成员时按它摆，否则接在上一个成员右边。
        成员登记在 self.objects 里（可以单独 focus、mark 它），但不挂在根上；obj[i] 是第 i 个成员。
        """
        items: list[Node] = []
        placed: dict[str, Node] = {}
        for child in d["children"]:
            if child in self.objects:
                raise ValueError(f"{obj_id}：成员 {child} 重复出现、已经在别的 group 里，或 group 嵌套成环（scene-spec check 报 SS344）")
            built = self.build_object(child, self.doc["objects"][child])
            self.objects[child] = built
            node = built.node
            cd = built.definition
            ref = next(((key, selectors.parse(cd[key]).obj) for key in layout.DIRECTIONS
                        if key in cd and selectors.parse(cd[key]).obj in placed), None)
            if ref is not None:
                key, other = ref
                node.next_to(placed[other].edge(layout.DIRECTIONS[key]), layout.DIRECTIONS[key], layout.GAPS[cd.get("gap", "md")])
                if key in ("below", "above"):
                    node.shift([placed[other].center()[0] - node.center()[0], 0.0])
                else:
                    node.shift([0.0, placed[other].center()[1] - node.center()[1]])
            elif items:
                node.next_to(items[-1], RIGHT, layout.GAPS["md"])
            placed[child] = node
            items.append(node)
            built.placed = True
        return Built(Group(*items, name=obj_id), d, items=items)

    def _is_clip(self, obj_id: str) -> bool:
        return self.doc.get("objects", {}).get(obj_id, {}).get("type") == "clip"

    def _build_clip(self, obj_id: str, d: dict) -> ClipNode:
        """type: clip：外部画面（视频或帧序列）当底板，z 默认 -1 垫在矢量对象下面。

        src 相对场景文件所在目录。muted 只能是 true（素材音轨不参与合成）。start 不在这里定：
        见 _sync_clips，它在片段第一次 show 时换算成整支片子里的时刻。
        """
        if d.get("muted", True) is not True:
            raise ValueError(f"{obj_id}：clip 的 muted 只能为 true（素材音轨不参与合成）")
        path = (self.spec_dir / d["src"]).resolve()
        if not path.exists():
            raise FileNotFoundError(f"{obj_id}：找不到素材 {d['src']}（相对 {self.spec_dir}）")
        trim = d.get("trim") or {}
        self.clip_offset[obj_id] = float(d.get("start", 0.0))
        return ClipNode(
            str(path), kind=d.get("kind"), fps=d.get("fps"),
            t_in=float(trim.get("in", 0.0)), t_out=None if trim.get("out") is None else float(trim["out"]),
            rate=float(d.get("rate", 1.0)), fit=d.get("fit", "contain"), opacity=float(d.get("opacity", 1.0)),
            end=d.get("end", "hold"), z=float(d.get("z", -1.0)), name=obj_id,
        )

    def _place_clip(self, obj_id: str, built: Built) -> Node:
        """底板：在区域（in）里铺满；size 的 max_width / max_height 是区域的比例（缺省 1）。之后和别的对象一样摆（below 等）。"""
        d = built.definition
        region = self.region(obj_id)
        size = d.get("size") or {}
        w = region.width * float(size.get("max_width", 1.0))
        h = region.height * float(size.get("max_height", 1.0))
        built.node.plate = BBox(-w / 2, -h / 2, w / 2, h / 2)
        refs = {key: self.wbox(self.resolve(d[key], place=True)) for key in analysis.LAYOUT_REFS if key in d}
        layout.place(built.node, {**d, "size": {"max_width": 1.0, "max_height": 1.0}}, refs, region)
        built.placed = True
        return built.node

    def _raise_labels(self, obj_id: str) -> bool:
        """排版后组件的标注字号低于下限（组件被缩小了）：按实际缩放加大 label_size 重建组件，返回是否重建了。

        只在组件第一次排版、还没做过动作时重建；标注变大以后组件更高、缩得更多，所以要再排一次，几轮内收敛。
        """
        built = self.objects[obj_id]
        w = built.widget
        kind = getattr(w, "label_floor", None)
        if w is None or kind is None or getattr(w, "acted", False):
            return False
        target = self.floors.get(kind, 0.0)
        em = w.label_size * SCALE * self.world_scale(built.node)
        if not target or em >= target - 0.05:
            return False
        if not any(n is built.node for n in self.root.children):
            return False  # 在 group 里：整组一起排版，不单独重建
        fresh = self._build_object(obj_id, built.definition, {"label_size": w.label_size * target / em * 1.01})
        fresh.node.font_floor = getattr(built.node, "font_floor", None)
        fresh.node.set_style(opacity=0.0)
        index = next(i for i, n in enumerate(self.root.children) if n is built.node)
        self.root.children[index] = fresh.node
        self.objects[obj_id] = fresh
        return True

    # ---------- 坐标 ----------

    def wbox(self, node: Node) -> BBox:
        """世界坐标包围盒；对不在树里的引用容器（片段 Group）取子节点并集。"""
        try:
            box = node.world_bbox(self.root)
        except ValueError:
            box = None
            for child in node.children:
                cb = self.wbox(child)
                box = cb if box is None else box.union(cb)
        if box is None:
            raise ValueError(f"节点 {node.name!r} 没有几何")
        return box

    def world_scale(self, node: Node) -> float:
        """节点在世界坐标里相对自身的缩放倍数（引用容器取第一个成员）。"""
        members = self._members(node)
        if not members:
            return 1.0
        m = parent_matrix_of(self.root, members[0]) @ members[0].matrix
        return float(np.sqrt(abs(np.linalg.det(m[:2, :2]))))

    def _in_tree(self, target: Node) -> bool:
        try:
            parent_matrix_of(self.root, target)
            return True
        except ValueError:
            return False

    def _members(self, node: Node) -> list[Node]:
        """选择器解析出的节点：在树里就是它自己；是临时打包的引用容器就展开成树里的成员。"""
        return [node] if self._in_tree(node) else [m for c in node.children for m in self._members(c)]

    def parent_of(self, target: Node) -> Node:
        def search(node: Node) -> Node | None:
            for child in node.children:
                if child is target:
                    return node
                found = search(child)
                if found is not None:
                    return found
            return None

        found = search(self.root)
        if found is None:  # 引用容器：用它第一个子节点的父亲
            return self.parent_of(target.children[0])
        return found

    def world_matrix(self, node: Node) -> np.ndarray:
        return parent_matrix_of(self.root, node) @ node.matrix

    def _attach(self, overlay: Node, host: Node, target: Node) -> None:
        """让 overlay（挂在 host 下）从现在起跟着 target 走：记下它相对 target 的世界矩阵。

        target 是临时打包的引用容器（例如 hole[1:3]）时跟第一个成员走。
        """
        members = self._members(target)
        if not members:
            return
        anchor = members[0]
        rel = np.linalg.inv(self.world_matrix(anchor)) @ self.world_matrix(host) @ overlay.matrix
        self.followers[id(overlay)] = (overlay, host, anchor, rel)

    def _detach(self, overlay: Node) -> None:
        self.followers.pop(id(overlay), None)

    def _follow(self) -> None:
        for overlay, host, anchor, rel in list(self.followers.values()):
            try:
                overlay.matrix = np.linalg.inv(self.world_matrix(host)) @ self.world_matrix(anchor) @ rel
            except ValueError:  # 目标不在树里了
                self._detach(overlay)

    def _overlay_out(self, overlay: Node, duration: float) -> Animation:
        return _Detach(fade_out(overlay, duration), lambda: self._detach(overlay))

    def to_local(self, node: Node, parent: Node) -> Node:
        """node 现在的矩阵是世界坐标；改成 parent 的局部坐标。"""
        world_of_parent = parent_matrix_of(self.root, parent) @ parent.matrix
        node.matrix = np.linalg.inv(world_of_parent) @ node.matrix
        return node

    def region_of(self, obj_id: str) -> str:
        return analysis.region_static(self.doc, obj_id) if self.objects[obj_id].definition.get("pin") != "top" else "top"

    def region(self, obj_id: str) -> Region:
        """对象所在区域。顶上有钉住的对象时，主讲区和侧栏的上沿取它的实际下沿，而不是固定比例。"""
        usage = analysis.usage_for(self.doc, self.lifetimes, obj_id)
        r = layout.regions(*usage)[self.region_of(obj_id)]
        if usage[0] and r.name in ("stage", "side") and self.pinned_floor is not None:
            r = replace(r, y1=self.pinned_floor)
        return r

    def dependents(self, obj_id: str, depth: int = 0) -> list[tuple[str, float]]:
        """挂在 obj_id 下面的对象（含叠在它上面的对象下面挂的），返回 (id, 间距)。"""
        out: list[tuple[str, float]] = []
        if depth > 10:
            return out
        for other, built in self.objects.items():
            d = built.definition
            if "below" in d and selectors.parse(d["below"]).obj == obj_id:
                out.append((other, layout.GAPS[d.get("gap", "md")]))
                out += self.dependents(other, depth + 1)
        for other, built in self.objects.items():
            if "over" in built.definition and selectors.parse(built.definition["over"]).obj == obj_id:
                extra = self.dependents(other, depth + 1)
                mine = {k for k, _ in out}
                out += [(k, g) for k, g in extra if k not in mine]
        return out

    def stack_below(self, obj_id: str) -> float:
        """挂在下面的对象的总高度（含间距），按各自区域先做宽度适配。

        同一时刻只有一部分会出现（例如 tr_name 和 tr_d0 都挂在 tr_zero 下面，但不同时可见）；
        按 beat 分别累加同一 beat 里可见的那些，取最大的一个 beat（同一 beat 里先后出现的仍然全算，宁可排紧一点也不出框）。
        只算和 obj_id 有共同可见 beat 的对象；不知道可见期的对象每个 beat 都算。
        """
        mine = self.lifetimes.get(obj_id) or set()
        heights: list[tuple[set[int] | None, float]] = []
        for other, gap in self.dependents(obj_id):
            life = self.lifetimes.get(other, set())
            if mine and not (life & mine):
                continue
            node = self.objects[other].node
            layout.fit(node, self.region(other))
            heights.append(((life & mine) if (mine and life) else None, gap + node.height))
        if not heights:
            return 0.0
        beats = set().union(*(life for life, _ in heights if life)) or {None}
        return max(sum(h for life, h in heights if life is None or beat in life) for beat in beats)

    def place(self, obj_id: str, attempt: int = 0) -> Node:
        built = self.objects[obj_id]
        if obj_id in self.group_of:  # group 的成员：随整组排版
            self.place(analysis.top_of(self.doc, obj_id))
            return built.node
        if built.placed:
            return built.node
        d = built.definition
        if d.get("type") == "clip":
            return self._place_clip(obj_id, built)
        refs: dict[str, BBox] = {}
        for key in analysis.LAYOUT_REFS:
            if key in d:
                refs[key] = self.wbox(self.resolve(d[key], place=True))
        for axis, sel in (d.get("align") or {}).items():
            refs[f"align.{axis}"] = self.wbox(self.resolve(sel, place=True))
        region = self.region(obj_id)
        if "over" in d:
            # 盖在别的对象上（例如题卡里的填空）：跟着被盖住的对象的实际缩放走，字号才一致
            built.node.scale(self.world_scale(self.resolve(d["over"])))
        factor = self.stack_scale.get(obj_id, 1.0)
        if factor < 1.0:
            built.node.scale(factor)
        below = 0.0
        relative = any(k in d for k in ("below", "above", "left_of", "right_of", "over", "within", "pin", "at"))
        if not relative:
            below = self.stack_below(obj_id)
            if below > 0:
                layout.fit(built.node, region)
                total = built.node.height + below
                if total > region.height * 0.98:
                    # 整叠放不下：所有成员按同一比例缩，不是只缩最上面一个
                    f = region.height * 0.98 / total
                    built.node.scale(f)
                    below *= f
                    for dep, _ in self.dependents(obj_id):
                        self.stack_scale[dep] = min(self.stack_scale.get(dep, 1.0), f)
        layout.place(built.node, d, refs, region, below)
        if attempt < 6 and self._raise_labels(obj_id):
            return self.place(obj_id, attempt + 1)
        built.placed = True
        for key in ("over", "within"):
            if key in d:
                self.ignore_pairs.add(frozenset((obj_id, selectors.parse(d[key]).obj)))
        return built.node

    def resolve(self, text: str, place: bool = False) -> Node:
        sel = selectors.parse(text)
        if place:
            self.place(sel.obj)
        built = self.objects[sel.obj]  # 排版时组件可能被重建（数轴标注抬到字号下限），排完再取
        if sel.span:
            return built.spans[sel.span]
        if sel.part:
            cells = built.parts[sel.part]
            return Group(*(cells[sel.index[0] : sel.index[1]] if sel.index else cells))
        if sel.index:
            return Group(*built.items[sel.index[0] : sel.index[1]])
        return built.node

    # ---------- beat ----------

    def _plan(self, beat: dict, clip: Path | None = None) -> _Plan:
        """把一个 beat 编成时间线，算出时长和版式采样点（每批动作结束时、beat 末尾）。

        clip 给了（真渲染）就在定格处给旁白插静音、写在 clip 旁边；不给（inspect）只估算时长。
        """
        bt = timing.load(beat, self.audio_dir, timing.lang_of(self.doc))
        tl_items: list[tuple[float, Animation]] = []
        snapshots: list[tuple[float, dict[str, Node]]] = [(-1.0, dict(self.visible))]  # 每批动作之后谁在画面上
        ends: list[tuple[float, str]] = []  # (逻辑时刻, 来源)
        marked: dict[int, tuple[frozenset[str] | None, str]] = {}  # 标了中途采样的动画 → (它在变的对象, 来源)
        cue_actions: list[dict] = []  # 时间报告：每个带 at 的动作批次，画面实际开始 vs 书签
        clip_shows: list[tuple[str, float]] = []  # 片段被 show 的逻辑时刻，定好时间线之后换成视频时刻
        prev_end = 0.0
        actions = beat.get("do", [])
        i = 0
        while i < len(actions):
            batch = [actions[i]]
            j = i + 1
            while j < len(actions) and "with" in actions[j]:
                batch.append(actions[j])
                j += 1
            head = batch[0]
            if "at" in head:
                want = bt.resolve(head["at"])
                start = max(want, prev_end)
                if start - want > CUE_LATE and want > 0.5:
                    self.stats["cue_late"] += 1
                    self.log(f"CUE LATE {beat['id']}[{head['at']}] 画面比旁白晚 {start - want:.1f}s")
                cue_actions.append({"at": head["at"], "want": round(want, 3), "start": round(start, 3)})
            else:
                start = prev_end
            next_at = self._next_at(actions, j, bt)
            anims: list[Animation] = []
            run_time = 0.0
            before = set(self.visible)
            cue = f" at={head['at']}" if "at" in head else ""
            source = f"do[{i}] {head['act']}{cue}"
            for n, action in enumerate(batch):
                self.stats["actions"] += 1
                if action["act"] == "show" and self._is_clip(selectors.parse(action["target"]).obj):
                    clip_shows.append((selectors.parse(action["target"]).obj, start))
                if action["act"] == "step":
                    ref = str(action.get("at", "after" if "after" in action else "with"))
                    self.freezes.append(segments.Freeze(start, spec_segments.step_hold(self.doc, action), ref))
                a, d = self.animations(action)
                if action.get("dur") == "fit":
                    d = max(0.3, (next_at - start) if next_at is not None else d) - 0.1
                elif isinstance(action.get("dur"), (int, float)):
                    d = float(action["dur"])
                for anim in a:
                    anim.duration = d
                    for sub in _marked(anim):  # 中途采样的来源写这个动作自己（同一批 with 进来的也分得清）
                        marked[id(sub)] = (self._moving_objects(action), f"do[{i + n}] {action['act']}{cue}")
                anims += a
                run_time = max(run_time, d)
            self._solve_layout(beat["id"], set(self.visible) - before)
            snapshots.append((start, dict(self.visible)))
            # 叠加层先 begin：量目标几何时，同一批的书写、变形还没改动路径
            # 残影也先 begin：快照要拍在同一批的 erase、变形改动画面之前
            anims.sort(key=lambda a: not isinstance(a, (_Overlay, segments.GhostIn)))
            if anims:
                tl_items.append((start, together(*anims)))
            ends.append((start + run_time, source))
            prev_end = start + run_time
            i = j

        inner = _FollowTimeline(follow=self._follow)
        for start, anim in tl_items:
            inner.add(start, anim)
        freezes, self.freezes = self.freezes, []
        tl = segments.Warped(inner, freezes)
        self._sync_clips(clip_shows, tl.freezes)
        narration = self.audio_dir / f"{beat['id']}.wav" if self.audio_dir else None
        if narration is not None and not narration.exists():
            narration = None
        frozen = sum(f.hold for f in tl.freezes)
        if narration is not None and tl.freezes and clip is not None:  # 定格处给旁白插入等长静音
            narration = segments.insert_silence(narration, clip.with_suffix(".narration.wav"), tl.freezes)
            spoken = wav_seconds(narration) + 0.28
        elif narration is not None:
            spoken = wav_seconds(narration) + frozen + 0.28
        else:
            spoken = bt.total + frozen + 0.5 if beat.get("say") else 0.0
        self.beat_info[beat["id"]] = {
            "cues": [{"start": segments.to_video(s.start, tl.freezes), "end": segments.to_video(s.end, tl.freezes),
                      "marks": {k: segments.to_video(v, tl.freezes) for k, v in s.marks.items()},
                      "words": s.words and {"n": s.words["n"],
                                            "at": [[i, segments.to_video(w, tl.freezes)] for i, w in s.words["at"]]}}
                     for s in bt.sentences],
            "steps": [{"time": round(t, 3), "hold": f.hold, "at": f.ref}
                      for f, t in zip(sorted(freezes, key=lambda f: f.at), segments.freeze_starts(freezes))],
        }
        # 时间报告用旁白的音频时钟（不乘 freezes），和 CUE LATE 的算法一致；cues 是句子的原始时刻，scene-spec 据此查句间静音
        self.timing_beats[beat["id"]] = {
            "cues": [{"start": round(s.start, 3), "end": round(s.end, 3)} for s in bt.sentences],
            "actions": cue_actions,
        }
        # 暂停（预测、想一想）接在旁白之后：画面定住 hold 秒，音轨补同样长的静音
        hold = float(beat["pause"].get("hold", 2)) if "pause" in beat else 0.0
        until = max(spoken + hold, tl.end + 0.3, 1.0)
        # 采样落在渲染真正会画的那一帧（不早于动作结束的第一帧），inspect 和 render 看到的是同一个画面
        last = round(until * self.fps) / self.fps

        def frame(t: float) -> float:
            return min(np.ceil(segments.to_video(t, tl.freezes) * self.fps - 1e-6) / self.fps, last)

        samples = [_Sample(frame(t), src) for t, src in ends]
        samples.append(_Sample(last, "结束"))
        samples += self._mid_samples(inner, marked, frame, {s.time for s in samples})
        return _Plan(inner, tl, narration, until, snapshots, sorted(samples, key=lambda s: s.time))

    def _sync_clips(self, clip_shows: list[tuple[str, float]], freezes) -> None:
        """片段的 start 换成这个 beat 的时钟（renderer.time 每个 beat 都从 0 起）。

        片段第一次 show 时记下它在整支片子里的开始时刻：beat 起点 + show 的视频时刻 + 场景里的 start（show 之后再推迟多久）。
        之后每个 beat 都减去自己的起点，所以片段跨 beat 接着播，不会每个 beat 从头播。
        show 之后隐藏、再显示，不重新开始（片段一直在播，隐藏时只是不画）。start 为负表示这个 beat 开始时它已经在播。
        """
        for obj, logical in clip_shows:
            if obj not in self.clip_t0:
                self.clip_t0[obj] = self.offset + segments.to_video(logical, freezes) + self.clip_offset[obj]
        for obj, t0 in self.clip_t0.items():
            self.objects[obj].node.start = t0 - self.offset

    def _mid_samples(self, inner: Timeline, marked: dict[int, tuple[frozenset[str] | None, str]],
                     frame: Callable[[float], float], rest: set[float]) -> list[_Sample]:
        """标了 sample_alphas 的动画的中途采样点，落到渲染会画的帧上。和结束帧同一帧的不要（结束帧照常查）；
        几个落在同一帧的合成一个：来源全部列出，正在变的对象取并集。没有对象在变（补间的参数没人用）就不采样。"""
        groups: dict[float, list[tuple[float, str, frozenset[str] | None]]] = {}
        for ms in inner.samples():
            time = frame(ms.time)
            if time in rest:
                continue
            for alpha, anim in ms.sources:
                moving, src = marked.get(id(anim), (None, "?"))
                groups.setdefault(time, []).append((alpha, src, moving))
        out = []
        for time, entries in groups.items():
            moving = [m for _a, _s, m in entries]
            objs = None if None in moving else frozenset().union(*moving)
            if objs is not None and not objs:
                continue
            source = " + ".join(dict.fromkeys(f"α={a:g} {s}" for a, s, _m in entries))
            out.append(_Sample(time, f"mid {source}", entries[0][0], objs))
        return out

    def _moving_objects(self, action: dict) -> frozenset[str] | None:
        """一个动作里连续变化的是哪些对象：tween 是依赖这些参数的对象（读数、跟着参数走的图），其余是 target。"""
        if action["act"] == "tween":
            deps = {id(d if isinstance(d, Node) else getattr(d, "node", d))
                    for k in action.get("args", {}) for d in self.params[k]._dependents}
            return frozenset(k for k, b in self.objects.items() if any(id(n) in deps for n in _subtree(b.node)))
        if "target" in action:
            return frozenset({selectors.parse(action["target"]).obj})
        return None

    def render_beat(self, beat: dict, clip: Path, plan: _Plan | None = None) -> float:
        """光栅化 + 编码一个 beat。plan 给了就用它（render_all 先编好时间线、算缓存键），否则在这里编。"""
        plan = self._plan(beat, clip) if plan is None else plan
        audio = self._padded_audio(plan.narration, plan.until, clip)
        self._camera_exempt()
        renderer = SkiaRenderer(self.width, self.height, background=self.t.BG, camera=self.camera)
        sampled = _Sampling(plan.tl, plan.samples, lambda s: self.check_at(beat["id"], s))
        render_video(self.root, sampled, clip, renderer, fps=self.fps, audio=audio, until=plan.until, log=lambda *_: None)
        self._settle_mid(beat["id"])
        self.stats["beats"] += 1
        self._end_checks(beat["id"], plan)
        return plan.until

    def _replay(self, beat: dict, plan: _Plan, check: bool = True) -> None:
        """不画、不编码地播完一个 beat：和 render_video 一样逐帧 seek（版式检查也在同样的采样点做），
        之后的 beat 就从和真渲染完全相同的状态出发，版式问题也和真渲染一样。
        check=False（--only 之外的 beat）不做版式检查，和 inspect 只查选中的 beat 一样。"""
        self._camera_exempt()
        timeline = _Sampling(plan.tl, plan.samples, lambda s: self.check_at(beat["id"], s)) if check else plan.tl
        for t in plan.tl.frames(self.fps, plan.until):
            timeline.seek(t)
        if check:
            self._settle_mid(beat["id"])
            self.stats["beats"] += 1
            self._end_checks(beat["id"], plan)

    def _end_checks(self, beat_id: str, plan: _Plan) -> None:
        pinned = {k for k, v in self.objects.items() if v.definition.get("pin")} | self.seg.pinned
        found = layout.check(self.visible, self.ignore_pairs, pinned)
        if plan.inner.schedules:  # 参数采样按逻辑时刻，用定格之前的时间线
            found += self._param_layout(plan.inner, plan.snapshots, pinned, {p.split("（")[0] for p in found})
        for problem in found:
            self.problems.append(f"{beat_id}: {problem}")
            self.log(f"LAYOUT {beat_id}: {problem}")
        # 组件内部的结束状态检查（例如 dissect 的标签互相压住）；移动途中的重叠不算
        for k in [k for k in self.visible if k in self.objects]:
            for problem in getattr(self.objects[k].widget, "end_problems", list)():
                self.problems.append(f"{beat_id}: {problem}")
                self.log(f"LAYOUT {beat_id}: {problem}")

    # ---------- 字形级版式检查（inspect） ----------

    def labels(self) -> dict[int, scan.Label]:
        """对象树里的节点 → 报告里的名字。叠加层挂在根上或目标的父节点下，各自单独标出。"""
        out: dict[int, scan.Label] = {}
        for k, b in self.objects.items():
            out[id(b.node)] = scan.Label(k, k)
            for name, node in b.spans.items():
                out[id(node)] = scan.Label(f"{k}#{name}", k)
        for k, frame in self.focus_boxes.items():
            out[id(frame)] = scan.Label(f"focus({k})", k, "focus")
        for sel, pad in self.marks.items():
            out[id(pad)] = scan.Label(f"mark({sel})", selectors.parse(sel).obj, "mark")
        for act, sel, deco in self.decorations:
            out[id(deco)] = scan.Label(f"{act}({sel})", selectors.parse(sel).obj, act)
        for obj, ghosts in self.seg.ghosts.items():
            for name, holder in ghosts:
                out[id(holder)] = scan.Label(name, obj, "ghost")
        return out

    def check_at(self, beat_id: str, sample: _Sample) -> None:
        if sample.mid is None:
            self.lint_at(beat_id, sample)
        else:
            self.mid_lint_at(beat_id, sample)

    def _lint_now(self, labels: dict[int, scan.Label], time: float) -> list[lint.Problem]:
        things = scan.scan(self.root, labels, self.camera, self.lint_config.min_opacity, time)
        return lint.check(things, self.lint_config, self.allow, self.ignore_pairs, framed=not self.camera.is_identity())

    def lint_at(self, beat_id: str, sample: _Sample) -> list[tuple[int, lint.Problem]]:
        problems = self._lint_now(self.labels(), sample.time)
        moment = lint.Moment(beat_id, sample.time, sample.source, self.offset + sample.time)
        self.rest_seen |= {(beat_id, p.ident()) for p in problems}
        return self.tracker.see(moment, problems)

    def mid_lint_at(self, beat_id: str, sample: _Sample) -> None:
        """中途采样：只看 MID_ERRORS / MID_WARNINGS 里的几类、只看牵涉到正在变的对象的问题，先记下，beat 播完再定。"""
        labels = self.labels()
        problems = [p for p in self._lint_now(labels, sample.time) if p.kind in MID_ERRORS + MID_WARNINGS]
        if sample.objs is not None:
            obj_of = {lab.owner: lab.obj for lab in labels.values()}
            problems = [p for p in problems if obj_of.get(p.subject) in sample.objs
                        or (p.other is not None and obj_of.get(p.other) in sample.objs)]
        if problems:
            self.mid_pending.append((lint.Moment(beat_id, sample.time, sample.source, self.offset + sample.time), problems))

    def _settle_mid(self, beat_id: str) -> None:
        """beat 播完：结束帧（动作结束、beat 末尾）上也有的问题已经照常报了，中途的不再重复；
        同一个问题在这个 beat 里只记第一次出现的时刻。都按 Problem.ident() 认（文字段的位置，不是文字内容：
        读数在变，同一段字每一刻的文字都不一样；同一个组件里不同的标注位置不同，不会被并成一条）。"""
        seen: set[tuple] = set()
        for moment, problems in self.mid_pending:
            for p in problems:
                ident = p.ident()
                if (beat_id, ident) in self.rest_seen or ident in seen:
                    continue
                seen.add(ident)
                self.mid_tracker.see(moment, [p])
        self.mid_pending = []

    def _report_lint(self) -> None:
        """整支片子检查完再报：同一个问题跨 beat 合并成一条，带完整的时间范围。中途的 cross 只提醒。"""
        for f in self.tracker.findings:
            self.problems.append(f.line())
            self.log(f"LINT {f.line()}")
        for f in self.mid_tracker.findings:
            if f.problem.kind in MID_ERRORS:
                self.problems.append(f.line())
                self.log(f"LINT {f.line()}")
            else:
                self.warnings.append(f.line())
                self.log(f"WARN {f.line()}")

    def inspect(self, only: set[str] | None = None, sheets: Path | None = None) -> list[lint.Finding]:
        """不编码视频：逐 beat 编时间线，跳到每个采样点做字形级检查；给了 sheets 就写分镜图。

        only 之外的 beat 也要编、要播到末尾（后面的 beat 从它们的结束状态出发），只是不检查、不出图。
        返回结束帧的问题加上中途的错误（clip / overlap，编号 [midN]）；中途的 cross 只是提醒，在 self.warnings 里。
        """
        from .. import storyboard

        for beat in self.doc["beats"]:
            plan = self._plan(beat)
            self._camera_exempt()
            chosen = only is None or beat["id"] in only
            cells: list[storyboard.Cell] = []
            renderer = SkiaRenderer(storyboard.CELL_W, storyboard.CELL_H, background=self.t.BG, camera=self.camera)
            for sample in plan.samples if chosen else []:
                plan.tl.seek(sample.time)
                if sample.mid is not None:  # 中途采样不出分镜格
                    self.mid_lint_at(beat["id"], sample)
                    continue
                marks = self.lint_at(beat["id"], sample)
                renderer.time = sample.time  # 和 render_beat 同一个时钟：片段取这一刻的帧
                if sheets is not None:
                    caption = f"#{len(cells)} {beat['id']} t={sample.time:.2f}s  {sample.source}"
                    cells.append(storyboard.Cell(renderer.render(self.root), caption, marks))
            plan.tl.seek(plan.until)
            if chosen:
                self._settle_mid(beat["id"])
                self._end_checks(beat["id"], plan)
            if cells:
                storyboard.write_sheets(cells, sheets, beat["id"])
            self.offset += round(plan.until * self.fps) / self.fps + 1 / self.fps
            self.stats["beats"] += 1
        self._report_lint()
        return self.tracker.findings + [f for f in self.mid_tracker.findings if f.problem.kind in MID_ERRORS]

    def _param_layout(self, tl: Timeline, snapshots: list[tuple[float, dict[str, Node]]], pinned: set[str],
                      seen: set[str]) -> list[str]:
        """参数在变的 beat：包围盒跟着参数变，结束帧没问题不代表中间没问题。

        在每个补间的起点、中点、终点，把参数设成那一刻的值，检查那时在画面上的对象（按动作批次记下的快照），
        再把参数恢复成结束时的值。位置用结束时的（同一段里别的对象一般不会同时移动）。同一对冲突只报一次。
        """
        final = {p: p.value for p in tl.params()}
        out: list[str] = []
        for t in tl.keyframes():
            visible = next(snap for start, snap in reversed(snapshots) if start <= t + 1e-9)
            for p in final:
                p.set(tl.param_at(p, t))
            values = "，".join(f"{p.name}={p.value:.3g}" for p in final)
            for problem in layout.check(visible, self.ignore_pairs, pinned):
                key = problem.split("（")[0]
                if key not in seen:
                    seen.add(key)
                    out.append(f"{problem}（t={t:.2f}s，{values}）")
        for p, v in final.items():
            p.set(v)
        return out

    def _solve_layout(self, beat_id: str, new: set[str]) -> None:
        """这一批动作新显示了对象时，让布局求解器把它们和已在画面上的对象分开。

        已经在画面上的对象固定不动（否则会从这一段开头就跳到新位置）；动画在播放时才读 matrix，
        所以在这里调整新对象是安全的。传对象本身的节点，不传 visible 里可能是临时容器的值。
        """
        if not new:
            return
        visible = [k for k in self.visible if k in self.objects]
        pinned = {k for k, v in self.objects.items() if v.definition.get("pin")} | self.seg.pinned
        problems = layout.resolve(
            {k: self.objects[k].node for k in visible},
            {k: self.objects[k].definition for k in visible},
            {k: self.region(k) for k in visible},
            self.ignore_pairs,
            fixed=(set(visible) - new) | pinned,
        )
        for p in problems:
            self.problems.append(f"{beat_id}: {p}")
            self.log(f"LAYOUT {beat_id}: {p}")

    @staticmethod
    def _padded_audio(narration: Path | None, seconds: float, clip: Path) -> Path:
        """每段都给一条和画面等长的音轨：有旁白就在后面补静音，没有就整段静音。

        拼接各段时用的是 -c copy，所有片段必须有同样的音轨结构；没有音轨的片段会让后面音画错位。
        """
        out = clip.with_suffix(".audio.wav")
        cmd = ["ffmpeg", "-y", "-v", "error"]
        if narration is not None:
            cmd += ["-i", str(narration), "-af", f"apad=whole_dur={seconds:.3f}"]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono"]
        cmd += ["-t", f"{seconds:.3f}", "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(out)]
        subprocess.run(cmd, check=True)
        return out

    def _next_at(self, actions: list, start: int, bt) -> float | None:
        for a in actions[start:]:
            if "at" in a:
                return bt.resolve(a["at"])
        return None

    # ---------- 动作 → 动画 ----------

    def animations(self, action: dict) -> tuple[list[Animation], float]:
        act = action["act"]
        args = action.get("args", {})
        if act == "hold":
            return [Wait(float(action.get("dur", 1.0)))], float(action.get("dur", 1.0))
        if act == "show":
            sel = selectors.parse(action["target"])
            node = self.resolve(action["target"], place=True)
            if self._is_clip(sel.obj):  # 片段是底板：淡入即可，不进 visible（版式求解不看它；lint 在扫描时看到它）
                return [fade_in(node, DEFAULT_DUR["show"])], DEFAULT_DUR["show"]
            style = action.get("style", "fade")
            dur = 1.0 if style == "write" else DEFAULT_DUR["show"]
            if sel.span is None and sel.part is None and sel.index is None:
                self._reveal(sel.obj)
                if style == "write":
                    return [Write(node, dur)], dur
                return [fade_in(node, dur, shift=(0.0, -0.1))], dur
            # 只显示对象里的某几项：第一次先把对象亮出来、其余项藏起来，之后只淡入指定的项
            self._open(sel.obj)
            return [fade_in(n, dur, shift=(0.0, -0.1)) for n in self._members(node)], dur
        if act == "hide":
            sel = selectors.parse(action["target"])
            node = self.resolve(action["target"])
            if sel.span is not None or sel.part is not None or sel.index is not None:
                return [fade_out(n, DEFAULT_DUR["hide"]) for n in self._members(node)], DEFAULT_DUR["hide"]
            if self._is_clip(sel.obj):
                return [fade_out(node, DEFAULT_DUR["hide"])], DEFAULT_DUR["hide"]
            return self._vanish(sel.obj, DEFAULT_DUR["hide"]), DEFAULT_DUR["hide"]
        if act == "transform":
            return self._transform(action), DEFAULT_DUR["transform"]
        if act == "focus":
            return self._focus(action["target"]), DEFAULT_DUR["focus"]
        if act == "unfocus":
            return self._unfocus(selectors.parse(action["target"]).obj), DEFAULT_DUR["unfocus"]
        if act == "mark":
            return [self._make_mark(action["target"])], DEFAULT_DUR["mark"]
        if act == "unmark":
            pad = self.marks.pop(action["target"], None)
            return ([self._overlay_out(pad, DEFAULT_DUR["unmark"])] if pad else []), DEFAULT_DUR["unmark"]
        if act == "clear":
            extras = self._drop_overlays(selectors.parse(action["target"]).obj, keep_focus=True)
            return [self._overlay_out(e, DEFAULT_DUR["clear"]) for e in extras], DEFAULT_DUR["clear"]
        if act in ("underline", "circle", "cross"):
            return [self._decoration(act, action["target"])], DEFAULT_DUR[act]
        if act == "set":
            sel = selectors.parse(action["target"])
            widget = self.objects[sel.obj].widget
            custom = widget.restyle(sel, args) if widget else None
            if custom is not None:
                return custom, DEFAULT_DUR["set"]
            node = self.resolve(action["target"])
            changes = {}
            if "role" in args:
                changes["fill"] = self.t.ink(args["role"])
            if "opacity" in args:
                changes["opacity"] = float(args["opacity"])
            return [Restyle(node, DEFAULT_DUR["set"], **changes)], DEFAULT_DUR["set"]
        if act == "move":
            sel = selectors.parse(action["target"])
            built = self.objects[sel.obj]
            old = built.node.matrix.copy()
            old_c = built.node.center()
            built.placed = False
            built.definition = {**built.definition, **args}
            self.place(sel.obj)
            delta = built.node.center() - old_c
            built.node.matrix = old
            return [Shift(built.node, delta, DEFAULT_DUR["move"])], DEFAULT_DUR["move"]
        if act == "camera":
            return self._camera(action, args), DEFAULT_DUR["camera"]
        if act == "tween":
            d = DEFAULT_DUR["tween"]
            ease = easing.by_name(action.get("ease", "smooth"))
            return [ParamTween(self.params[k], float(v), d, ease) for k, v in args.items()], d
        if act in ("ghost", "unghost", "step", "pin"):
            return self._segment(act, action, args)
        sel = selectors.parse(action["target"])
        built = self.objects[sel.obj]
        return built.widget.action(act, args), WIDGET_DUR

    def _reveal(self, obj_id: str) -> None:
        """整个对象要出现了：登记进 visible。group 的成员不单独登记，而是打开它所在的组（等于 show group[i]）。"""
        if obj_id in self.group_of:
            self._open(self.group_of[obj_id][0])
        else:
            self.visible[obj_id] = self.objects[obj_id].node

    def _open(self, obj_id: str) -> None:
        """要显示对象里的某几项：第一次先把对象亮出来、其余项藏起来（之后再逐项淡入）。

        对象本身是某个 group 的成员时，先把外层的组也这样打开（只亮出这一路上的成员）。
        """
        built = self.objects[obj_id]
        if obj_id in self.group_of:
            self._open(self.group_of[obj_id][0])
            if id(built.node) in self.opened:
                return
            self.opened.add(id(built.node))
        elif obj_id in self.visible:
            return
        else:
            self.visible[obj_id] = built.node
        built.node.set_style(opacity=1.0)
        for item in built.items:
            item.set_style(opacity=0.0)

    def _vanish(self, obj_id: str, dur: float, fade: bool = True) -> list[Animation]:
        """整个对象退场（hide，或被 transform replace 替换掉）：连同强调框、标记、装饰、残影一起收掉，不再算在画面上。"""
        node = self.objects[obj_id].node
        extras = self._drop_overlays(obj_id)
        if obj_id not in self.group_of:
            self.visible.pop(obj_id, None)
            self.opened -= {id(n) for n in _subtree(node)}  # 再出现时组内成员重新按 show 的写法打开
        return (([fade_out(node, dur)] if fade else []) + [self._overlay_out(e, dur) for e in extras]
                + self._unghost(obj_id))

    def _transform(self, action: dict) -> list[Animation]:
        """from 变成 to。

        - 默认（不写 match、match: shape）：TransformCopy，目标的每个字形从源里对应的字形变过来，源保持不动。
        - match: tex / auto 且两边是有同名片段的整个对象：TransformMatching，同名片段平移 + 缩放过去，新片段淡入；
          配不上时退回上面的整体变形。match 只管配对，源默认保持不动（配上的片段像复制一样飞过去）。
        - 只有显式写了 replace: true：源在变形期间淡出、之后不在画面上（等于同时 hide 它）。
        都不做中途采样：部件互相穿过是变形本来的样子。
        """
        d = DEFAULT_DUR["transform"]
        src = self.resolve(action["from"])
        dst = self.resolve(action["to"], place=True)
        from_sel, to_sel = selectors.parse(action["from"]), selectors.parse(action["to"])
        # 记整个对象的节点：变到某个片段上时，dst 可能是不在树里的临时容器，拿它算包围盒会误报重叠
        self._reveal(to_sel.obj)
        replace = selectors.transform_replaces(action) and src is not dst
        gone = selectors.transform_replaced(action)
        pairs = self._match_pairs(action, from_sel, to_sel)
        if pairs is not None:
            anims: list[Animation] = [TransformMatching(src, dst, pairs, self.root, d, keep_source=not replace)]
        else:
            anims = [TransformCopy(self._members(src), dst, self.root, d)]
            if replace:
                anims += [fade_out(n, d) for n in self._members(src)]
        if gone is not None:
            anims += self._vanish(gone, d, fade=False)  # 淡出上面已经有了；这里收掉叠加层、登记
        return anims

    def _match_pairs(self, action: dict, from_sel, to_sel) -> list[tuple[list[Node], list[Node], bool]] | None:
        """match: tex / auto 的片段配对：(源片段的节点, 目标片段的节点, 内容是否不同)。配不了时 None（退回整体变形）。

        只配整个对象对整个对象；按片段 id 配，同名片段的文字不同时交叉淡入淡出。
        """
        if action.get("match") not in ("tex", "auto"):
            return None
        whole = all(s.span is None and s.part is None and s.index is None for s in (from_sel, to_sel))
        a, b = self.objects[from_sel.obj], self.objects[to_sel.obj]
        common = [k for k in b.spans if k in a.spans] if whole and from_sel.obj != to_sel.obj else []
        if not common:
            if action.get("match") == "tex":
                self.log(f"提示：transform {action['from']} → {action['to']} 没有可配对的同名片段，按整体变形")
            return None
        texts_a, texts_b = _span_texts(a.definition), _span_texts(b.definition)
        return [(self._members(a.spans[k]), self._members(b.spans[k]),
                 k not in texts_a or texts_a.get(k) != texts_b.get(k)) for k in common]

    # ---------- 分段：残影、子步、常驻 ----------

    def _segment(self, act: str, action: dict, args: dict) -> tuple[list[Animation], float]:
        if act == "step":  # 定格在 render_beat 里登记，这里不产生动画
            return [], 0.0
        obj = selectors.parse(action["target"]).obj
        if act == "ghost":
            name, holder, anim = self.seg.ghost(obj, self.objects[obj].node, self.root, self.t, args)
            self.visible[name] = holder
            if args.get("place", "stay") == "stay":
                self.ignore_pairs.add(frozenset((obj, name)))
            return [anim], DEFAULT_DUR["ghost"]
        if act == "unghost":
            return self._unghost(obj), DEFAULT_DUR["unghost"]
        # pin：之后的布局求解把它当固定占位；它此刻已有的划线、圈、标记跟着常驻
        self.seg.pinned.add(obj)
        self.seg.protected |= {id(d) for _a, s, d in self.decorations if selectors.parse(s).obj == obj}
        self.seg.protected |= {id(p) for s, p in self.marks.items() if selectors.parse(s).obj == obj}
        return [], 0.0

    def _unghost(self, obj_id: str) -> list[Animation]:
        names, anims = self.seg.drop(obj_id, DEFAULT_DUR["unghost"])
        for name in names:
            self.visible.pop(name, None)
        return anims

    # ---------- 镜头 ----------

    def _camera(self, action: dict, args: dict) -> list[Animation]:
        """{zoom: 倍数} 放大到 target 中心；{fit: true, margin?} 让 target 填满画面；{reset: true} 复位。

        zoom 是相对原样的绝对倍数，不累乘；没有 target 时绕当前中心缩放。
        """
        d = DEFAULT_DUR["camera"]
        if args.get("reset"):
            return [camera_reset(self.camera, d)]
        if "target" not in action:
            return [CameraMove(self.camera, zoom=float(args.get("zoom", CAMERA_ZOOM)), duration=d)]
        box = self.wbox(self.resolve(action["target"]))
        if args.get("fit"):
            return [focus_on(self.camera, box, margin=float(args.get("margin", CAMERA_MARGIN)), duration=d)]
        return [focus_on(self.camera, box, zoom=float(args.get("zoom", CAMERA_ZOOM)), duration=d)]

    def _camera_exempt(self) -> None:
        """钉住的对象、top / footer 区域的对象，连同挂在根上的强调框和装饰，都不跟镜头走。"""
        exempt = {k for k, b in self.objects.items()
                  if b.definition.get("pin") or self.region_of(k) in CAMERA_EXEMPT_REGIONS}
        for k in exempt:
            self.objects[k].node.fixed = True
            if k in self.focus_boxes:
                self.focus_boxes[k].fixed = True
        for _act, sel, deco in self.decorations:
            if selectors.parse(sel).obj in exempt:
                deco.fixed = True
        if any(a.get("act") == "camera" for beat in self.doc["beats"] for a in beat.get("do") or []):
            for k in exempt:
                self._backdrop(self.objects[k].node)

    def _backdrop(self, node: Node) -> None:
        """不跟镜头走的对象垫一块底色：镜头推近时放大的图从它下面经过，线不会穿过字。

        底色就是背景色，没有东西从下面经过时看不出来。每个对象只垫一次。
        """
        done = self.__dict__.setdefault("_backdrops", set())
        if id(node) in done or isinstance(node, (Text, Tex)):
            return  # 整个对象就是一段 Text / Tex：垫进去会被当成字形，不垫
        boxes = [b for c in node.children if (b := c.bbox(np.eye(3))) is not None]
        if not boxes:
            return
        done.add(id(node))
        box = boxes[0]
        for b in boxes[1:]:
            box = box.union(b)
        pad = PathNode(path=rounded_rect(box.width + 0.24, box.height + 0.16, 0.08), style=Style(fill=self.t.BG),
                       z=-10, name="backdrop")
        pad.move_to(box.center)
        node.add(pad)

    # ---------- 强调、标记、装饰 ----------

    def _focus(self, target: str) -> list[Animation]:
        sel = selectors.parse(target)
        built = self.objects[sel.obj]
        if built.widget and sel.index is not None:
            custom = built.widget.focus(sel.index[0])
            if custom is not None:
                return custom
        node = self.resolve(target)
        anims = list(self._unfocus(sel.obj, fade=True))
        frame = PathNode(style=Style(stroke=self.t.HL_INK, stroke_width=2.5, opacity=0.0))
        self.root.add(frame)

        def setup() -> None:
            box = self.wbox(node)
            frame.path = rounded_rect(box.width + 0.16, box.height + 0.16, 0.08)
            frame.matrix = np.eye(3)
            frame.move_to(box.center)
            self._attach(frame, self.root, node)

        self.focus_boxes[sel.obj] = frame
        selected = set(id(c) for c in node.children) | {id(node)}
        siblings = [m for m in built.items if id(m) not in selected]
        self.dimmed[sel.obj] = siblings
        anims.append(_Overlay(fade_in(frame, DEFAULT_DUR["focus"]), setup))
        anims += [Restyle(s, DEFAULT_DUR["focus"], opacity=0.45) for s in siblings]
        return anims

    def _unfocus(self, obj_id: str, fade: bool = True) -> list[Animation]:
        built = self.objects[obj_id]
        anims: list[Animation] = []
        if built.widget:
            custom = built.widget.focus(None)
            if custom is not None and obj_id not in self.focus_boxes:
                anims += custom
        frame = self.focus_boxes.pop(obj_id, None)
        if frame is not None:
            anims.append(self._overlay_out(frame, DEFAULT_DUR["unfocus"]))
        for s in self.dimmed.pop(obj_id, []):
            anims.append(Restyle(s, DEFAULT_DUR["unfocus"], opacity=1.0))
        return anims

    def _make_mark(self, target: str) -> Animation:
        """标记底色挂在目标的父节点下、画在目标后面（z=-1），大小和位置到入场时才算。"""
        node = self.resolve(target)
        pad = PathNode(style=Style(fill=self.t.MARK, opacity=0.0), z=-1)
        parent = self.parent_of(node)
        parent.add(pad)

        def setup() -> None:
            box = self.wbox(node)
            pad.path = rounded_rect(box.width + 0.2, box.height + 0.16, 0.08)
            pad.matrix = np.eye(3)
            pad.move_to(box.center)
            self.to_local(pad, parent)
            self._attach(pad, parent, node)

        self.marks[target] = pad
        return _Overlay(fade_in(pad, DEFAULT_DUR["mark"]), setup)

    def _decoration(self, act: str, target: str) -> Animation:
        node = self.resolve(target)
        if act == "cross":
            style = Style(stroke=self.t.NO, stroke_width=4, opacity=0.0)
        else:
            style = Style(stroke=self.t.HL_INK, stroke_width=4 if act == "underline" else 3, opacity=0.0)
        deco = PathNode(path=VPath(), style=style)
        self.root.add(deco)
        self.decorations.append((act, target, deco))

        def setup() -> None:
            box = self.wbox(node)
            deco.matrix = np.eye(3)
            if act == "underline":
                y = box.y0 - 0.06
                deco.path = line(box.x0, y, box.x1, y)
            elif act == "circle":
                left, right = self._circle_room(node, box)
                deco.path = rounded_rect(box.width + left + right, box.height + 0.2, 0.12)
                deco.move_to(box.center + np.array([(right - left) / 2, 0.0]))
            else:
                deco.path = line(box.x0, box.y0, box.x1, box.y1).extend(line(box.x0, box.y1, box.x1, box.y0))
            self._attach(deco, self.root, node)

        d = DEFAULT_DUR[act]
        return _Overlay(Draw(deco, d) if act != "cross" else fade_in(deco, d), setup)

    def _circle_room(self, node: Node, box: BBox, pad: float = 0.1) -> tuple[float, float]:
        """圈的左右留白：默认 pad；同一行紧挨着别的字（例如「A₈ =」后面的填空横线）时只占一半空隙，圈不压到它。"""
        left = right = pad
        nodes = set(map(id, node.children)) | {id(node)}
        for sib in self.parent_of(node).children:
            if id(sib) in nodes:
                continue
            try:
                b = self.wbox(sib)
            except ValueError:
                continue
            if b.y1 < box.y0 or b.y0 > box.y1:
                continue  # 不在同一行
            if b.x0 >= box.x1 - 1e-6:
                right = min(right, (b.x0 - box.x1) / 2)
            elif b.x1 <= box.x0 + 1e-6:
                left = min(left, (box.x0 - b.x1) / 2)
        return max(left, 0.02), max(right, 0.02)

    def _drop_overlays(self, obj_id: str, keep_focus: bool = False) -> list[Node]:
        extras: list[Node] = []
        if not keep_focus:
            frame = self.focus_boxes.pop(obj_id, None)
            if frame is not None:
                extras.append(frame)
            for s in self.dimmed.pop(obj_id, []):
                s.set_style(opacity=1.0)
        protected = self.seg.protected if keep_focus else set()  # clear 不收常驻对象 pin 之前就有的装饰
        for sel in [s for s in self.marks if selectors.parse(s).obj == obj_id and id(self.marks[s]) not in protected]:
            extras.append(self.marks.pop(sel))
        keep = []
        for act, sel, deco in self.decorations:
            mine = selectors.parse(sel).obj == obj_id and id(deco) not in protected
            (extras.append(deco) if mine else keep.append((act, sel, deco)))
        self.decorations = keep
        return extras

    # ---------- 整支 ----------

    def _cache_base(self) -> str:
        """缓存键里所有 beat 共用的部分（见 beat_cache 的说明）。"""
        h = beat_cache.Hasher().add("env", beat_cache.environment())
        h.json("settings", {"quality": self.quality, "size": [self.width, self.height, self.fps], "theme": self.t,
                            "lang": timing.lang_of(self.doc), "audio": self.audio_dir is not None})
        h.json("doc", {k: v for k, v in self.doc.items() if k != "beats"})
        h.json("lifetimes", self.lifetimes)
        h.json("camera", any(a.get("act") == "camera" for b in self.doc["beats"] for a in b.get("do") or []))
        h.json("sources", beat_cache.sources(self.doc, self.spec_dir))
        return h.hexdigest()

    def _audio_digest(self, beat_id: str) -> str:
        if self.audio_dir is None:
            return "none"
        return beat_cache.canonical({ext: beat_cache.file_digest(self.audio_dir / f"{beat_id}{ext}")
                                     for ext in (".wav", ".timing.json")})

    def _beat_key(self, base: str, before: str, beat: dict, plan: _Plan) -> str:
        """编好时间线（排完版）、开播之前算：base + 之前各 beat 的链 + 这个 beat 的定义、旁白、解析出的时间 + 此刻的画面状态。"""
        bid = beat["id"]
        h = beat_cache.Hasher().add("base", base).add("before", before)
        h.json("beat", beat).add("audio", self._audio_digest(bid))
        h.json("timing", {"info": self.beat_info[bid], "report": self.timing_beats[bid], "until": plan.until,
                          "samples": plan.samples, "freezes": plan.tl.freezes})
        h.add("narration", beat_cache.file_digest(plan.narration) if plan.narration is not None else "none")
        h.add("state", beat_cache.scene_state(self.root, self.camera, self.params))
        return h.hexdigest()

    def render_all(self, out: Path, build: Path, only: set[str] | None = None, cache: bool = True) -> dict:
        """逐 beat：编时间线 → 算缓存键 → parts/<beat>.mp4 的 .hash 对得上就只推进状态（_replay），否则 render_beat。

        cache=False（--no-cache）：一律重渲（照样写新的 .hash），不读旧的 .hash；--only 之外的 beat 时长也按帧数估。
        only（--only）：只渲染这些 beat，各自复制成 out 旁边的 <beat>.mp4，不拼成片、不动 out；
        其余 beat 照样编时间线、推进状态（不检查版式、没有缓存也不渲染）。
        """
        parts = build / "parts"
        parts.mkdir(parents=True, exist_ok=True)
        base = self._cache_base()
        chain = beat_cache.Hasher()  # 之前各 beat 的定义和旁白：编译器里有不在对象树上的跨 beat 状态
        has_clips = any(self._is_clip(k) for k in self.objects)
        clips, rendered, cached = [], [], []
        probed: dict[Path, float] = {}  # 每段只 probe 一次
        for beat in self.doc["beats"]:
            bid = beat["id"]
            clip = parts / f"{bid}.mp4"
            chosen = only is None or bid in only
            plan = self._plan(beat, clip)
            self._camera_exempt()  # 垫底色、钉住会改画面；render_beat / _replay 里再调一次不会重复
            key = self._beat_key(base, chain.hexdigest(), beat, plan)
            chain.json("beat", beat).add("audio", self._audio_digest(bid))
            valid = cache and beat_cache.hit(clip, key)  # cache=False（--no-cache、regress）根本不读旧旁注
            if chosen and not valid:
                self.log(f"render {bid}")
                beat_cache.invalidate(clip)
                seconds = self.render_beat(beat, clip, plan)
                if not beat_cache.record(clip, key):
                    self.log(f"提醒：记不下 {bid} 播放中用到的公式和字体，这一段不缓存")
                rendered.append(bid)
                self.log(f"muxed {bid} {seconds:.1f}s")
            else:
                self._replay(beat, plan, check=chosen)
                if chosen:
                    cached.append(bid)
                    self.log(f"cached {bid} {plan.until:.1f}s（{clip.name} 内容哈希未变，跳过渲染）")
            if chosen or valid:  # 真渲染了，或者缓存里就是这一段：时长取实际的
                probed[clip] = probe_seconds(clip)
                self.offset += probed[clip]
            else:  # --only 之外、也没有有效缓存的 beat：成片时长按帧数估（和 inspect 一样）
                self.offset += round(plan.until * self.fps) / self.fps + 1 / self.fps
                if has_clips:
                    self.log(f"提醒：{bid} 没有有效缓存，它的时长按帧数估算；片段（clip）的时钟可能和全量渲染差一帧")
            if chosen:
                clips.append(clip)
        self._report_lint()
        self.log(f"beat 缓存：{len(cached)} 段命中，{len(rendered)} 段重渲" + ("" if cache else "（--no-cache）"))
        info = {"cache": {"cached": cached, "rendered": rendered},
                "clip_seconds": [probed[c] for c in clips], "beat_info": self.beat_info,
                "timing_beats": self.timing_beats,
                "layout_problems": self.problems, "layout_warnings": self.warnings, "stats": self.stats}
        if only is not None:
            outputs = []
            out.parent.mkdir(parents=True, exist_ok=True)
            for clip in clips:
                target = out.parent / clip.name
                tmp = target.with_name(f".{target.name}.tmp")
                shutil.copyfile(clip, tmp)
                os.replace(tmp, target)
                outputs.append(str(target))
                self.log(f"wrote {target}")
            return {"out": None, "outputs": outputs, "beats": [c.stem for c in clips], **info}
        playlist = parts / "list.txt"
        playlist.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(playlist), "-c", "copy", str(out)], check=True)
        return {"out": str(out), "seconds": round(probe_seconds(out), 1), "beats": [b["id"] for b in self.doc["beats"]], **info}


def _marked(anim: Animation):
    """anim 和它的子动画（together 里的）里标了中途采样的那些。"""
    if anim.sample_alphas:
        yield anim
    for _offset, sub in anim.children():
        yield from _marked(sub)


def _span_texts(definition: dict) -> dict[str, str]:
    """text 对象里每个带 id 的片段的原文（判断同名片段内容是否相同）；别的对象没有。"""
    if definition.get("type") != "text" or "text" not in definition:
        return {}
    return {sp.id: sp.text for sp in parse_object_text(definition["text"]).spans if sp.id}


def _subtree(node: Node):
    yield node
    for child in node.children:
        yield from _subtree(child)
