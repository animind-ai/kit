"""天平（墨线实现）：解方程就是天平（洞察 #19）。

balance：一根横梁架在支点上，两头各有一个托盘（托盘始终水平，跟着梁的端点走），盘里放小方块砝码（每块重 1）和
一个未知盒子（重量 box，标 box_label）。两边一样重时梁是平的，不一样重时往重的一边倾斜 TILT 度。动作：
- remove：side（left / right / both）的盘里拿走 count 块砝码（从最后放上去的拿起），砝码往上飘走淡出；拿完梁转到新的倾斜；
- add：side 的盘里放上 count 块砝码（从上方落下），role 定颜色（默认和原来的一样）；放完梁转到新的倾斜；
- label：未知盒子上的字换成 text（例如揭晓答案）。
参数：left、right（必填）：盘里的东西，列表里写 1（一块砝码）或 "box"（未知盒子，每边最多一个）；box（必填，正整数）：
盒子的重量（只用来判断平不平，画面上不显示）；box_label（可选，默认 "x"）：盒子上的字；role（可选，默认 A）：砝码颜色；
room（可选）：每个盘最多放几块（排版预留，默认两边最多的那个）。
暴露：left / right（两个盘和盘里的东西，各一项）、beam（横梁，一项）、box（未知盒子，一项）。

颜色、透明度和位置都在动画开始播放时才改：一个 beat 的动作先全部建好再播放，建动作时不能动画面。
"""

from __future__ import annotations

import math

import numpy as np
from moxian.anim import Animation, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import Path, line, rect

from .dots import FadeTo, Steps, Travel, reserve, straight
from .text import mmath
from .widgets import Built, Widget

HALF = 3.0  # 半根横梁长
POST = 0.35  # 托盘离梁端多高
TRAY = 2.2  # 托盘宽
SIZE = 0.44  # 一块砝码的边长
GAP = 0.06
PER_ROW = 4
TILT = math.radians(9)
FOOT = 1.5  # 支点到底座的高
LABEL = 34


class _Tilt(Animation):
    """梁从当前角度转到 angle：梁绕支点转，两个托盘只平移（保持水平）。"""

    def __init__(self, widget: Balance, angle: float, duration: float = 0.8):
        super().__init__(widget.beam, duration, easing.smooth)
        self.w, self.angle = widget, angle

    def begin(self) -> None:
        super().begin()
        self.start = self.w.shown_angle

    def interpolate(self, alpha: float) -> None:
        a = self.start + (self.angle - self.start) * alpha
        self.w.pose(a)

    def finish(self) -> None:
        self.interpolate(1.0)


