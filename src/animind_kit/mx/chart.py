"""折线图 line_chart（墨线实现）：横轴是离散的几项，几条折线各走各的，一条条露出来。

竞赛题里"商 b_n 和最大质因子 p_n 怎么升降"这类对比用它：横轴标每一项（LaTeX），纵轴按 ymax 等比例画，
不写坐标系的名字也行。画面要安静：折线直接淡入，不画出来、不闪。

参数：
- x：横轴标注列表（数或 LaTeX 字符串）；
- series：[{name, values, role?}]，values 和 x 一样长；name 是线末端的标注（LaTeX）；
- ymax：纵轴最大值（默认取所有值的最大值再加 1）；
- yticks：横向参考线的值（可选）；
- x_label / y_label：轴名（可选）；
- width / height：图的宽高（画面单位，默认 5.0 × 3.4）。

暴露的子元素：
- axes[0]：坐标轴、刻度和参考线；
- series[k]：第 k 条折线（线 + 点 + 名字），show chart.series[k] 露出它。

动作：
- ring {series, index}：在第 series 条线的第 index 个点外画一个圈（结论色），强调两条线在这里相遇之类。
"""

from __future__ import annotations

import numpy as np
from moxian.anim import Animation, Draw
from moxian.core import Group, Node, PathNode, Style
from moxian.path import Path, circle, line

from .text import mmath, rich
from .widgets import Built, Widget


class LineChart(Widget):
    name = "line_chart"
    W, H = 5.0, 3.4
    LABEL = 26

    def build(self) -> Built:
        t = self.theme
        d = self.definition
        self.W = float(d.get("width", self.W))
        self.H = float(d.get("height", self.H))
        xs = [str(v) for v in d["x"]]
        series = d["series"]
        values = [[float(v) for v in s["values"]] for s in series]
        ymax = float(d.get("ymax", max(v for vs in values for v in vs) + 1))
        self.dx = self.W / max(1, len(xs) - 1 + 0.6)
        self.dy = self.H / ymax
        node = Group(name=self.obj_id)
        axes = Group(name="axes")
        frame = Path().move_to(0, self.H + 0.25).line_to(0, 0).line_to(self.W + 0.3, 0)
        axes.add(PathNode(path=frame, style=Style(stroke=t.DIM, stroke_width=2.5)))
        for i, label in enumerate(xs):
            x = self.pt(i, 0)[0]
            axes.add(PathNode(path=line(x, 0, x, -0.1), style=Style(stroke=t.DIM, stroke_width=2)))
            lab = mmath(label, self.LABEL, t.FG)
            lab.move_to([x, -0.2 - lab.height / 2])
            axes.add(lab)
        right = self.pt(len(xs) - 1, 0)[0] + 0.1  # 参考线到最后一项为止，不伸到线末端名字的下面
        for v in d.get("yticks", []):
            y = float(v) * self.dy
            axes.add(PathNode(path=line(-0.08, y, right, y), style=Style(stroke=t.CARD, stroke_width=1.5)))
            lab = mmath(str(v), self.LABEL, t.FG)
            lab.move_to([-0.2 - lab.width / 2, y])
            axes.add(lab)
        if d.get("x_label"):
            lab = rich(str(d["x_label"]), t, size=self.LABEL, color=t.DIM).node
            lab.move_to([self.W + 0.3 - lab.width / 2, 0.25 + lab.height / 2])
            axes.add(lab)
        if d.get("y_label"):
            lab = rich(str(d["y_label"]), t, size=self.LABEL, color=t.DIM).node
            lab.move_to([0.15 + lab.width / 2, self.H + 0.25 - lab.height / 2])
            axes.add(lab)
        node.add(axes)
        self.points: list[list[tuple[float, float]]] = []
        lines: list[Node] = []
        placed: list[Node] = []  # 已放好的名字，后来的名字避开它们
        for s, vs in zip(series, values):
            color = t.color(s.get("role"), t.A)
            pts = [self.pt(i, v) for i, v in enumerate(vs)]
            self.points.append(pts)
            path = Path().move_to(*pts[0])
            for p in pts[1:]:
                path.line_to(*p)
            g = Group(PathNode(path=path, style=Style(stroke=color, stroke_width=4)), name=str(s.get("name", "")))
            for p in pts:
                dot = PathNode(path=circle(0.07), style=Style(fill=color))
                dot.move_to(list(p))
                g.add(dot)
            if s.get("name"):
                # 名字放在线末端的右边、和末点同高（线在这里结束，不会穿过字）；几条线末端挨在一起时，
                # 后来的名字往下错开一行
                lab = mmath(str(s["name"]), 30, color)
                end = pts[-1]
                lab.move_to([end[0] + 0.4 + lab.width / 2, end[1]])  # 留出 ring 的圈
                for _ in range(len(placed)):
                    if any(lab.bbox().overlaps(o.bbox(), tolerance=-0.05) for o in placed):
                        lab.shift([0.0, -(lab.height + 0.12)])
                placed.append(lab)
                g.add(lab)
            g.set_style(opacity=0.0)
            node.add(g)
            lines.append(g)
        self.rings = Group(name="rings")
        node.add(self.rings)
        return Built(node, parts={"axes": [axes], "series": lines})

    def pt(self, i: float, v: float) -> tuple[float, float]:
        return (i * self.dx + 0.35, v * self.dy)

    def action(self, act: str, args: dict) -> list[Animation]:
        if act != "ring":
            raise KeyError(act)
        p = self.points[int(args["series"])][int(args["index"])]
        ring = PathNode(path=circle(0.2), style=Style(stroke=self.theme.HL_INK, stroke_width=3))
        ring.move_to(list(np.asarray(p)))
        self.rings.add(ring)
        return [Draw(ring, 0.5)]
