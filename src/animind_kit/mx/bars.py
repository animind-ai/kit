"""积木条形图（墨线实现）：一列一列叠起来的小方块，平均数"削峰填谷"用。

block_bars：heights 是每一列有几块（从左往右），每块是边长 PITCH 的方块，列底对齐在一条底线上。动作：
- transfer：from 列顶上的 count 块（默认 1）一块一块跳到 to 列顶上；列上方的块数（numbers: true 时）跟着变；
- add：在右边的空位上长出新的一列，height 块，role 默认 B，label 是这一列底下的名字（可选）；
- level：在 at 块高处画一条横贯所有列的虚线，右边写 label（文字可以混排公式），role 默认 HL；
- unlevel：横线和标签淡出；
- recolor：全部块（或 col 那一列现在的块）换成 role 色。
参数：heights（必填，非负整数列表）；room（可选）：排版时预留几块高（默认最高的一列）；cols（可选）：预留几列
（默认 heights 的列数，add 要用的空位算在里面）；labels（可选）：每列底下的名字；numbers（可选，true）：列上方写块数；
role（可选，默认 A）：方块的颜色。
暴露：bars（全部方块，一项）、line（横线和标签，一项）。

颜色、透明度和位置都在动画开始播放时才改：一个 beat 的动作先全部建好再播放，建动作时不能动画面。
"""

from __future__ import annotations

import numpy as np
from moxian.anim import Animation, Grow, Restyle
from moxian.core import Group, Node, PathNode, Style
from moxian.path import Path, rect

from .dissect import dashed
from .dots import FadeTo, Steps, Travel, hop, placed, reserve
from .text import mmath, mtext, rich
from .widgets import Built, Widget

PITCH = 0.5  # 一块的边长（画面单位，排版前）
GAP = 0.35  # 列与列之间的空
NUM = 34  # 块数的字号
NAME = 32  # 列名的字号
LINE_LABEL = 34  # 横线标签的字号
LABEL_ROOM = 2.6  # 横线右边给标签留的宽度
HOP_UP = 0.6  # 跳的时候比两列里较高的那列再高出多少


