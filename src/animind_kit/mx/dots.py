"""十格框和点阵（墨线实现）：数的世界入门 #1–#4 的圆点。

两个组件共用一套尺寸（PITCH 一格、DOT 圆点半径），五集里十格框、点阵、方格一样大。

ten_frame：两排、每排五格的框，框旁边可以放一堆圆点。动作：
- fill：count 个圆点一个个放进框里的空格（从上排左边起，按读的顺序），role 定颜色（默认 A）；
- side：count 个圆点放在框的右边（两排，一列一列排），role 默认 B；
- move_in：框旁边的前 count 个圆点一个个跳进框里的空格，剩下的往左靠拢；
- count：给圆点逐个标数，which: side / frame（默认 side），start 是第一个点的数（默认 1）；
- recolor：which 那一组圆点换成 role 色；
- remove：which: side / frame / all（默认 all）的圆点和标数淡出（clear 是内置动作，这里叫 remove）。
参数 side（可选，默认 0）：框旁边最多放几个点，排版时预留这块地方。
暴露：frame（框，一项）、cell（十个格子，按读的顺序）、dots（全部圆点，一项）。

dot_array：rows 排、每排 cols 个的圆点阵，整个点阵的中心不动。动作：
- rotate：整个点阵转 90°（dir: ccw 逆时针，默认；cw 顺时针），每个点沿圆弧走到新位置，一个不多、一个不少，
  rows × cols 变成 cols × rows；
- extend：补到 cols 列和 / 或 rows 排（新的总数，不少于现在的），原来的点挪到新位置，新点一列一列（再一排一排）长出来，
  role 默认和点阵相同；
- fade_col：第 col 列（从 0 数，负数从右数，默认最后一列）的点换成 role 色（默认 NO）并淡到 opacity（默认 0.3），
  "拿掉"但留着位置；之后补出来的新排在这一列也是淡的；
- squares：每个点放进一个小方格（画出方格），fill 给方格垫 role 色（淡出过的点的方格垫得更淡），dots: false 时圆点淡出，
  只剩方格的长方形；
- recolor：全部点（或 row 那一排、col 那一列）换成 role 色。
参数 cells（可选，true）：一开始就画出每个点的方格（十格框的样子）；room（可选，[排, 列]）：排版时预留多大
（默认 max(rows, cols) 的正方形），点阵转过、补过之后都要放得下。
暴露：dots（全部点，一项）、cells（全部方格，一项）。

颜色和透明度都在动画开始播放时才改：一个 beat 的动作先全部建好再播放，建动作时不能动画面。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from moxian.anim import Animation, Draw, Fade, Grow, Restyle, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import circle, rect

from .text import mmath
from .widgets import Built, Widget

PITCH = 0.7  # 一格的边长（画面单位，排版前）
DOT = 0.22  # 圆点半径
SIDE_GAP = 0.45  # 十格框和旁边那堆点之间的空
COUNT = 30  # 标数字号
HOP = 0.9  # move_in 跳起的高度


class FadeTo(Fade):
    """淡到 end：起点在播放时读节点当时的透明度。"""

    def __init__(self, node: Node, end: float, duration: float = 0.4):
        super().__init__(node, 0.0, end, duration)

    def begin(self) -> None:
        super().begin()
        self.start = (self.node.style or Style()).opacity


class Travel(Animation):
    """节点平移，位移是 alpha 的函数（直线、圆弧、跳跃）；只改平移，不转不缩。"""

    def __init__(self, node: Node, offset: Callable[[float], np.ndarray], duration: float = 0.8,
                 ease=easing.smooth):
        super().__init__(node, duration, ease)
        self.offset = offset

    def begin(self) -> None:
        super().begin()
        self.base = self.node.matrix.copy()

    def interpolate(self, alpha: float) -> None:
        d = self.offset(alpha)
        self.node.matrix = T.translate(float(d[0]), float(d[1])) @ self.base


def straight(p0, p1) -> Callable[[float], np.ndarray]:
    a, b = np.asarray(p0, dtype=float), np.asarray(p1, dtype=float)
    return lambda t: (b - a) * t


def hop(p0, p1, height: float = HOP) -> Callable[[float], np.ndarray]:
    a, b = np.asarray(p0, dtype=float), np.asarray(p1, dtype=float)
    return lambda t: (b - a) * t + np.array([0.0, height * 4 * t * (1 - t)])


def arc(p0, angle: float) -> Callable[[float], np.ndarray]:
    """绕原点转 angle（弧度，逆时针为正）的圆弧。"""
    a = np.asarray(p0, dtype=float)

    def offset(t: float) -> np.ndarray:
        c, s = math.cos(angle * t), math.sin(angle * t)
        return np.array([c * a[0] - s * a[1], s * a[0] + c * a[1]]) - a

    return offset


class Steps(Animation):
    """几组动画依次播放（在整段时长里均分）；每组里的动画同时播。"""

    def __init__(self, steps: list[list[Animation]], duration: float = 1.5):
        super().__init__(Node(), duration, easing.linear)
        steps = [s for s in steps if s]
        n = max(1, len(steps))
        self.windows = [(k / n, (k + 1) / n, a) for k, step in enumerate(steps) for a in step]
        self.started: set[int] = set()

    def interpolate(self, alpha: float) -> None:
        for k, (start, end, a) in enumerate(self.windows):
            if alpha < start:
                continue
            if k not in self.started:
                a.begin()
                self.started.add(k)
            a.interpolate(a.ease(min(1.0, (alpha - start) / max(1e-6, end - start))))

    def finish(self) -> None:
        self.interpolate(1.0)


class DrawIn(Draw):
    """先显出来再画：透明度在播放时才改。"""

    def begin(self) -> None:
        self.node.set_style(opacity=1.0)
        super().begin()


def dot_node(color: str) -> PathNode:
    return PathNode(path=circle(DOT), style=Style(fill=color))


def placed(node: Node, p) -> Node:
    """节点放到 p（只用平移矩阵，之后的 Travel 在它上面叠加）。"""
    node.matrix = T.translate(float(p[0]), float(p[1]))
    return node


def reserve(width: float, height: float, cx: float = 0.0, cy: float = 0.0) -> PathNode:
    """看不见的占位框：排版按它定大小，动作前后组件的大小不变。"""
    return PathNode(path=rect(width, height).transformed(T.translate(cx, cy)), style=Style(opacity=0.0))


# ---------- 十格框 ----------

class TenFrame(Widget):
    name = "ten_frame"

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.max_side = int(d.get("side", 0))
        node = Group(name=self.obj_id)
        P = PITCH
        self.cell_pos = [np.array([(i % 5 - 2) * P, (0.5 - i // 5) * P]) for i in range(10)]
        cells = [PathNode(path=rect(P, P).transformed(T.translate(*p)), style=Style(stroke=t.FG, stroke_width=2))
                 for p in self.cell_pos]
        outline = PathNode(path=rect(5 * P, 2 * P), style=Style(stroke=t.FG, stroke_width=4))
        self.frame = Group(*cells, outline, name="frame")
        self.dots = Group(name="dots")
        self.labels = Group(name="labels")
        side_cols = math.ceil(self.max_side / 2)
        width = 5 * P + (SIDE_GAP + side_cols * P if side_cols else 0.0)
        top = P + 0.25 + mmath("8", COUNT, t.FG).height  # 上排点的标数
        node.add(reserve(width + 0.2, 2 * top, width / 2 - 2.5 * P, 0.0), self.frame, self.dots, self.labels)
        self.slots: list[PathNode | None] = [None] * 10  # 框里每格的点
        self.side_dots: list[PathNode] = []
        self.count_labels: dict[int, Node] = {}  # id(点) → 标数
        self.pos: dict[int, np.ndarray] = {}  # id(点) → 按动作推演的位置（建动作时节点还没动）
        return Built(node, parts={"frame": [self.frame], "cell": cells, "dots": [self.dots]})

    def side_pos(self, k: int) -> np.ndarray:
        P = PITCH
        return np.array([2.5 * P + SIDE_GAP + (k // 2 + 0.5) * P, (0.5 - k % 2) * P])

    def new_dot(self, role: str | None, default: str, p) -> PathNode:
        dot = dot_node(self.theme.color(role, default))
        dot.set_style(opacity=0.0)  # 还没出现：Grow 播放时才显出来
        placed(dot, p)
        self.pos[id(dot)] = np.asarray(p, dtype=float)
        self.dots.add(dot)
        return dot

    def group(self, which: str) -> list[PathNode]:
        frame = [s for s in self.slots if s is not None]
        return {"side": self.side_dots, "frame": frame, "all": frame + self.side_dots}[which]

    def action(self, act: str, args: dict) -> list[Animation]:
        t = self.theme
        if act == "fill":
            steps = []
            for _ in range(int(args["count"])):
                i = self.slots.index(None)
                self.slots[i] = self.new_dot(args.get("role"), t.A, self.cell_pos[i])
                steps.append([Grow(self.slots[i])])
            return [Steps(steps)]
        if act == "side":
            steps = []
            for _ in range(int(args["count"])):
                dot = self.new_dot(args.get("role"), t.B, self.side_pos(len(self.side_dots)))
                self.side_dots.append(dot)
                steps.append([Grow(dot)])
            return [Steps(steps)]
        if act == "move_in":
            n = int(args["count"])
            movers, self.side_dots = self.side_dots[:n], self.side_dots[n:]
            steps = []
            for k, dot in enumerate(movers):
                i = self.slots.index(None)
                self.slots[i] = dot
                self.pos[id(dot)] = self.cell_pos[i]
                steps.append([Travel(dot, hop(self.side_pos(k), self.cell_pos[i]))])
            # 剩下的往左靠拢（第 k 个点从 n + k 的位置挪到 k）
            slide = []
            for k, dot in enumerate(self.side_dots):
                self.pos[id(dot)] = self.side_pos(k)
                slide.append(Travel(dot, straight(self.side_pos(n + k), self.side_pos(k))))
            steps.append(slide)
            # 旁边的标数不再跟着点走：一开始跳就收起来
            labels = [self.count_labels.pop(id(dot)) for dot in movers + self.side_dots if id(dot) in self.count_labels]
            return [Steps([[FadeTo(lab, 0.0) for lab in labels]] + steps if labels else steps)]
        if act == "count":
            which = args.get("which", "side")
            start = int(args.get("start", 1))
            steps = []
            for k, dot in enumerate(self.group(which)):
                p = self.pos[id(dot)]
                row = round(0.5 - p[1] / PITCH)
                label = mmath(str(start + k), COUNT, t.HL_INK)
                label.move_to([p[0], p[1] + (1 if row == 0 else -1) * (PITCH / 2 + 0.05 + label.height / 2)])
                label.set_style(opacity=0.0)
                self.labels.add(label)
                self.count_labels[id(dot)] = label
                steps.append([FadeTo(label, 1.0)])
            return [Steps(steps)]
        if act == "recolor":
            color = t.color(args.get("role"), t.A)
            return [Restyle(dot, 0.4, fill=color) for dot in self.group(args.get("which", "all"))]
        if act == "remove":
            which = args.get("which", "all")
            gone = self.group(which)
            if which in ("frame", "all"):
                self.slots = [None] * 10
            if which in ("side", "all"):
                self.side_dots = []
            out: list[Animation] = [FadeTo(dot, 0.0) for dot in gone]
            out += [FadeTo(self.count_labels.pop(id(dot)), 0.0) for dot in gone if id(dot) in self.count_labels]
            return out
        raise KeyError(f"组件 {self.name} 没有动作 {act}")


# ---------- 点阵 ----------

@dataclass
class Slot:
    node: Group  # 方格 + 圆点，一起平移
    cell: PathNode
    dot: PathNode
    faded: bool = False


def grid_pos(r: int, c: int, rows: int, cols: int) -> np.ndarray:
    """rows × cols 点阵里第 r 排第 c 列的中心；点阵中心在原点。"""
    return np.array([(c - (cols - 1) / 2) * PITCH, ((rows - 1) / 2 - r) * PITCH])


def rotated(r: int, c: int, rows: int, cols: int, ccw: bool) -> tuple[int, int]:
    """转 90° 之后 (r, c) 落在 cols × rows 点阵的哪一格。"""
    return (cols - 1 - c, r) if ccw else (c, rows - 1 - r)


class DotArray(Widget):
    name = "dot_array"
    FADED = 0.3

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.rows, self.cols = int(d["rows"]), int(d["cols"])
        self.color = t.color(d.get("role"), t.A)
        self.cells_on = bool(d.get("cells", False))
        side = max(self.rows, self.cols)
        room_r, room_c = d.get("room") or [side, side]
        node = Group(name=self.obj_id)
        self.dots = Group(name="dots")
        self.cells = Group(name="cells")  # 只是引用容器：方格挂在各自的点上
        node.add(reserve(room_c * PITCH + 0.2, room_r * PITCH + 0.2), self.dots)
        self.grid: dict[tuple[int, int], Slot] = {}
        for r in range(self.rows):
            for c in range(self.cols):
                slot = self.new_slot(self.color, grid_pos(r, c, self.rows, self.cols), visible=True)
                self.grid[r, c] = slot
        return Built(node, parts={"dots": [self.dots], "cells": [self.cells]})

    def new_slot(self, color: str, p, visible: bool) -> Slot:
        t = self.theme
        cell = PathNode(path=rect(PITCH, PITCH), style=Style(stroke=t.FG, stroke_width=2, opacity=1.0 if self.cells_on else 0.0))
        dot = dot_node(color)
        slot = Slot(placed(Group(cell, dot, name="slot"), p), cell, dot)
        slot.node.style = Style(opacity=1.0 if visible else 0.0)
        self.dots.add(slot.node)
        self.cells.children.append(cell)
        return slot

    def action(self, act: str, args: dict) -> list[Animation]:
        t = self.theme
        R, C = self.rows, self.cols
        if act == "rotate":
            ccw = args.get("dir", "ccw") == "ccw"
            angle = math.pi / 2 if ccw else -math.pi / 2
            out, grid = [], {}
            for (r, c), slot in self.grid.items():
                out.append(Travel(slot.node, arc(grid_pos(r, c, R, C), angle), 1.2))
                grid[rotated(r, c, R, C, ccw)] = slot
            self.grid, self.rows, self.cols = grid, C, R
            return out
        if act == "extend":
            R2, C2 = int(args.get("rows", R)), int(args.get("cols", C))
            color = t.color(args.get("role"), self.color)
            slide = []
            for (r, c), slot in self.grid.items():
                a, b = grid_pos(r, c, R, C), grid_pos(r, c, R2, C2)
                if not np.allclose(a, b):
                    slide.append(Travel(slot.node, straight(a, b)))
            faded_cols = {c for (_r, c), s in self.grid.items() if s.faded}
            steps = [slide]
            new = [[(r, c) for r in range(R)] for c in range(C, C2)] + [[(r, c) for c in range(C2)] for r in range(R, R2)]
            for batch in new:
                step = []
                for r, c in batch:
                    slot = self.new_slot(color, grid_pos(r, c, R2, C2), visible=False)
                    if c in faded_cols:  # 还没出现，可以直接定样式
                        slot.faded = True
                        slot.dot.style = Style(fill=t.color(args.get("faded_role"), t.NO), opacity=self.FADED)
                    self.grid[r, c] = slot
                    step.append(Grow(slot.node))
                steps.append(step)
            self.rows, self.cols = R2, C2
            return [Steps(steps)]
        if act == "fade_col":
            col = int(args.get("col", -1)) % C
            color = t.color(args.get("role"), t.NO)
            opacity = float(args.get("opacity", self.FADED))
            out = []
            for r in range(R):
                slot = self.grid[r, col]
                slot.faded = True
                out.append(Restyle(slot.dot, 0.6, fill=color, opacity=opacity))
            return out
        if act == "squares":
            fill = t.color(args["fill"]) if args.get("fill") else None
            self.cells_on = True
            out: list[Animation] = []
            for slot in self.grid.values():
                out.append(DrawIn(slot.cell))
                if fill:  # 拿掉的那一列垫得更淡，变成方格以后还看得出少了一列
                    out.append(Restyle(slot.cell, 0.6, fill=fill, fill_opacity=0.08 if slot.faded else 0.35))
                if args.get("dots", True) is False:
                    out.append(FadeTo(slot.dot, 0.0))
            return out
        if act == "recolor":
            color = t.color(args.get("role"), t.A)
            pick = [s for (r, c), s in self.grid.items()
                    if ("row" not in args or r == int(args["row"]) % R) and ("col" not in args or c == int(args["col"]) % C)]
            return [Restyle(s.dot, 0.4, fill=color) for s in pick]
        raise KeyError(f"组件 {self.name} 没有动作 {act}")
