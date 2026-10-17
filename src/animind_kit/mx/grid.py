"""格子正方形（墨线实现）：分数乘法的"一部分的一部分"。

一个代表 1 的正方形，竖着分 cols 列、横着分 rows 行（cols × rows 格）。动作：

- cut：画出切线，axis: cols（竖线）或 rows（横线），线从一端长到另一端；
- shade：涂色，cols: n 从左涂 n 列，或 rows: n 从上涂 n 行；role 定颜色（默认 A）；列和行各一层，叠在一起时两层都看得见；
- overlap：既在涂过的列里、又在涂过的行里的格子换成 HL，依次标上 1、2、3…（"交叠的格子有几个"）。

暴露的子元素：cols / rows（两组切线，各一项）、overlap（交叠格和它们的编号，一项）、cell（全部格子，按行从左往右）。

rect: true 时是另一种用法（数的世界入门 #4、#5：分配律、竖式乘法）：每格是边长相同的小方格，整块是 cols 宽、rows 高的
长方形（和 dot_array 的方格一样大；格子多时按 MAX_W × MAX_H 缩小每格）。lines: false 时不画每一格的细线（格子太多时只看按比例的大块）。动作：

- split：cols: [5, 3] 和 / 或 rows: [10, 4]（各段长度，加起来等于 cols / rows），画出切线，上边和左边标出每段的长度；
- apart：沿切线把各块分开 gap（默认 0.35）；join：拼回去（切线留着）；
- fill：给块涂 role 色（默认 A）：col / row 是第几段（从 0 数，不写就是所有段），或者 cells: "a:b"（按行从左往右数的格子）；
  label: true 在每块中间写它的格数（宽 × 高，精确整数），也可以写一段 LaTeX；
- unfill：收起涂色和块里的数。
rect 模式下没有 cut / shade / overlap；暴露 cell（全部格子）、cut（切线和边上的长度，一项）、areas（块里的数，一项）。
"""

from __future__ import annotations

from bisect import bisect_right

from moxian.anim import Animation, Draw, Fade, Restyle, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import line, rect, rounded_rect
from scene_spec.selectors import parse_slice

from .dots import PITCH, Travel, reserve, straight
from .text import mmath
from .widgets import Built, Widget


class _FadeTo(Fade):
    def __init__(self, node: Node, end: float, duration: float = 0.4):
        super().__init__(node, 0.0, end, duration)

    def begin(self) -> None:
        super().begin()
        self.start = (self.node.style or Style()).opacity


class _Steps(Animation):
    """几组动画依次播放（在整段时长里均分）。"""

    def __init__(self, steps: list[list[Animation]], duration: float = 1.5):
        super().__init__(Node(), duration, easing.linear)
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


class _DrawIn(Draw):
    """先显出来再画：透明度在播放时才改，排时间线时不动画面。"""

    def begin(self) -> None:
        self.node.set_style(opacity=1.0)
        super().begin()