class BlockBars(Widget):
    name = "block_bars"

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.heights = [int(h) for h in d["heights"]]
        self.ncols = int(d.get("cols", len(self.heights)))
        self.room = int(d.get("room", max(self.heights + [1])))
        self.role = d.get("role")
        self.numbers = bool(d.get("numbers", False))
        node = Group(name=self.obj_id)
        self.step = PITCH + GAP
        self.width = self.ncols * self.step - GAP
        self.base = -self.room * PITCH / 2
        num_h = mmath("8", NUM, t.FG).height + 0.15 if self.numbers else 0.0
        names = d.get("labels") or []
        name_h = mtext("名", NAME, t.FG).height + 0.15 if names else 0.0
        top, bottom = self.room * PITCH / 2 + num_h, self.base - name_h - 0.1
        node.add(reserve(self.width + LABEL_ROOM, top - bottom, LABEL_ROOM / 2, (top + bottom) / 2))
        axis = Path().move_to(-self.width / 2 - 0.15, self.base).line_to(self.width / 2 + 0.15, self.base)
        node.add(PathNode(path=axis, style=Style(stroke=t.FG, stroke_width=3)))
        self.blocks = Group(name="bars")
        self.marks = Group(name="numbers")
        self.line = Group(name="line")
        node.add(self.blocks, self.marks, self.line)
        self.cols: list[list[PathNode]] = [[] for _ in range(self.ncols)]
        for k, h in enumerate(self.heights):
            for j in range(h):
                self.cols[k].append(self.new_block(k, j, self.role, visible=True))
        for k, name in enumerate(names):
            label = mtext(str(name), NAME, t.FG)
            label.move_to([self.x(k), self.base - 0.15 - label.height / 2])
            node.add(label)
        self.num_nodes: list[Node | None] = [None] * self.ncols
        if self.numbers:
            for k in range(len(self.heights)):
                self.num_nodes[k] = self.new_number(k, visible=True)
        return Built(node, parts={"bars": [self.blocks], "line": [self.line]})

    def x(self, k: int) -> float:
        return -self.width / 2 + PITCH / 2 + k * self.step

    def pos(self, k: int, j: int) -> np.ndarray:
        return np.array([self.x(k), self.base + (j + 0.5) * PITCH])

    def new_block(self, k: int, j: int, role: str | None, visible: bool) -> PathNode:
        t = self.theme
        color = t.color(role, t.A)
        block = PathNode(path=rect(PITCH, PITCH), style=Style(fill=color, fill_opacity=0.55, stroke=color, stroke_width=2,
                                                              opacity=1.0 if visible else 0.0))
        placed(block, self.pos(k, j))
        self.blocks.add(block)
        return block

    def new_number(self, k: int, visible: bool) -> Node:
        t = self.theme
        n = len(self.cols[k])
        label = mmath(str(n), NUM, t.FG)
        label.move_to([self.x(k), self.base + n * PITCH + 0.1 + label.height / 2])
        label.set_style(opacity=1.0 if visible else 0.0)
        self.marks.add(label)
        return label

    def renumber(self, k: int) -> list[Animation]:
        """第 k 列的块数换成现在的：旧的淡出，新的淡入（播放时）。"""
        if not self.numbers:
            return []
        old = self.num_nodes[k]
        self.num_nodes[k] = new = self.new_number(k, visible=False)
        return ([FadeTo(old, 0.0, 0.25)] if old is not None else []) + [FadeTo(new, 1.0, 0.25)]

    def action(self, act: str, args: dict) -> list[Animation]:
        t = self.theme
        if act == "transfer":
            src, dst = int(args["from"]), int(args["to"])
            steps = []
            for _ in range(int(args.get("count", 1))):
                block = self.cols[src].pop()
                p0, p1 = self.pos(src, len(self.cols[src])), self.pos(dst, len(self.cols[dst]))
                self.cols[dst].append(block)
                lift = max(p0[1], p1[1]) - min(p0[1], p1[1]) + HOP_UP
                steps.append([Travel(block, hop(p0, p1, lift), 0.6)] + self.renumber(src) + self.renumber(dst))
            return [Steps(steps, duration=0.6 * len(steps))]
        if act == "add":
            k = next(i for i, c in enumerate(self.cols) if not c and i >= len(self.heights))
            self.heights.append(int(args["height"]))
            steps = []
            for j in range(int(args["height"])):
                block = self.new_block(k, j, args.get("role", "B"), visible=False)
                self.cols[k].append(block)
                steps.append([Grow(block)])
            if self.numbers:
                steps.append(self.renumber(k))
            if args.get("label"):
                name = mtext(str(args["label"]), NAME, t.FG)
                name.move_to([self.x(k), self.base - 0.15 - name.height / 2])
                name.set_style(opacity=0.0)
                self.node.add(name)
                steps[0].append(FadeTo(name, 1.0, 0.3))
            return [Steps(steps, duration=max(0.8, 0.12 * len(steps)))]
        if act == "level":
            at = float(args["at"])
            y = self.base + at * PITCH
            color = t.color(args.get("role"), t.HL)
            x0, x1 = -self.width / 2 - 0.25, self.width / 2 + 0.25
            out: list[Animation] = [FadeTo(old, 0.0, 0.4) for old in self.line.children]  # 换一条线：旧的淡出
            dash = PathNode(path=dashed([((x0, y), (x1, y))], 0.18, 0.12), style=Style(stroke=color, stroke_width=4, opacity=0.0))
            self.line.add(dash)
            out.append(FadeTo(dash, 1.0, 0.5))
            if args.get("label"):
                label = rich(str(args["label"]), t, size=LINE_LABEL, color=t.ink(args.get("role", "HL"))).node
                label.move_to([x1 + 0.15 + label.width / 2, y])
                label.set_style(opacity=0.0)
                self.line.add(label)
                out.append(FadeTo(label, 1.0, 0.5))
            return out
        if act == "unlevel":
            return [FadeTo(n, 0.0, 0.4) for n in self.line.children]
        if act == "recolor":
            color = t.color(args.get("role"), t.A)
            blocks = self.cols[int(args["col"])] if "col" in args else [b for c in self.cols for b in c]
            return [Restyle(b, 0.4, fill=color, stroke=color) for b in blocks]
        raise KeyError(f"组件 {self.name} 没有动作 {act}")
