"""格子正方形（墨线实现）：分数乘法的"一部分的一部分"。

一个代表 1 的正方形，竖着分 cols 列、横着分 rows 行（cols × rows 格）。动作：

- cut：画出切线，axis: cols（竖线）或 rows（横线），线从一端长到另一端；
- shade：涂色，cols: n 从左涂 n 列，或 rows: n 从上涂 n 行；role 定颜色（默认 A）；列和行各一层，叠在一起时两层都看得见；
- overlap：既在涂过的列里、又在涂过的行里的格子换成 HL，依次标上 1、2、3…（"交叠的格子有几个"）。

暴露的子元素：cols / rows（两组切线，各一项）、overlap（交叠格和它们的编号，一项）、cell（全部格子，按行从左往右）。
"""

from __future__ import annotations

from moxian.anim import Animation, Draw, Fade, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import line, rect

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
