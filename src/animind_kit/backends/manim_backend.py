"""Manim 后端：把 scene-spec 文档编译成一组 Scene（每个 beat 一个）。

对象在第一次 show 时按布局字段摆放；跨 beat 的画面状态（哪些对象可见、组件内部状态、
焦点与标记）由 Compiler 记着，每个 beat 的 Scene 先静态恢复上一 beat 结束时的画面，再播动作。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from manim import (
    DOWN,
    Animation,
    Cross,
    FadeIn,
    FadeOut,
    Line,
    Mobject,
    RoundedRectangle,
    Scene,
    SurroundingRectangle,
    Transform,
    TransformFromCopy,
    TransformMatchingTex,
    VGroup,
    Write,
)
from scene_spec import selectors

from .. import analysis, layout, timing, widgets
from ..text import math, rich
from ..theme import Theme

DEFAULT_DUR = {
    "show": 0.6, "hide": 0.4, "transform": 1.0, "move": 0.8, "focus": 0.4, "unfocus": 0.4,
    "mark": 0.3, "unmark": 0.3, "underline": 0.4, "circle": 0.5, "cross": 0.4, "set": 0.3,
}
WIDGET_DUR = 0.5
CUE_LATE = 0.4


@dataclass
class Built:
    mob: Mobject
    definition: dict
    widget: widgets.Widget | None = None
    spans: dict[str, Mobject] = field(default_factory=dict)
    parts: dict[str, list[Mobject]] = field(default_factory=dict)
    items: list[Mobject] = field(default_factory=list)
    placed: bool = False


@dataclass
class State:
    """一个 beat 结束时的画面。"""

    visible: list[str] = field(default_factory=list)
    widget_states: dict[str, dict] = field(default_factory=dict)
    focus: dict[str, str] = field(default_factory=dict)  # 对象 id → 被聚焦的选择器
    marks: list[str] = field(default_factory=list)  # 选择器
    decorations: list[tuple[str, str]] = field(default_factory=list)  # (动作, 选择器)


class Compiler:
    def __init__(self, doc: dict, theme: Theme, audio_dir: Path | None, log=print):
        self.doc = doc
        self.theme = theme
        self.audio_dir = audio_dir
        self.log = log
        self.data = doc.get("data", {})
        self.state = State()
        self.problems: list[str] = []
        self.lifetimes = analysis.lifetimes(doc)
        self.timing_beats: dict[str, dict] = {}  # 时间报告（和墨线后端同一格式）
        for obj_id, d in doc.get("objects", {}).items():
            if d.get("type") == "clip":  # 片段只在墨线后端做：这里早早报清楚，不等到渲染深处才崩
                raise NotImplementedError(f"type: clip（对象 {obj_id}）只支持墨线后端，请用 --backend moxian（manim 后端不支持片段）")

    def usage_for(self, obj_id: str) -> tuple[bool, bool, bool]:
        return analysis.usage_for(self.doc, self.lifetimes, obj_id)

    # ---------- 对象 ----------

    def build_all(self) -> dict[str, Built]:
        built: dict[str, Built] = {}
        for obj_id, definition in self.doc.get("objects", {}).items():
            built[obj_id] = self.build_object(obj_id, definition)
        return built

    def build_object(self, obj_id: str, definition: dict) -> Built:
        t = self.theme
        if "widget" in definition:
            cls = widgets.lookup(definition["widget"])
            if cls is None:
                raise NotImplementedError(f"kit 还没有组件 {definition['widget']!r}（对象 {obj_id}）")
            w = cls(obj_id, definition, t, self.data)
            return Built(w.mob, definition, w, w.built.spans, w.built.parts, w.built.items)
        kind = definition["type"]
        role = definition.get("role")
        if kind == "text":
            r = rich(definition["text"], t, size=36, color=t.ink(role))
            return Built(r.mob, definition, spans=r.spans)
        if kind == "math":
            return Built(math(definition["tex"], 40, t.ink(role)), definition)
        raise NotImplementedError(f"kit 0.1 还不支持 type: {kind}（对象 {obj_id}）")

    # ---------- 每个 beat 一个 Scene ----------

    def scene_class(self, beat: dict) -> type[Scene]:
        compiler = self
        beat_timing = timing.load(beat, self.audio_dir, timing.lang_of(self.doc))

        class BeatScene(Scene):
            def construct(self) -> None:
                runner = BeatRunner(self, compiler, beat, beat_timing)
                runner.run()

        BeatScene.__name__ = beat["id"]
        BeatScene.__qualname__ = beat["id"]
        return BeatScene


class BeatRunner:
    """在一个 Scene 里恢复状态、按时间播动作、记录新状态。"""

    def __init__(self, scene: Scene, compiler: Compiler, beat: dict, beat_timing: timing.BeatTiming):
        self.scene = scene
        self.c = compiler
        self.t = compiler.theme
        self.beat = beat
        self.timing = beat_timing
        self.c.timing_beats[beat["id"]] = {
            "cues": [{"start": round(s.start, 3), "end": round(s.end, 3)} for s in beat_timing.sentences],
            "actions": [],
        }
        self.objects = compiler.build_all()
        self.visible: dict[str, Mobject] = {}
        self.focus_boxes: dict[str, VGroup] = {}
        self.dimmed: dict[str, list[Mobject]] = {}
        self.marks: dict[str, Mobject] = {}
        self.decorations: list[tuple[str, str, Mobject]] = []
        self.ignore_pairs: set[frozenset[str]] = set()

    # ---- 状态 ----

    def restore(self) -> None:
        s = self.c.state
        for obj_id in s.visible:
            built = self.objects[obj_id]
            if built.widget and obj_id in s.widget_states:
                built.widget.restore(s.widget_states[obj_id])
            self.place(obj_id)
            self.scene.add(built.mob)
            self.visible[obj_id] = built.mob
        for sel in s.marks:
            self.scene.add(self._make_mark(sel))
        for sel in s.focus.values():
            self._focus_anims(sel, instant=True)  # instant=True 直接改画面，不返回动画
        for act, sel in s.decorations:
            deco = self._decoration(act, sel)
            self.decorations.append((act, sel, deco))
            self.scene.add(deco)

    def snapshot(self) -> State:
        return State(
            visible=list(self.visible),
            widget_states={k: v.widget.state() for k, v in self.objects.items() if v.widget and k in self.visible},
            focus={k: v for k, v in self.current_focus.items()},
            marks=list(self.marks),
            decorations=[(act, sel) for act, sel, _ in self.decorations],
        )

    # ---- 布局 ----

    def place(self, obj_id: str) -> Mobject:
        built = self.objects[obj_id]
        if built.placed:
            return built.mob
        d = built.definition
        refs: dict[str, Mobject] = {}
        for key in ("below", "above", "left_of", "right_of", "within", "over"):
            if key in d:
                refs[key] = self.resolve(d[key], place=True)
        for axis, sel in (d.get("align") or {}).items():
            refs[f"align.{axis}"] = self.resolve(sel, place=True)
        layout.place(built.mob, d, refs, self.region_of(obj_id), self.stack_below(obj_id), self.c.usage_for(obj_id))
        built.placed = True
        if "over" in d:
            self.ignore_pairs.add(frozenset((obj_id, selectors.parse(d["over"]).obj)))
        if "within" in d:
            self.ignore_pairs.add(frozenset((obj_id, selectors.parse(d["within"]).obj)))
        return built.mob

    def region_of(self, obj_id: str, depth: int = 0) -> str:
        """对象所在区域：自己写了 in 就用它，否则沿锚点往上找。"""
        d = self.objects[obj_id].definition
        if "in" in d:
            return d["in"]
        for key in ("below", "above", "left_of", "right_of", "over", "within"):
            if key in d and depth < 10:
                return self.region_of(selectors.parse(d[key]).obj, depth + 1)
        return "stage"

    def stack_below(self, obj_id: str, depth: int = 0) -> float:
        """声明在 obj_id 下面的对象（含它们下面的）总高度，含间距。"""
        total = 0.0
        if depth > 10:
            return total
        for other, built in self.objects.items():
            d = built.definition
            if "below" in d and selectors.parse(d["below"]).obj == obj_id:
                region = layout.regions(*self.c.usage_for(other))[self.region_of(other)]
                layout.fit(built.mob, region)
                total += layout.GAPS[d.get("gap", "md")] + built.mob.height + self.stack_below(other, depth + 1)
        return total

    def resolve(self, text: str, place: bool = False) -> Mobject:
        sel = selectors.parse(text)
        built = self.objects[sel.obj]
        if place:
            self.place(sel.obj)
        if sel.span:
            return built.spans[sel.span]
        if sel.part:
            cells = built.parts[sel.part]
            return VGroup(*cells[sel.index[0] : sel.index[1]]) if sel.index else VGroup(*cells)
        if sel.index:
            return VGroup(*built.items[sel.index[0] : sel.index[1]])
        return built.mob

    # ---- 主循环 ----

    def run(self) -> None:
        self.current_focus: dict[str, str] = dict(self.c.state.focus)
        self.restore()
        actions = self.beat.get("do", [])
        i = 0
        while i < len(actions):
            batch = [actions[i]]
            j = i + 1
            while j < len(actions) and "with" in actions[j]:
                batch.append(actions[j])
                j += 1
            self.play_batch(batch, next_at=self._next_at(actions, j))
            i = j
        if "pause" in self.beat:
            self.scene.wait(max(1.0, self.beat["pause"].get("hold", 2)))
        self.wait_until(self.timing.total + 0.5)
        pinned = {k for k, v in self.objects.items() if v.definition.get("pin")}
        for problem in layout.check(self.visible, self.ignore_pairs, pinned):
            self.c.problems.append(f"{self.beat['id']}: {problem}")
            self.c.log(f"LAYOUT {self.beat['id']}: {problem}")
        self.c.state = self.snapshot()

    def _next_at(self, actions: list, start: int) -> float | None:
        for a in actions[start:]:
            if "at" in a:
                return self.timing.resolve(a["at"])
        return None

    def wait_until(self, moment: float) -> None:
        gap = moment - self.scene.time
        if gap > 0.05:
            self.scene.wait(gap)

    def play_batch(self, batch: list[dict], next_at: float | None) -> None:
        head = batch[0]
        if "at" in head:
            at = self.timing.resolve(head["at"])
            late = self.scene.time - at
            if late > CUE_LATE and self.scene.time > 0.5:
                self.c.log(f"CUE LATE {self.beat['id']}[{head.get('at')}] 画面比旁白晚 {late:.1f}s")
            self.wait_until(at)
            self.c.timing_beats[self.beat["id"]]["actions"].append(
                {"at": head["at"], "want": round(at, 3), "start": round(self.scene.time, 3)})
        anims: list[Animation] = []
        run_time = 0.0
        for action in batch:
            a, d = self.animations(action)
            if action.get("dur") == "fit":
                d = max(0.3, (next_at - self.scene.time) if next_at is not None else d) - 0.1
            elif isinstance(action.get("dur"), (int, float)):
                d = float(action["dur"])
            anims += a
            run_time = max(run_time, d)
        if anims:
            self.scene.play(*anims, run_time=run_time)
        elif run_time > 0:  # hold
            self.scene.wait(run_time)

    # ---- 动作 → 动画 ----

    def animations(self, action: dict) -> tuple[list[Animation], float]:
        act = action["act"]
        args = action.get("args", {})
        if act == "hold":
            return [], float(action.get("dur", 1.0))
        if act == "show":
            sel = selectors.parse(action["target"])
            mob = self.resolve(action["target"], place=True)
            if sel.span is None and sel.part is None and sel.index is None:
                self.visible[sel.obj] = mob
            style = action.get("style", "fade")
            anim = Write(mob) if style == "write" else FadeIn(mob, shift=DOWN * 0.1 if style == "fade" else 0)
            return [anim], 1.0 if style == "write" else DEFAULT_DUR["show"]
        if act == "hide":
            sel = selectors.parse(action["target"])
            mob = self.resolve(action["target"])
            extras = self._drop_overlays(sel.obj)
            self.visible.pop(sel.obj, None)
            return [FadeOut(VGroup(mob, *extras))], DEFAULT_DUR["hide"]
        if act == "transform":
            src = self.resolve(action["from"])
            dst = self.resolve(action["to"], place=True)
            to_sel = selectors.parse(action["to"])
            self.visible[to_sel.obj] = dst
            if action.get("match") == "tex":
                return [TransformMatchingTex(src.copy(), dst)], DEFAULT_DUR["transform"]
            return [TransformFromCopy(src, dst)], DEFAULT_DUR["transform"]
        if act == "focus":
            return self._focus_anims(action["target"]), DEFAULT_DUR["focus"]
        if act == "unfocus":
            sel = selectors.parse(action["target"])
            return self._unfocus(sel.obj), DEFAULT_DUR["unfocus"]
        if act == "mark":
            rect = self._make_mark(action["target"])
            return [FadeIn(rect)], DEFAULT_DUR["mark"]
        if act == "unmark":
            rect = self.marks.pop(action["target"], None)
            return ([FadeOut(rect)] if rect else []), DEFAULT_DUR["unmark"]
        if act == "clear":
            sel = selectors.parse(action["target"])
            extras = self._drop_overlays(sel.obj, keep_focus=True)
            return ([FadeOut(VGroup(*extras))] if extras else []), DEFAULT_DUR["hide"]
        if act in ("underline", "circle", "cross"):
            deco = self._decoration(act, action["target"])
            self.decorations.append((act, action["target"], deco))
            return [Write(deco) if act != "cross" else FadeIn(deco)], DEFAULT_DUR[act]
        if act == "set":
            mob = self.resolve(action["target"])
            anims = []
            if "role" in args:
                anims.append(mob.animate.set_color(self.t.ink(args["role"])))
            if "opacity" in args:
                anims.append(mob.animate.set_opacity(float(args["opacity"])))
            return anims, DEFAULT_DUR["set"]
        if act == "move":
            sel = selectors.parse(action["target"])
            built = self.objects[sel.obj]
            built.placed = False
            new_def = {**built.definition, **{k: v for k, v in args.items()}}
            target = built.mob.copy()
            refs = {k: self.resolve(new_def[k], place=True) for k in ("below", "above", "left_of", "right_of", "over", "within") if k in new_def}
            layout.place(target, new_def, refs)
            built.placed = True
            return [Transform(built.mob, target)], DEFAULT_DUR["move"]
        if act == "camera":
            self.c.log(f"kit 0.1 不支持 camera，已跳过（{self.beat['id']}）")
            return [], 0.0
        # 组件动作
        sel = selectors.parse(action["target"])
        built = self.objects[sel.obj]
        anims, new = built.widget.action(act, args)
        return anims, WIDGET_DUR

    # ---- 强调、标记、装饰 ----

    def _focus_anims(self, target: str, instant: bool = False) -> list[Animation]:
        sel = selectors.parse(target)
        built = self.objects[sel.obj]
        self.current_focus[sel.obj] = target
        if built.widget and sel.index is not None:
            custom = built.widget.focus(sel.index[0])
            if custom is not None:
                if instant:
                    built.widget.restore(built.widget.state())
                    return []
                return custom
        mob = self.resolve(target)
        anims: list[Animation] = []
        if sel.obj in self.focus_boxes:
            old = self.focus_boxes.pop(sel.obj)
            self.scene.remove(old)
        box = SurroundingRectangle(mob, color=self.t.HL_INK, buff=0.08, corner_radius=0.08, stroke_width=2.5)
        self.focus_boxes[sel.obj] = box
        self.scene.add(box)
        # 同一对象里的其他兄弟调暗
        siblings = [m for m in (built.items or []) if m is not mob and not any(m is x for x in mob.submobjects)]
        self.dimmed[sel.obj] = siblings
        if instant:
            for s in siblings:
                s.set_opacity(0.45)
            return []
        anims.append(FadeIn(box))
        anims += [s.animate.set_opacity(0.45) for s in siblings]
        anims.append(mob.animate.set_opacity(1))
        return anims

    def _unfocus(self, obj_id: str) -> list[Animation]:
        built = self.objects[obj_id]
        self.current_focus.pop(obj_id, None)
        anims: list[Animation] = []
        if built.widget and built.widget.focus(None) is not None:
            anims += built.widget.focus(None)
        box = self.focus_boxes.pop(obj_id, None)
        if box is not None:
            anims.append(FadeOut(box))
        for s in self.dimmed.pop(obj_id, []):
            anims.append(s.animate.set_opacity(1))
        return anims

    def _make_mark(self, target: str) -> Mobject:
        mob = self.resolve(target)
        rect = RoundedRectangle(
            width=mob.width + 0.2, height=mob.height + 0.16, corner_radius=0.08,
            fill_color=self.t.MARK, fill_opacity=1, stroke_width=0,
        ).move_to(mob)
        # 垫在目标下面、但在目标所在卡片的底色上面：靠 z_index 排序
        rect.set_z_index(0.5)
        mob.set_z_index(1)
        self.marks[target] = rect
        return rect

    def _decoration(self, act: str, target: str) -> Mobject:
        mob = self.resolve(target)
        sel = selectors.parse(target)
        color = self.t.HL_INK
        if act == "underline":
            deco = Line(mob.get_corner([-1, -1, 0]), mob.get_corner([1, -1, 0]), color=color, stroke_width=4).shift(DOWN * 0.06)
        elif act == "circle":
            deco = SurroundingRectangle(mob, color=color, buff=0.1, corner_radius=0.12, stroke_width=3)
        else:
            deco = Cross(mob, stroke_color=self.t.NO, stroke_width=4)
        deco.owner = sel.obj
        return deco

    def _drop_overlays(self, obj_id: str, keep_focus: bool = False) -> list[Mobject]:
        """隐藏对象（或 clear）时，连同它身上的焦点框、标记、划线一起去掉。"""
        extras: list[Mobject] = []
        if not keep_focus:
            box = self.focus_boxes.pop(obj_id, None)
            if box is not None:
                extras.append(box)
            self.current_focus.pop(obj_id, None)
            self.dimmed.pop(obj_id, None)
        for sel in [s for s in self.marks if selectors.parse(s).obj == obj_id]:
            extras.append(self.marks.pop(sel))
        keep = []
        for act, sel, deco in self.decorations:
            if selectors.parse(sel).obj == obj_id:
                extras.append(deco)
            else:
                keep.append((act, sel, deco))
        self.decorations = keep
        return extras