class AreaGrid(Widget):
    name = "area_grid"
    SIDE = 3.6  # 正方形边长（画面单位，排版前）
    NUM = 30

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.cols, self.rows = int(d["cols"]), int(d["rows"])
        self.rect_mode = bool(d.get("rect", False))
        if self.rect_mode:
            return self.build_rect()
        s = self.SIDE
        self.cw, self.rh = s / self.cols, s / self.rows
        node = Group(name=self.obj_id)
        # 两层涂色：列一层、行一层；交叠层在最上
        self.col_fill, self.row_fill, self.hl_fill = [], [], []
        layers = (Group(name="colfill"), Group(name="rowfill"), Group(name="overlap"))
        for r in range(self.rows):
            for c in range(self.cols):
                cx, cy = (c + 0.5) * self.cw, s - (r + 0.5) * self.rh
                for layer, store in zip(layers, (self.col_fill, self.row_fill, self.hl_fill)):
                    cell = PathNode(path=rect(self.cw, self.rh).transformed(T.translate(cx, cy)),
                                    style=Style(fill=t.A, opacity=0.0))
                    layer.add(cell)
                    store.append(cell)
        self.numbers = Group(name="numbers")
        layers[2].add(self.numbers)
        self.overlap_group = layers[2]
        outline = PathNode(path=rect(s, s).transformed(T.translate(s / 2, s / 2)), style=Style(stroke=t.FG, stroke_width=3))
        self.col_lines = Group(*[PathNode(path=line(c * self.cw, 0.0, c * self.cw, s), style=Style(stroke=t.FG, stroke_width=2, opacity=0.0))
                                 for c in range(1, self.cols)], name="cols")
        self.row_lines = Group(*[PathNode(path=line(0.0, r * self.rh, s, r * self.rh), style=Style(stroke=t.FG, stroke_width=2, opacity=0.0))
                                 for r in range(1, self.rows)], name="rows")
        node.add(*layers, self.col_lines, self.row_lines, outline)
        cells = [Group(self.col_fill[i], self.row_fill[i], self.hl_fill[i]) for i in range(self.rows * self.cols)]
        self.shaded_cols, self.shaded_rows = 0, 0
        return Built(node, parts={"cols": [self.col_lines], "rows": [self.row_lines], "overlap": [self.overlap_group],
                                  "cell": cells})

    def action(self, act: str, args: dict) -> list[Animation]:
        t = self.theme
        if self.rect_mode:
            return self.rect_action(act, args)
        if act == "cut":
            lines = self.col_lines if args["axis"] == "cols" else self.row_lines
            return [_DrawIn(ln) for ln in lines.children]
        if act == "shade":
            color = t.color(args.get("role"), t.A)
            if "cols" in args:
                self.shaded_cols = int(args["cols"])
                pick = [r * self.cols + c for r in range(self.rows) for c in range(self.shaded_cols)]
                layer, alpha = self.col_fill, 0.45
            else:
                self.shaded_rows = int(args["rows"])
                pick = [r * self.cols + c for r in range(self.shaded_rows) for c in range(self.cols)]
                layer, alpha = self.row_fill, 0.35
            out = []
            for i in pick:
                layer[i].set_style(fill=color)
                out.append(_FadeTo(layer[i], alpha))
            return out
        if act == "overlap":
            steps = []
            k = 0
            for r in range(self.shaded_rows):
                for c in range(self.shaded_cols):
                    k += 1
                    i = r * self.cols + c
                    cell = self.hl_fill[i]
                    cell.set_style(fill=t.HL)
                    num = mmath(str(k), self.NUM, t.HL_INK).move_to([(c + 0.5) * self.cw, self.SIDE - (r + 0.5) * self.rh])
                    num.set_style(opacity=0.0)
                    self.numbers.add(num)
                    steps.append([_FadeTo(cell, 0.6), _FadeTo(num, 1.0)])
            return [_Steps(steps)]
        raise KeyError(f"组件 {self.name} 没有动作 {act}")

    # ---------- rect 模式：小方格拼成的长方形，切开、分开、拼回 ----------

    LABEL = 34  # 边上长度、块里格数的字号
    GAP = 0.35  # apart 默认分开多远
    MAX_W, MAX_H = 9.0, 5.0  # 格子多时缩小每格，整块不超过这么大，排版就不用把标注一起缩小

    def build_rect(self) -> Built:
        t = self.theme
        C, R = self.cols, self.rows
        self.u = u = min(PITCH, self.MAX_W / C, self.MAX_H / R)
        # 标注字号：后端排版后按实际缩放把 label_size 抬到 "label" 类的字号下限（和 number_line 一样）
        self.label_floor = "label"
        self.label_size = float(self.opts.get("label_size", self.LABEL))
        self.acted = False
        self.W, self.H = C * u, R * u
        node = Group(name=self.obj_id)
        lines = self.definition.get("lines", True)
        self.cells: list[Group] = []
        self.fills: list[PathNode] = []
        for r in range(R):
            for c in range(C):
                cx, cy = (c + 0.5) * u, self.H - (r + 0.5) * u
                fill = PathNode(path=rect(u, u).transformed(T.translate(cx, cy)), style=Style(fill=t.A, opacity=0.0))
                cell = Group(fill, name="cell")
                if lines:
                    cell.add(PathNode(path=rect(u, u).transformed(T.translate(cx, cy)), style=Style(stroke=t.DIM, stroke_width=1.5)))
                self.cells.append(cell)
                self.fills.append(fill)
        self.outline = PathNode(path=rect(self.W, self.H).transformed(T.translate(self.W / 2, self.H / 2)),
                                style=Style(stroke=t.FG, stroke_width=4))
        self.cut = Group(name="cut")  # 切线和边上的长度
        self.areas = Group(name="areas")  # 块里的数
        self.blocks_layer = Group(name="blocks")  # 分开以后每块自己的外框
        self.block_fill = Group(name="blockfill")  # 整块的涂色：一块一个长方形，块里的数不压在格子边上
        pad = 1.0  # 边上的长度、分开的空隙
        node.add(reserve(self.W + 2 * pad, self.H + 2 * pad, self.W / 2, self.H / 2), self.block_fill, *self.cells, self.blocks_layer,
                 self.outline, self.cut, self.areas)
        self.col_cuts, self.row_cuts = [0, C], [0, R]
        self.cut_lines: list[PathNode] = []
        self.top_labels: list[Node] = []
        self.left_labels: list[Node] = []
        self.block_frames: dict[tuple[int, int], PathNode] = {}
        self.extras: dict[tuple[int, int], list[Node]] = {}  # 块里的数，跟着块走
        self.dx, self.dy = [0.0], [0.0]  # 每段现在的偏移（apart 之后不为 0）
        return Built(node, parts={"cell": self.cells, "cut": [self.cut], "areas": [self.areas]})

    def block_of(self, r: int, c: int) -> tuple[int, int]:
        return bisect_right(self.col_cuts, c) - 1, bisect_right(self.row_cuts, r) - 1

    def block_box(self, i: int, j: int) -> tuple[float, float, float, float]:
        """第 i 段列、第 j 段行那一块（未分开时）的中心和宽高。"""
        u = self.u
        c0, c1 = self.col_cuts[i], self.col_cuts[i + 1]
        r0, r1 = self.row_cuts[j], self.row_cuts[j + 1]
        return (c0 + c1) / 2 * u, self.H - (r0 + r1) / 2 * u, (c1 - c0) * u, (r1 - r0) * u

    def label(self, tex: str, p) -> Node:
        lab = mmath(tex, self.label_size, self.theme.FG).move_to(p)
        lab.tex = tex  # 测试和排查时读回写的是什么
        lab.font_floor = self.label_floor
        lab.set_style(opacity=0.0)
        return lab

    def rect_action(self, act: str, args: dict) -> list[Animation]:
        t, u = self.theme, self.u
        self.acted = True  # 做过动作之后后端就不再重建它
        if act == "split":
            out: list[Animation] = []
            if "cols" in args:
                self.col_cuts = [0]
                for n in args["cols"]:
                    self.col_cuts.append(self.col_cuts[-1] + int(n))
            if "rows" in args:
                self.row_cuts = [0]
                for n in args["rows"]:
                    self.row_cuts.append(self.row_cuts[-1] + int(n))
            for k in self.col_cuts[1:-1]:
                ln = PathNode(path=line(k * u, 0.0, k * u, self.H), style=Style(stroke=t.HL_INK, stroke_width=5, opacity=0.0))
                self.cut_lines.append(ln)
                self.cut.add(ln)
                out.append(_DrawIn(ln))
            for k in self.row_cuts[1:-1]:
                y = self.H - k * u
                ln = PathNode(path=line(0.0, y, self.W, y), style=Style(stroke=t.HL_INK, stroke_width=5, opacity=0.0))
                self.cut_lines.append(ln)
                self.cut.add(ln)
                out.append(_DrawIn(ln))
            # 边上的长度：重新切时换掉旧的
            out += [_FadeTo(lab, 0.0) for lab in self.top_labels + self.left_labels]
            self.top_labels, self.left_labels = [], []
            for i in range(len(self.col_cuts) - 1):
                n = self.col_cuts[i + 1] - self.col_cuts[i]
                lab = self.label(str(n), [0.0, 0.0])
                lab.move_to([(self.col_cuts[i] + n / 2) * u, self.H + 0.12 + lab.height / 2])
                self.top_labels.append(lab)
            for j in range(len(self.row_cuts) - 1):
                n = self.row_cuts[j + 1] - self.row_cuts[j]
                lab = self.label(str(n), [0.0, 0.0])
                lab.move_to([-0.15 - lab.width / 2, self.H - (self.row_cuts[j] + n / 2) * u])
                self.left_labels.append(lab)
            self.cut.add(*self.top_labels, *self.left_labels)
            out += [_FadeTo(lab, 1.0) for lab in self.top_labels + self.left_labels]
            self.dx = [0.0] * (len(self.col_cuts) - 1)
            self.dy = [0.0] * (len(self.row_cuts) - 1)
            self.block_frames = {}
            for i in range(len(self.dx)):
                for j in range(len(self.dy)):
                    cx, cy, w, h = self.block_box(i, j)
                    frame = PathNode(path=rect(w, h).transformed(T.translate(cx, cy)), style=Style(stroke=t.FG, stroke_width=4, opacity=0.0))
                    self.blocks_layer.add(frame)
                    self.block_frames[i, j] = frame
            return out
        if act in ("apart", "join"):
            gap = float(args.get("gap", self.GAP)) if act == "apart" else 0.0
            ni, nj = len(self.dx), len(self.dy)
            dx = [(i - (ni - 1) / 2) * gap for i in range(ni)]
            dy = [((nj - 1) / 2 - j) * gap for j in range(nj)]

            def move(node: Node, a: tuple[float, float], b: tuple[float, float]) -> Travel:
                return Travel(node, straight(a, b))

            out = []
            for r in range(self.rows):
                for c in range(self.cols):
                    i, j = self.block_of(r, c)
                    out.append(move(self.cells[r * self.cols + c], (self.dx[i], self.dy[j]), (dx[i], dy[j])))
            for (i, j), frame in self.block_frames.items():
                out.append(move(frame, (self.dx[i], self.dy[j]), (dx[i], dy[j])))
                out += [move(n, (self.dx[i], self.dy[j]), (dx[i], dy[j])) for n in self.extras.get((i, j), [])]
                out.append(_FadeTo(frame, 1.0 if act == "apart" else 0.0, 0.3))
            for i, lab in enumerate(self.top_labels):
                out.append(move(lab, (self.dx[i], self.dy[0]), (dx[i], dy[0])))
            for j, lab in enumerate(self.left_labels):
                out.append(move(lab, (self.dx[0], self.dy[j]), (dx[0], dy[j])))
            # 分开时整块的外框和切线收起，拼回时再出来
            on = 1.0 if act == "join" else 0.0
            out += [_FadeTo(n, on, 0.3) for n in [self.outline, *self.cut_lines]]
            self.dx, self.dy = dx, dy
            return out
        if act == "fill":
            color = t.color(args.get("role"), t.A)
            if "cells" in args:
                start, stop = parse_slice(args["cells"])
                return [Restyle(self.fills[k], 0.5, fill=color, opacity=0.45) for k in range(start, stop)]
            ii = [int(args["col"])] if "col" in args else range(len(self.dx))
            jj = [int(args["row"])] if "row" in args else range(len(self.dy))
            label = args.get("label")
            out = []
            for i in ii:
                for j in jj:
                    cx, cy, w, h = self.block_box(i, j)
                    p = (cx + self.dx[i], cy + self.dy[j])
                    pad = PathNode(path=rect(w, h).transformed(T.translate(*p)), style=Style(fill=color, opacity=0.0))
                    self.block_fill.add(pad)
                    self.extras.setdefault((i, j), []).append(pad)
                    out.append(_FadeTo(pad, 0.45, 0.5))
                    if not label:
                        continue
                    tex = label if isinstance(label, str) else str(round(w / u) * round(h / u))
                    lab = self.label(tex, p)
                    if lab.width > w - 0.3:
                        lab.scale_to_fit_width(max(0.2, w - 0.3))
                    # 不透明的底：格子线不从数字上穿过
                    chip = PathNode(path=rounded_rect(lab.width + 0.24, lab.height + 0.16, 0.08).transformed(T.translate(*p)),
                                    style=Style(fill=t.BG, opacity=0.0))
                    self.areas.add(chip, lab)
                    self.extras[i, j] += [chip, lab]
                    out += [_FadeTo(chip, 1.0), _FadeTo(lab, 1.0)]
            return out
        if act == "unfill":
            out = [Restyle(f, 0.4, opacity=0.0) for f in self.fills]
            for labs in self.extras.values():
                out += [_FadeTo(lab, 0.0) for lab in labs]
            self.extras = {}
            return out
        raise KeyError(f"组件 {self.name} 的 rect 模式没有动作 {act}")
