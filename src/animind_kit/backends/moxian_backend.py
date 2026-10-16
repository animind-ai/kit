"""墨线后端：把 scene-spec 文档编译成一棵常驻的对象树 + 每个 beat 一条时间线。

和 Manim 后端的区别：对象树跨 beat 常驻，不需要每个 beat 重建画面；
每个 beat 渲染成一段带旁白的 mp4，最后拼接。
"""

from __future__ import annotations

import subprocess
import wave
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from moxian.anim import Animation, CameraMove, Draw, Restyle, Shift, TransformCopy, Wait, Write, fade_in, fade_out, together
from moxian.anim.camera import focus_on
from moxian.anim.camera import reset as camera_reset
from moxian.anim.timeline import Timeline
from moxian.core import BBox, Camera, Group, Node, PathNode, Style, parent_matrix_of
from moxian.encode import render_video
from moxian.path import Path as VPath
from moxian.path import line, rounded_rect
from moxian.render import SkiaRenderer
from scene_spec import selectors

from .. import analysis, timing
from ..mx import layout, widgets
from ..mx.text import mmath, rich
from ..regions import Region
from ..theme import Theme

DEFAULT_DUR = {
    "show": 0.6, "hide": 0.4, "transform": 1.0, "move": 0.8, "focus": 0.4, "unfocus": 0.4,
    "mark": 0.3, "unmark": 0.3, "underline": 0.4, "circle": 0.5, "cross": 0.4, "clear": 0.4, "set": 0.3,
    "camera": 1.5,
}
CAMERA_ZOOM = 2.0  # camera 只给 target 时的默认放大倍数
CAMERA_MARGIN = 0.5  # fit 时四周留白（画面单位）
CAMERA_EXEMPT_REGIONS = ("top", "footer")  # 这些区域的对象不跟镜头走
WIDGET_DUR = 0.5
LEVELS = {"display": 1.5, "title": 1.25, "body": 1.0, "caption": 0.75}  # scene-spec 的 level → 字号倍数
CUE_LATE = 0.4
SIZES = {"l": (854, 480, 15), "m": (1280, 720, 30), "h": (1920, 1080, 30)}


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
    def __init__(self, doc: dict, theme: Theme, audio_dir: Path | None, log=print, quality: str = "l"):
        self.doc, self.t, self.audio_dir, self.log = doc, theme, audio_dir, log
        self.width, self.height, self.fps = SIZES[quality]
        self.data = doc.get("data", {})
        self.lifetimes = analysis.lifetimes(doc)
        self.stack_scale: dict[str, float] = {}
        self.pinned_floor: float | None = None
        self.root = Group(name="root")
        self.camera = Camera()  # 跨 beat 常驻；没用 camera 动作时是原样，渲染逐像素不变
        self.objects: dict[str, Built] = {}
        for obj_id, definition in doc.get("objects", {}).items():
            built = self.build_object(obj_id, definition)
            built.node.set_style(opacity=0.0)
            self.root.add(built.node)
            self.objects[obj_id] = built
        self.visible: dict[str, Node] = {}
        self.focus_boxes: dict[str, Node] = {}
        self.dimmed: dict[str, list[Node]] = {}
        self.marks: dict[str, Node] = {}
        self.decorations: list[tuple[str, str, Node]] = []
        # 叠加层 → (挂在哪个节点下, 跟随的目标节点, 叠加层相对目标的世界矩阵)
        self.followers: dict[int, tuple[Node, Node, Node, np.ndarray]] = {}
        self.ignore_pairs: set[frozenset[str]] = set()
        self.problems: list[str] = []
        self.stats = {"beats": 0, "actions": 0, "cue_late": 0}
        pinned = [k for k, b in self.objects.items() if b.definition.get("pin") == "top"]
        for k in pinned:
            self.place(k)
        if pinned:
            self.pinned_floor = min(self.objects[k].node.bbox().y0 for k in pinned) - 0.3

    # ---------- 对象 ----------

    def build_object(self, obj_id: str, definition: dict) -> Built:
        t = self.t
        if "widget" in definition:
            cls = widgets.lookup(definition["widget"])
            if cls is None:
                raise NotImplementedError(f"kit（墨线）还没有组件 {definition['widget']!r}（对象 {obj_id}）")
            w = cls(obj_id, definition, t, self.data)
            return Built(w.node, definition, w, w.built.spans, w.built.parts, w.built.items)
        kind = definition["type"]
        role = definition.get("role")
        level = LEVELS[definition.get("level", "body")]
        if kind == "text":
            r = rich(definition["text"], t, size=36 * level, color=t.ink(role))
            return Built(r.node, definition, spans=r.spans)
        if kind == "math":
            return Built(mmath(r"\displaystyle " + definition["tex"], 40 * level, t.ink(role)), definition)
        raise NotImplementedError(f"kit（墨线）0.1 还不支持 type: {kind}（对象 {obj_id}）")

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
        这里保守地按全部累加，宁可排紧一点也不出框。只算和 obj_id 有共同可见 beat 的对象。
        """
        mine = self.lifetimes.get(obj_id) or set()
        total = 0.0
        for other, gap in self.dependents(obj_id):
            if mine and not (self.lifetimes.get(other, set()) & mine):
                continue
            node = self.objects[other].node
            layout.fit(node, self.region(other))
            total += gap + node.height
        return total

    def place(self, obj_id: str) -> Node:
        built = self.objects[obj_id]
        if built.placed:
            return built.node
        d = built.definition
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
        built.placed = True
        for key in ("over", "within"):
            if key in d:
                self.ignore_pairs.add(frozenset((obj_id, selectors.parse(d[key]).obj)))
        return built.node

    def resolve(self, text: str, place: bool = False) -> Node:
        sel = selectors.parse(text)
        built = self.objects[sel.obj]
        if place:
            self.place(sel.obj)
        if sel.span:
            return built.spans[sel.span]
        if sel.part:
            cells = built.parts[sel.part]
            return Group(*(cells[sel.index[0] : sel.index[1]] if sel.index else cells))
        if sel.index:
            return Group(*built.items[sel.index[0] : sel.index[1]])
        return built.node

    # ---------- beat ----------

    def render_beat(self, beat: dict, clip: Path) -> float:
        bt = timing.load(beat, self.audio_dir)
        tl_items: list[tuple[float, Animation]] = []
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
            else:
                start = prev_end
            next_at = self._next_at(actions, j, bt)
            anims: list[Animation] = []
            run_time = 0.0
            before = set(self.visible)
            for action in batch:
                self.stats["actions"] += 1
                a, d = self.animations(action)
                if action.get("dur") == "fit":
                    d = max(0.3, (next_at - start) if next_at is not None else d) - 0.1
                elif isinstance(action.get("dur"), (int, float)):
                    d = float(action["dur"])
                for anim in a:
                    anim.duration = d
                anims += a
                run_time = max(run_time, d)
            self._solve_layout(beat["id"], set(self.visible) - before)
            # 叠加层先 begin：量目标几何时，同一批的书写、变形还没改动路径
            anims.sort(key=lambda a: not isinstance(a, _Overlay))
            if anims:
                tl_items.append((start, together(*anims)))
            prev_end = start + run_time
            i = j

        tl = _FollowTimeline(follow=self._follow)
        for start, anim in tl_items:
            tl.add(start, anim)
        narration = self.audio_dir / f"{beat['id']}.wav" if self.audio_dir else None
        if narration is not None and not narration.exists():
            narration = None
        spoken = (wav_seconds(narration) + 0.28) if narration else (bt.total + 0.5 if beat.get("say") else 0.0)
        # 暂停（预测、想一想）接在旁白之后：画面定住 hold 秒，音轨补同样长的静音
        hold = float(beat["pause"].get("hold", 2)) if "pause" in beat else 0.0
        until = max(spoken + hold, tl.end + 0.3, 1.0)
        audio = self._padded_audio(narration, until, clip)
        self._camera_exempt()
        renderer = SkiaRenderer(self.width, self.height, background=self.t.BG, camera=self.camera)
        render_video(self.root, tl, clip, renderer, fps=self.fps, audio=audio, until=until, log=lambda *_: None)
        self.stats["beats"] += 1
        pinned = {k for k, v in self.objects.items() if v.definition.get("pin")}
        for problem in layout.check(self.visible, self.ignore_pairs, pinned):
            self.problems.append(f"{beat['id']}: {problem}")
            self.log(f"LAYOUT {beat['id']}: {problem}")
        return until

    def _solve_layout(self, beat_id: str, new: set[str]) -> None:
        """这一批动作新显示了对象时，让布局求解器把它们和已在画面上的对象分开。

        已经在画面上的对象固定不动（否则会从这一段开头就跳到新位置）；动画在播放时才读 matrix，
        所以在这里调整新对象是安全的。传对象本身的节点，不传 visible 里可能是临时容器的值。
        """
        if not new:
            return
        visible = [k for k in self.visible if k in self.objects]
        pinned = {k for k, v in self.objects.items() if v.definition.get("pin")}
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
            style = action.get("style", "fade")
            dur = 1.0 if style == "write" else DEFAULT_DUR["show"]
            if sel.span is None and sel.part is None and sel.index is None:
                self.visible[sel.obj] = node
                if style == "write":
                    return [Write(node, dur)], dur
                return [fade_in(node, dur, shift=(0.0, -0.1))], dur
            # 只显示对象里的某几项：第一次先把对象亮出来、其余项藏起来，之后只淡入指定的项
            built = self.objects[sel.obj]
            if sel.obj not in self.visible:
                built.node.set_style(opacity=1.0)
                for item in built.items:
                    item.set_style(opacity=0.0)
                self.visible[sel.obj] = built.node
            return [fade_in(n, dur, shift=(0.0, -0.1)) for n in self._members(node)], dur
        if act == "hide":
            sel = selectors.parse(action["target"])
            node = self.resolve(action["target"])
            if sel.span is not None or sel.part is not None or sel.index is not None:
                return [fade_out(n, DEFAULT_DUR["hide"]) for n in self._members(node)], DEFAULT_DUR["hide"]
            extras = self._drop_overlays(sel.obj)
            self.visible.pop(sel.obj, None)
            return ([fade_out(node, DEFAULT_DUR["hide"])] + [self._overlay_out(e, DEFAULT_DUR["hide"]) for e in extras],
                    DEFAULT_DUR["hide"])
        if act == "transform":
            src = self.resolve(action["from"])
            dst = self.resolve(action["to"], place=True)
            to_sel = selectors.parse(action["to"])
            self.visible[to_sel.obj] = dst
            d = DEFAULT_DUR["transform"]
            return [TransformCopy(self._members(src), dst, self.root, d)], d
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
        sel = selectors.parse(action["target"])
        built = self.objects[sel.obj]
        return built.widget.action(act, args), WIDGET_DUR

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
                deco.path = rounded_rect(box.width + 0.2, box.height + 0.2, 0.12)
                deco.move_to(box.center)
            else:
                deco.path = line(box.x0, box.y0, box.x1, box.y1).extend(line(box.x0, box.y1, box.x1, box.y0))
            self._attach(deco, self.root, node)

        d = DEFAULT_DUR[act]
        return _Overlay(Draw(deco, d) if act != "cross" else fade_in(deco, d), setup)

    def _drop_overlays(self, obj_id: str, keep_focus: bool = False) -> list[Node]:
        extras: list[Node] = []
        if not keep_focus:
            frame = self.focus_boxes.pop(obj_id, None)
            if frame is not None:
                extras.append(frame)
            for s in self.dimmed.pop(obj_id, []):
                s.set_style(opacity=1.0)
        for sel in [s for s in self.marks if selectors.parse(s).obj == obj_id]:
            extras.append(self.marks.pop(sel))
        keep = []
        for act, sel, deco in self.decorations:
            (extras.append(deco) if selectors.parse(sel).obj == obj_id else keep.append((act, sel, deco)))
        self.decorations = keep
        return extras

    # ---------- 整支 ----------

    def render_all(self, out: Path, build: Path) -> dict:
        parts = build / "parts"
        parts.mkdir(parents=True, exist_ok=True)
        clips = []
        for beat in self.doc["beats"]:
            self.log(f"render {beat['id']}")
            clip = parts / f"{beat['id']}.mp4"
            seconds = self.render_beat(beat, clip)
            clips.append(clip)
            self.log(f"muxed {beat['id']} {seconds:.1f}s")
        playlist = parts / "list.txt"
        playlist.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(playlist), "-c", "copy", str(out)], check=True)
        return {"out": str(out), "seconds": round(probe_seconds(out), 1), "beats": [b["id"] for b in self.doc["beats"]],
                "clip_seconds": [probe_seconds(c) for c in clips],
                "layout_problems": self.problems, "stats": self.stats}
