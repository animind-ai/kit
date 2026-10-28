"""组件的墨线实现：题卡、步骤胶囊、想一想框、总结卡、差分表。

接口和 Manim 版一致：build() 出节点并暴露 spans / parts / items，action() 返回动画列表，
focus() 可选地给出组件自己的强调方式。状态直接留在节点上（墨线的对象树跨 beat 常驻）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from moxian.anim import Animation, Grow, Restyle, fade_in, fade_out, together
from moxian.core import DOWN, LEFT, RIGHT, UP, Group, Node, PathNode, Style, arrange
from moxian.path import line, rect, rounded_rect
from scene_spec.selectors import parse_slice

from ..theme import Theme
from .text import mmath, mtext, rich


@dataclass
class Built:
    node: Node
    spans: dict[str, Node] = field(default_factory=dict)
    parts: dict[str, list[Node]] = field(default_factory=dict)
    items: list[Node] = field(default_factory=list)


class Widget:
    name = "widget"

    def __init__(self, obj_id: str, definition: dict, theme: Theme, data: dict, params: dict | None = None,
                 opts: dict | None = None):
        self.obj_id, self.definition, self.theme, self.data = obj_id, definition, theme, data
        self.params = params or {}  # 场景参数：名字 → moxian.anim.Param（组件属性里的表达式按它们的当前值求值）
        self.opts = opts or {}  # 后端给的排版选项（不来自场景文件）：min_size 字号下限、label_size 数轴标注字号
        self.built = self.build()

    def build(self) -> Built:
        raise NotImplementedError

    @property
    def node(self) -> Node:
        return self.built.node

    def action(self, act: str, args: dict) -> list[Animation]:
        raise KeyError(f"组件 {self.name} 没有动作 {act}")

    def focus(self, index: int | None) -> list[Animation] | None:
        return None

    def restyle(self, sel, args: dict) -> list[Animation] | None:
        """内置的 set {role, opacity} 指向组件的一部分时，组件可以自己处理（样式在子节点上、改组的 fill 不起作用时）；
        返回 None 时后端按默认做法改目标节点的样式。"""
        return None


# ---------- 题卡 ----------

class ProblemCard(Widget):
    name = "problem_card"
    ANSWER_PAD = 1.9  # 有答案时卡片右侧留白（画面单位，按 30px 字号排版时）

    def build(self) -> Built:
        t = self.theme
        lines, spans = [], {}
        for text in self.definition["lines"]:
            r = rich(text, t, size=30)
            lines.append(r.node)
            spans.update(r.spans)
        arrange(lines, DOWN, gap=0.2, align=LEFT)
        body = Group(*lines)
        answers = self.definition.get("answers")
        pad = self.ANSWER_PAD if answers else 0.0
        w, h = body.width + 0.6 + pad, body.height + 0.42
        bg = PathNode(path=rounded_rect(w, h, 0.14), style=Style(fill=t.CARD))
        bar = PathNode(path=rect(0.08, h - 0.24), style=Style(fill=t.color(self.definition.get("role"), t.HL_INK)))
        bg.move_to(body.center() + [pad / 2, 0.0])
        bar.move_to([bg.edge(LEFT)[0] + 0.14, bg.center()[1]])
        card = Group(bg, bar, body)
        parts: dict[str, list[Node]] = {}
        if answers:
            # 答案写在右侧留白里，和对应那一行对齐；一开始隐藏，show card.answer[i] 时出现
            slots: list[Node] = []
            x = bg.edge(RIGHT)[0] - pad / 2 - 0.05
            for line, tex in zip(lines, answers):
                if tex is None:
                    slots.append(Group())
                    continue
                tag = rich(tex, t, size=30, color=t.HL_INK).node
                if tag.width > pad - 0.2:
                    tag.scale_to_fit_width(pad - 0.2)
                tag.move_to([x, line.center()[1]])
                tag.set_style(opacity=0.0)
                card.add(tag)
                slots.append(tag)
            parts["answer"] = slots
        return Built(card, spans=spans, parts=parts, items=lines)


# ---------- 对齐的等式组 ----------

class Equations(Widget):
    """多行等式按对齐点（& 的位置，没有 & 时按第一个 =）对齐，每行一个 item，可以逐行 show。"""

    name = "equations"
    SIZE = 40
    GAP = 0.32

    def build(self) -> Built:
        t = self.theme
        rows: list[Group] = []
        for row in self.definition["rows"]:
            tex, role = (row, None) if isinstance(row, str) else (row["tex"], row.get("role"))
            color = t.ink(role) if role else t.FG
            if "&" in tex:
                left_tex, right_tex = tex.split("&", 1)
            elif "=" in tex:
                left_tex, right_tex = tex.split("=", 1)
                right_tex = "=" + right_tex
            else:
                left_tex, right_tex = "", tex
            left = mmath(r"\displaystyle " + left_tex, self.SIZE, color) if left_tex.strip() else Group()
            right = mmath(r"\displaystyle {}" + right_tex, self.SIZE, color)
            rows.append(Group(left, right))
        # 右半边的左端排在同一条竖线上；左半边右对齐到这条线
        x = 0.0
        y = 0.0
        for row in rows:
            left, right = row.children
            right.shift([x + 0.08 - right.bbox().x0, 0.0])
            if left.children:
                left.shift([x - 0.08 - left.bbox().x1, 0.0])
                # 公式按各自包围盒排，竖直方向以右半边为准
                left.shift([0.0, right.center()[1] - left.center()[1]])
            row.shift([0.0, y - row.bbox().y1])
            y = row.bbox().y0 - self.GAP
        return Built(Group(*rows), items=rows)


# ---------- 数列格子 ----------

class SeqCells(Widget):
    """一排带标签的格子：b_1 b_2 b_3 …；前 known 个显示数值，其余显示问号，fill 时换成数值并垫色。"""

    name = "seq_cells"
    GAP = 0.9

    def build(self) -> Built:
        t = self.theme
        values, labels = self.definition["values"], self.definition["labels"]
        known = self.definition.get("known", len(values))
        columns, self.values, self.unknown, self.pads = [], [], [], []
        for i, (v, lab) in enumerate(zip(values, labels)):
            label = mmath(str(lab), 30, t.DIM)
            value = mmath(str(v), 46, t.A if i < known else t.HL_INK)
            col = Group(label, value)
            value.next_to(label, DOWN, 0.12)
            value.shift([label.center()[0] - value.center()[0], 0.0])
            q = mmath("?", 46, t.HL_INK)
            q.move_to(value.center())
            pad = PathNode(path=rounded_rect(max(value.width, 0.3) + 0.3, value.height + 0.2, 0.1),
                           style=Style(fill=t.MARK, opacity=0.0), z=-1)
            pad.move_to(value.center())
            col.add(pad)
            if i >= known:
                value.set_style(opacity=0.0)
                col.add(q)
            else:
                q.set_style(opacity=0.0)
            columns.append(col)
            self.values.append(value)
            self.unknown.append(q)
            self.pads.append(pad)
        arrange(columns, RIGHT, gap=self.GAP)
        node = Group(*columns)
        if self.definition.get("dots"):
            dots = mmath(r"\cdots", 46, t.DIM)
            dots.next_to(columns[-1], RIGHT, 0.5)
            dots.shift([0.0, self.values[-1].center()[1] - dots.center()[1]])
            node.add(dots)
        return Built(node, parts={"value": self.values}, items=columns)

    def action(self, act: str, args: dict) -> list[Animation]:
        if act != "fill":
            raise KeyError(act)
        start, stop = parse_slice(args["index"])
        anims = []
        for i in range(start, stop):
            anims.append(together(fade_out(self.unknown[i], 0.3), fade_in(self.pads[i], 0.4), Grow(self.values[i], 0.5)))
        return anims


# ---------- 步骤胶囊 ----------

class Chips(Widget):
    """步骤胶囊。reveal: true 时胶囊先不露出来，focus 到第几个就露到第几个（讲一个、标一个，
    不提前把整条解题路线摆给学生看）；`reveal` 动作可以一次露出若干个（例如回放完整证明时全部亮出）。"""

    name = "chips"

    def build(self) -> Built:
        t = self.theme
        self.ink = t.color(self.definition.get("role"), t.A)
        self.reveal_mode = bool(self.definition.get("reveal", False))
        self.bgs: list[PathNode] = []
        self.labels: list[Node] = []
        self.shown: list[bool] = []
        chips = []
        for text in self.definition["items"]:
            label = mtext(text, 22, self.ink)
            h = max(0.5, label.height + 0.28)
            bg = PathNode(path=rounded_rect(label.width + 0.45, h, h / 2), style=Style(fill=t.CARD))
            bg.move_to(label.center())
            chip = Group(bg, label)
            if self.reveal_mode:
                chip.set_style(opacity=0.0)
            chips.append(chip)
            self.bgs.append(bg)
            self.labels.append(label)
            self.shown.append(not self.reveal_mode)
        arrange(chips, RIGHT, gap=0.3)
        return Built(Group(*chips), items=chips)

    def _reveal(self, start: int, stop: int) -> list[Animation]:
        """露出下标在 [start, stop) 里还没露出的胶囊。"""
        anims = []
        for i in range(max(0, start), min(stop, len(self.shown))):
            if not self.shown[i]:
                self.shown[i] = True
                anims.append(fade_in(self.built.items[i], 0.4, shift=(0.0, -0.08)))
        return anims

    def focus(self, index: int | None) -> list[Animation]:
        anims = self._reveal(0, index + 1) if (self.reveal_mode and index is not None) else []
        for i, (bg, label) in enumerate(zip(self.bgs, self.labels)):
            on = i == index
            anims.append(Restyle(bg, 0.4, fill=self.ink if on else self.theme.CARD))
            anims.append(Restyle(label, 0.4, fill=self.theme.BG if on else self.ink))
        return anims

    def action(self, act: str, args: dict) -> list[Animation]:
        if act != "reveal":
            raise KeyError(act)
        start, stop = parse_slice(args.get("index", f"0:{len(self.shown)}"))
        return self._reveal(start, stop)


# ---------- 想一想框 ----------

class ThinkBox(Widget):
    name = "think_box"

    SIZE = 30  # 「练一练」框常放在窄的侧栏里，字号下限见 lint.FLOORS_720["practice"]

    def build(self) -> Built:
        t = self.theme
        lines, spans = [], {}
        size = max(self.SIZE, float(self.opts.get("min_size", 0.0)))
        for i, text in enumerate(self.definition["lines"]):
            r = rich(text, t, size=size, color=t.HL_INK if i == 0 and text.startswith("[") else t.FG)
            lines.append(r.node)
            spans.update(r.spans)
        arrange(lines, DOWN, gap=0.16, align=LEFT)
        body = Group(*lines)
        if body.width > 11.8:
            body.scale_to_fit_width(11.8)
        box = PathNode(path=rounded_rect(body.width + 0.6, body.height + 0.4, 0.14),
                       style=Style(fill=t.MARK, fill_opacity=0.6, stroke=t.HL_INK, stroke_width=2))
        box.move_to(body.center())
        node = Group(box, body)
        node.font_floor = "practice"
        return Built(node, spans=spans, items=lines)


# ---------- 总结卡 ----------

class Summary(Widget):
    name = "summary"

    def build(self) -> Built:
        t = self.theme
        rows = []
        for row in self.definition["rows"]:
            color = t.color(row.get("role"), t.A)
            tag = mtext(row["tag"], 22, t.BG)
            h = max(0.5, tag.height + 0.28)
            badge_bg = PathNode(path=rounded_rect(max(1.5, tag.width + 0.45), h, h / 2), style=Style(fill=color))
            badge_bg.move_to(tag.center())
            badge = Group(badge_bg, tag)
            body = rich(row["text"], t, size=28).node
            body.next_to(badge, RIGHT, 0.3)
            body.shift([0.0, badge.center()[1] - body.center()[1]])
            rows.append(Group(badge, body))
        arrange(rows, DOWN, gap=0.36, align=LEFT)
        return Built(Group(*rows), items=rows)


# ---------- 差分表 ----------

COL, ROW = 1.36, 1.17


class DiffTable(Widget):
    name = "diff_table"

    def _sz(self, size: float) -> float:
        """字号；后端给了 opts["min_text"]（v2 的全局下限换成 kit 字号）时不小于它。"""
        return max(size, float(self.opts.get("min_text", 0.0)))

    def smallest_text(self) -> float:
        return self._sz(17)

    def build(self) -> Built:
        t = self.theme
        rows = self.definition["rows"]
        self.names = [r["data"] for r in rows]
        self.values = [list(self.data[r["data"]]) for r in rows]
        self.colors = [t.color(r["role"]) for r in rows]
        self.shown = list(self.definition.get("known") or [len(v) for v in self.values])
        n = len(self.values[0])
        x0 = -(n - 1) / 2 * COL
        y0 = (len(rows) - 1) / 2 * ROW
        table = Group()
        self.cells: list[list[Node]] = []
        for r, values in enumerate(self.values):
            row = []
            for i, v in enumerate(values):
                m = mmath(str(v), self._sz(42), self.colors[r]).move_to([x0 + i * COL + r * COL / 2, y0 - r * ROW])
                if i >= self.shown[r]:
                    m.set_style(opacity=0.0)
                row.append(m)
            self.cells.append(row)
            label = Group(mmath(rows[r].get("label", f"${rows[r]['data']}$"), self._sz(34), self.colors[r]),
                          mtext(rows[r].get("name", ""), self._sz(17), self.colors[r]))
            arrange(list(label), DOWN, gap=0.06)
            label.move_to([x0 - 1.25, y0 - r * ROW])
            table.add(label)
        # 每行每个数都有垫色（第一行往后补数时也要垫）；连线只有第二行起才有
        self.links: list[list[Group]] = []
        self.pads: list[list[PathNode]] = []
        for r in range(len(self.values)):
            row_links, row_pads = [], []
            for i, child in enumerate(self.cells[r]):
                shown = i < self.shown[r]
                if r >= 1:
                    pair = Group()
                    if i + 1 < len(self.cells[r - 1]):
                        top = child.center() + UP * 0.28
                        for src, dx in ((self.cells[r - 1][i], 0.08), (self.cells[r - 1][i + 1], -0.08)):
                            s = src.center() + DOWN * 0.28 + RIGHT * dx
                            pair.add(PathNode(path=line(s[0], s[1], top[0], top[1])))
                    pair.style = Style(stroke=t.DIM, stroke_width=2, stroke_opacity=0.55, opacity=1.0 if shown else 0.0)
                    row_links.append(pair)
                    table.add(pair)
                pad = PathNode(path=rounded_rect(max(child.width, 0.3) + 0.3, child.height + 0.2, 0.1),
                               style=Style(fill=t.MARK, opacity=0.0), z=-1)  # 已知的数不垫色，推出来的才垫
                pad.move_to(child.center())
                row_pads.append(pad)
                table.add(pad)
            if r >= 1:
                self.links.append(row_links)
            self.pads.append(row_pads)
        for row in self.cells:
            table.add(*row)
        self.extra = Group()
        table.add(self.extra)
        return Built(table, parts={name: cells for name, cells in zip(self.names, self.cells)})

    def action(self, act: str, args: dict) -> list[Animation]:
        r = self.names.index(args["row"])
        start, stop = parse_slice(args["index"])
        if act in ("derive", "extend"):
            anims = []
            for i in range(start, stop):
                cell = self.cells[r][i]
                pieces = [fade_in(self.pads[r][i], 0.4), Grow(cell, 0.5) if act == "derive" else fade_in(cell, 0.4, shift=(-0.2, 0.0))]
                if r >= 1 and i < len(self.links[r - 1]):
                    pieces.append(fade_in(self.links[r - 1][i], 0.4))
                anims.append(together(*pieces))
                self.shown[r] = max(self.shown[r], i + 1)
            return anims
        if act == "triangle":
            i = start
            pts = [self.cells[r][i - 1], self.cells[r + 1][i - 1], self.cells[r][i]]
            from moxian.path import polygon

            tri = PathNode(path=polygon(enclosing_triangle(pts, margin=0.08)),
                           style=Style(stroke=self.theme.HL_INK, stroke_width=3, fill=self.theme.HL, fill_opacity=0.12, opacity=0.0))
            self.extra.children.clear()
            self.extra.add(tri)
            return [fade_in(tri, 0.4)]
        raise KeyError(act)


def enclosing_triangle(cells: list[Node], margin: float) -> list[tuple[float, float]]:
    """连起三个格子的三角形，三条边各自往外平移到让两端的数整个落在里面（不从字上穿过）。"""
    centers = [c.center() for c in cells]
    halves = [np.array([c.width, c.height]) / 2 for c in cells]
    g = np.mean(centers, axis=0)
    lines = []
    for i in range(3):
        a, b = centers[i], centers[(i + 1) % 3]
        n = np.array([b[1] - a[1], a[0] - b[0]])
        n /= np.linalg.norm(n)
        if np.dot(n, a - g) < 0:
            n = -n
        reach = max(float(np.dot(np.abs(n), halves[k])) for k in (i, (i + 1) % 3)) + margin
        lines.append((n, float(np.dot(n, a)) + reach))
    out = []
    for i in range(3):  # 第 i 个顶点 = 第 i-1 条边和第 i 条边的交点
        (n1, c1), (n2, c2) = lines[i - 1], lines[i]
        out.append(tuple(float(v) for v in np.linalg.solve(np.array([n1, n2]), np.array([c1, c2]))))
    return out


REGISTRY: dict[str, type[Widget]] = {
    "problem_card": ProblemCard,
    "equations": Equations,
    "seq_cells": SeqCells,
    "chips": Chips,
    "think_box": ThinkBox,
    "summary": Summary,
    "diff_table": DiffTable,
    "area_model": __import__("animind_kit.mx.area", fromlist=["AreaModel"]).AreaModel,  # 面积模型在 area.py（勾股定理拼图）
    "graph": __import__("animind_kit.mx.graph", fromlist=["Graph"]).Graph,  # 函数图像在 graph.py（割线、切线、放大）
    "number_line": __import__("animind_kit.mx.numberline", fromlist=["NumberLine"]).NumberLine,  # 数轴在 numberline.py（分数除法的跳跃）
    "dissect": __import__("animind_kit.mx.dissect", fromlist=["Dissect"]).Dissect,  # 剖分在 dissect.py（剪开、挪动、拼成新图形）
    "area_grid": __import__("animind_kit.mx.grid", fromlist=["AreaGrid"]).AreaGrid,  # 格子正方形在 grid.py（分数乘法的交叠格；rect 模式切长方形）
    "ten_frame": __import__("animind_kit.mx.dots", fromlist=["TenFrame"]).TenFrame,  # 十格框在 dots.py（凑十）
    "dot_array": __import__("animind_kit.mx.dots", fromlist=["DotArray"]).DotArray,  # 点阵在 dots.py（转 90°、补满、淡出一列）
    "block_bars": __import__("animind_kit.mx.bars", fromlist=["BlockBars"]).BlockBars,  # 积木条形图在 bars.py（平均数削峰填谷）
    "balance": __import__("animind_kit.mx.balance", fromlist=["Balance"]).Balance,  # 天平在 balance.py（解方程两边同时拿走）
    "wallet": __import__("animind_kit.mx.wallet", fromlist=["Wallet"]).Wallet,  # 钱包和余额条在 wallet.py（撕欠条：负负得正、减去负数）
    "plane": __import__("animind_kit.mx.plane", fromlist=["Plane"]).Plane,  # 坐标平面在 plane.py（向量、乘 i 转 90°、矩阵变形）
    "geometry": __import__("animind_kit.mx.geometry", fromlist=["Geometry"]).Geometry,  # 综合几何图在 geometry.py（构造求点、角标、断言验算）
    "solid": __import__("animind_kit.mx.solid", fromlist=["Solid"]).Solid,  # 立体在 solid.py（轴测图、剖成三个四棱锥、截面）
    "space": __import__("animind_kit.mx.space", fromlist=["Space"]).Space,  # 三维坐标空间在 space.py（向量、平面、3×3 变形、投影）
    "coin_flips": __import__("animind_kit.mx.chance", fromlist=["CoinFlips"]).CoinFlips,  # 随机试验在 chance.py（频率折线）
    "galton": __import__("animind_kit.mx.chance", fromlist=["Galton"]).Galton,  # （高尔顿板）
    "outcomes": __import__("animind_kit.mx.chance", fromlist=["Outcomes"]).Outcomes,  # （样本空间方格，条件概率）
    "seq_table": __import__("animind_kit.mx.table", fromlist=["SeqTable"]).SeqTable,  # 数例表在 table.py（竞赛题"先试几步"，一列列露）
    "line_chart": __import__("animind_kit.mx.chart", fromlist=["LineChart"]).LineChart,  # 折线图在 chart.py（几个量随项数的升降）
}
REGISTRY.update(__import__("animind_kit.mx.cs", fromlist=["REGISTRY"]).REGISTRY)  # 编程课组件在 cs.py（代码面板、终端、调用栈……）


def lookup(name: str) -> type[Widget] | None:
    return REGISTRY.get(name.split("@", 1)[0])
