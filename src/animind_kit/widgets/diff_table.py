"""差分表：若干层数列错位排成三角，支持 derive（做差得到新数）、extend（往后补）、triangle（高亮小三角）。

数据来自 data 段；known 给出一开始每行显示几个数；已推出的数在跨 beat 的状态里记着。
"""

from __future__ import annotations

import numpy as np
from manim import DOWN, LEFT, RIGHT, UP, Animation, FadeIn, GrowFromCenter, Line, Polygon, RoundedRectangle, VGroup, VectorizedPoint

from scene_spec.selectors import parse_slice

from ..text import math, mixed, plain
from .base import Built, Widget

COL = 1.36
ROW = 1.17


class DiffTable(Widget):
    name = "diff_table"

    def build(self) -> Built:
        t = self.theme
        rows = self.definition["rows"]
        self.names = [r["data"] for r in rows]
        self.values = [list(self.data[r["data"]]) for r in rows]
        self.colors = [t.color(r["role"]) for r in rows]
        n = len(self.values[0])
        xs = [-(n - 1) / 2 * COL + COL * i for i in range(n)]
        y0 = (len(rows) - 1) / 2 * ROW
        self.cells: list[list] = []
        self.links: list[list] = []
        all_mobs = VGroup()
        for r, values in enumerate(self.values):
            y = y0 - r * ROW
            offset = r * COL / 2
            cells = []
            for i, v in enumerate(values):
                m = math(str(v), 42, self.colors[r]).move_to([xs[i] + offset, y, 0])
                cells.append(m)
                all_mobs.add(m)
            self.cells.append(cells)
        for r in range(1, len(self.values)):
            links = []
            for i, child in enumerate(self.cells[r]):
                if i + 1 >= len(self.cells[r - 1]):
                    links.append(VGroup())
                    continue
                left, right = self.cells[r - 1][i], self.cells[r - 1][i + 1]
                top = child.get_center() + UP * 0.28
                pair = VGroup(
                    Line(left.get_center() + DOWN * 0.28 + RIGHT * 0.08, top, color=t.DIM, stroke_width=2),
                    Line(right.get_center() + DOWN * 0.28 + LEFT * 0.08, top, color=t.DIM, stroke_width=2),
                ).set_opacity(0.55)
                links.append(pair)
                all_mobs.add(pair)
            self.links.append(links)
        labels = VGroup()
        for r, row in enumerate(rows):
            label = VGroup(
                mixed(row.get("label", f"${row['data']}$"), size=34, color=self.colors[r]),
                plain(row.get("name", ""), 17, self.colors[r]),
            ).arrange(DOWN, buff=0.06)
            label.move_to([xs[0] - 1.25, y0 - r * ROW, 0])
            labels.add(label)
            all_mobs.add(label)
        self.labels = labels
        self.marks: dict[tuple[int, int], RoundedRectangle] = {}
        self.shown = list(self.definition.get("known") or [len(v) for v in self.values])
        self.extra = VGroup()  # 三角形等临时标记
        self._apply_visibility()
        mob = VGroup(all_mobs, self.extra)
        return Built(mob, parts={name: cells for name, cells in zip(self.names, self.cells)})

    # ---- 可见性 ----
    def _apply_visibility(self) -> None:
        for r, cells in enumerate(self.cells):
            for i, cell in enumerate(cells):
                cell.set_opacity(1 if i < self.shown[r] else 0)
                if r >= 1:
                    self.links[r - 1][i].set_opacity(0.55 if i < self.shown[r] else 0)

    def state(self) -> dict:
        return {"shown": list(self.shown), "marks": sorted(self.marks)}

    def restore(self, state: dict) -> None:
        self.shown = list(state.get("shown", self.shown))
        self._apply_visibility()
        for r, i in state.get("marks", []):
            self._mark(r, i)

    def _mark(self, r: int, i: int) -> RoundedRectangle:
        cell = self.cells[r][i]
        rect = RoundedRectangle(
            width=max(cell.width, 0.3) + 0.3, height=cell.height + 0.2, corner_radius=0.1,
            fill_color=self.theme.MARK, fill_opacity=1, stroke_width=0,
        ).move_to(cell).set_z_index(-1)
        self.marks[(r, i)] = rect
        self.mob[0].add(rect)
        return rect

    # ---- 动作 ----
    def action(self, act: str, args: dict) -> tuple[list[Animation], list]:
        row = self.names.index(args["row"])
        start, stop = parse_slice(args["index"])
        if act == "derive":
            anims, new = [], []
            for i in range(start, stop):
                rect = self._mark(row, i)
                cell = self.cells[row][i]
                cell.set_opacity(1)
                link = self.links[row - 1][i]
                link.set_opacity(0.55)
                anims += [FadeIn(link), FadeIn(rect), GrowFromCenter(cell)]
                new += [link, rect, cell]
                self.shown[row] = max(self.shown[row], i + 1)
            return anims, new
        if act == "extend":
            anims, new = [], []
            for i in range(start, stop):
                rect = self._mark(row, i)
                cell = self.cells[row][i]
                cell.set_opacity(1)
                anims += [FadeIn(rect), FadeIn(cell, shift=RIGHT * 0.2)]
                new += [rect, cell]
                if row >= 1 and i < len(self.links[row - 1]):
                    link = self.links[row - 1][i]
                    link.set_opacity(0.55)
                    anims.append(FadeIn(link))
                    new.append(link)
                self.shown[row] = max(self.shown[row], i + 1)
            return anims, new
        if act == "triangle":
            # 第 row 行第 i 个数 = 同行前一个 + 下一行前一个：左边加下边等于右边
            i = start
            if row + 1 >= len(self.cells) or i == 0:
                raise ValueError(f"triangle 需要 row 下面还有一行，且 index ≥ 1（给的是 {args}）")
            pts = [self.cells[row][i - 1], self.cells[row + 1][i - 1], self.cells[row][i]]
            tri = Polygon(*[m.get_center() for m in pts], color=self.theme.HL_INK, stroke_width=3,
                          fill_color=self.theme.HL, fill_opacity=0.12)
            self.extra.submobjects.clear()
            self.extra.add(tri)
            return [FadeIn(tri)], [tri]
        raise KeyError(act)