class Balance(Widget):
    name = "balance"

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.box_weight = int(d["box"])
        self.color = t.color(d.get("role"), t.A)
        self.items = {"left": list(d["left"]), "right": list(d["right"])}
        counts = [len(v) for v in self.items.values()]
        self.room = int(d.get("room", max(counts + [1])))
        rows = math.ceil(self.room / PER_ROW)
        node = Group(name=self.obj_id)
        lift = HALF * math.sin(TILT)
        top = POST + rows * (SIZE + GAP) + lift + 0.2
        width = 2 * HALF + TRAY
        node.add(reserve(width + 0.2, top + FOOT + 0.2, 0.0, (top - FOOT) / 2))
        base = Path().move_to(0.0, 0.0).line_to(-0.55, -FOOT).line_to(0.55, -FOOT).close()
        node.add(PathNode(path=base, style=Style(fill=t.DIM, fill_opacity=0.35, stroke=t.FG, stroke_width=3)))
        node.add(PathNode(path=line(-1.2, -FOOT, 1.2, -FOOT), style=Style(stroke=t.FG, stroke_width=4)))
        self.beam = PathNode(path=line(-HALF, 0.0, HALF, 0.0), style=Style(stroke=t.FG, stroke_width=6))
        node.add(self.beam)
        self.pans: dict[str, Group] = {}
        self.slots: dict[str, list[Node]] = {}
        self.box: Group | None = None
        for side, sign in (("left", -1.0), ("right", 1.0)):
            pan = Group(name=side)
            post = PathNode(path=line(0.0, 0.0, 0.0, POST), style=Style(stroke=t.FG, stroke_width=4))
            tray = PathNode(path=line(-TRAY / 2, POST, TRAY / 2, POST), style=Style(stroke=t.FG, stroke_width=5))
            pan.add(post, tray)
            self.pans[side] = pan
            self.slots[side] = []
            for k, item in enumerate(self.items[side]):
                self.slots[side].append(self._item(pan, item, k, visible=True))
            pan.matrix = T.translate(sign * HALF, 0.0)
            node.add(pan)
        self.shown_angle = self.angle()
        self.pose(self.shown_angle)
        parts = {"left": [self.pans["left"]], "right": [self.pans["right"]], "beam": [self.beam],
                 "box": [self.box if self.box is not None else Group()]}
        return Built(node, parts=parts)

    # ---- 几何 ----

    def spot(self, k: int) -> np.ndarray:
        """盘里第 k 个东西的中心（托盘坐标）：一排 PER_ROW 个，从左往右、从下往上。"""
        row, col = divmod(k, PER_ROW)
        x = (col - (PER_ROW - 1) / 2) * (SIZE + GAP)
        y = POST + 0.03 + SIZE / 2 + row * (SIZE + GAP)
        return np.array([x, y])

    def _item(self, pan: Group, item, k: int, visible: bool, role: str | None = None) -> Node:
        t = self.theme
        p = self.spot(k)
        if item == "box":
            g = Group(PathNode(path=rect(SIZE, SIZE), style=Style(fill=t.B, fill_opacity=0.5, stroke=t.B, stroke_width=3)),
                      name="box")
            self.box_text = mmath(str(self.definition.get("box_label", "x")), LABEL, t.FG)
            self.box_text.move_to([0.0, 0.0])
            g.add(self.box_text)
            self.box = g
        else:
            color = t.color(role, None) if role else self.color
            g = Group(PathNode(path=rect(SIZE, SIZE), style=Style(fill=color, fill_opacity=0.55, stroke=color, stroke_width=2)))
        g.matrix = T.translate(float(p[0]), float(p[1]))
        g.set_style(opacity=1.0 if visible else 0.0)
        pan.add(g)
        return g

    def weight(self, side: str) -> int:
        return sum(self.box_weight if i == "box" else int(i) for i in self.items[side])

    def angle(self) -> float:
        """右边重，梁顺时针转（右端往下）：角度为负。"""
        diff = self.weight("right") - self.weight("left")
        return 0.0 if diff == 0 else (-TILT if diff > 0 else TILT)

    def pose(self, a: float) -> None:
        self.beam.matrix = T.rotate(a, about=(0.0, 0.0))
        for side, sign in (("left", -1.0), ("right", 1.0)):
            self.pans[side].matrix = T.translate(sign * HALF * math.cos(a), sign * HALF * math.sin(a))
        self.shown_angle = a

    # ---- 动作 ----

    def action(self, act: str, args: dict) -> list[Animation]:
        if act in ("remove", "add"):
            sides = ["left", "right"] if args.get("side", "both") == "both" else [args["side"]]
            n = int(args.get("count", 1))
            steps: list[list[Animation]] = []
            for _ in range(n):
                step = []
                for side in sides:
                    if act == "remove":
                        k = max(i for i, it in enumerate(self.items[side]) if it != "box")
                        self.items[side].pop(k)
                        node = self.slots[side].pop(k)
                        step += [Travel(node, straight((0, 0), (0, 0.6)), 0.5), FadeTo(node, 0.0, 0.5)]
                    else:
                        k = len(self.items[side])
                        self.items[side].append(1)
                        node = self._item(self.pans[side], 1, k, visible=False, role=args.get("role"))
                        self.slots[side].append(node)
                        node.matrix = T.translate(0.0, 0.6) @ node.matrix
                        step += [Travel(node, straight((0, 0.6), (0, 0)), 0.5), FadeTo(node, 1.0, 0.3)]
                steps.append(step)
            return [Steps(steps, duration=max(0.6, 0.35 * n)), _Tilt(self, self.angle())]
        if act == "label":
            if self.box is None:
                raise ValueError(f"{self.obj_id}：没有未知盒子，不能 label")
            t = self.theme
            new = mmath(str(args["text"]), LABEL, t.ink("HL"))
            new.move_to([0.0, 0.0])
            new.set_style(opacity=0.0)
            self.box.add(new)
            old, self.box_text = self.box_text, new
            return [FadeTo(old, 0.0, 0.3), FadeTo(new, 1.0, 0.4)]
        raise KeyError(f"组件 {self.name} 没有动作 {act}")
