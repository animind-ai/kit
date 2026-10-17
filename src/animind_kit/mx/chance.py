"""随机试验（墨线实现）：抛硬币的频率折线、高尔顿板、样本空间方格。

随机数都来自 scene_spec.chance（random.Random(seed)），和检查器核 expect 用的是同一串。

coin_flips：横轴是抛的次数（对数刻度，1、10、100、1000 等距），纵轴是到这一次为止正面的频率，p 处一条虚线。
- flip {to}：折线从现在的位置画到第 to 次（按对数匀速），末端一个点跟着走；画完右上角换成"正面 h / to"；
- runs {count}：再画 count 条 seed+1、seed+2 … 的折线（淡色，从头画到 n）——每一串都在收窄；
- band：p ± 1/√k 的漏斗（淡色填充），k 越大越窄。
参数 n、seed、p（默认 0.5）、runs（预留几条，默认 0）。暴露 axes、line、runs、band。

galton：rows 层钉子摆成三角形，下面 rows+1 个格子。每个球在每层往左或往右（各一半），落进第"往右次数"个格子，
在格子里每排 PER_ROW 个往上堆。参数 rows、balls（最多几个，排版按期望最高的那格留高度）、seed。
- drop {count}：接着落 count 个球，一个接一个（时长内均匀发出，可以同时在空中）；
- path {ball, role}：把第 ball 个球走过的路描出来（这一路左右的选择）；
- pascal：每个格子底下写路径数 C(rows, k)（杨辉三角那一行）；
- expect：按二项分布画出现在这么多球时每格的期望高度（虚线台阶）；
- flat：画一条"每格一样多"的水平虚线（非例）；
- erase：收起描线、数字、虚线。暴露 board、balls、marks。

outcomes：rows × cols 的方格，每格写一个结果（labels，按行从上往下、每行从左往右），可选行列表头。
格子用编号（0 起，按读的顺序）或格子里的字指。shade {cells, role} 垫色、unshade {cells}；strike {cells}：这几格淡下去并打叉
（"已知"把它们排除出样本空间），restore；frame {cells, role}：把这几格的外轮廓描出来（剩下的样本空间，可以不是长方形），unframe。
暴露 cell（每格一项）、frame。

颜色、透明度、位置都在动画播放时才改：一个 beat 的动作先全部建好再播放，建动作时只推演状态。
"""

from __future__ import annotations

import math

import numpy as np
from moxian.anim import Animation, Draw, Restyle, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import Path, circle, polygon, rect
from scene_spec.chance import binomial, coin_flips, galton_paths

from .dissect import dashed
from .dots import FadeTo, reserve
from .text import mmath, rich
from .widgets import Built, Widget


class _Show(Draw):
    """边画边显出来：透明度在播放时才改。"""

    def begin(self) -> None:
        self.node.set_style(opacity=1.0)
        super().begin()


def polyline(points) -> Path:
    path = Path()
    for i, (x, y) in enumerate(points):
        (path.move_to if i == 0 else path.line_to)(float(x), float(y))
    return path


# ---------- 抛硬币：频率折线 ----------

class _Trace(Animation):
    """折线从第 k0 次画到第 k1 次（k 按对数匀速增长），末端的点跟着走。"""

    def __init__(self, chart: CoinFlips, k0: int, k1: int, duration: float):
        super().__init__(Node(), duration, easing.linear)
        self.chart, self.k0, self.k1 = chart, k0, k1

    def begin(self) -> None:
        super().begin()
        self.chart.line.set_style(opacity=1.0)
        self.chart.dot.set_style(opacity=1.0)

    def interpolate(self, alpha: float) -> None:
        a, b = math.log(max(1, self.k0)), math.log(self.k1)
        k = max(1, min(self.k1, int(round(math.exp(a + (b - a) * alpha)))))
        pts = self.chart.points[:k]
        self.chart.line.path = polyline(pts)
        self.chart.dot.matrix = T.translate(*pts[-1])


class CoinFlips(Widget):
    name = "coin_flips"
    W, H = 10.0, 3.4  # 图的宽、高（画面单位，排版前）；扁一点：主讲区高度紧
    LABEL = 34
    label_floor = "label"

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.acted = False
        self.label_size = float(self.opts.get("label_size", self.LABEL))
        self.n, self.seed, self.p = int(d["n"]), int(d["seed"]), float(d.get("p", 0.5))
        self.flips = coin_flips(self.seed, self.n, self.p)
        self.points = self.trace_points(self.flips)
        node = Group(name=self.obj_id)
        W, H = self.W, self.H
        axes = Group(name="axes")
        frame = Path().move_to(0, H + 0.2).line_to(0, 0).line_to(W + 0.25, 0)
        axes.add(PathNode(path=frame, style=Style(stroke=t.FG, stroke_width=3)))
        decades = int(round(math.log10(self.n)))
        num_h = mmath("8", self.label_size, t.FG).height
        for e in range(decades + 1):
            x = self.x(10 ** e)
            axes.add(PathNode(path=Path().move_to(x, 0).line_to(x, -0.12), style=Style(stroke=t.FG, stroke_width=3)))
            axes.add(self.label(str(10 ** e), t.DIM, (x, -0.2), (0.0, -1.0)))
        for v, tex in ((self.p, r"\dfrac{1}{2}" if self.p == 0.5 else f"{self.p:g}"), (1.0, "1")):  # 0 不写：和横轴的 1 挤在原点
            y = v * H
            axes.add(PathNode(path=Path().move_to(0, y).line_to(-0.12, y), style=Style(stroke=t.FG, stroke_width=3)))
            axes.add(self.label(tex, t.DIM, (-0.2, y), (-1.0, 0.0)))
        # 不写轴名（版面高度紧，字号下限又大）：横轴的 1、10、100、1000 和右上角的读数"正面 h/k"已经说清楚了
        self.top_y = H + 0.3
        target = PathNode(path=dashed([((0, self.p * H), (W, self.p * H))], 0.2, 0.12),
                          style=Style(stroke=t.HL_INK, stroke_width=3))
        self.band = PathNode(path=Path(), style=Style(fill=t.MARK, fill_opacity=0.8, opacity=0.0), name="band")
        self.runs = Group(name="runs")
        self.line = PathNode(path=Path(), style=Style(stroke=t.A, stroke_width=5, opacity=0.0), name="line")
        self.dot = PathNode(path=circle(0.09), style=Style(fill=t.A, opacity=0.0))
        self.readout = Group(name="readout")
        top = H + 0.3 + rich("正面", t, size=self.label_size, color=t.A).node.height + 0.1
        bottom = -0.3 - num_h
        left = -0.4 - mmath(r"\dfrac{1}{2}", self.label_size, t.FG).width
        right = W + 0.35
        node.add(reserve(right - left, top - bottom, (left + right) / 2, (top + bottom) / 2))
        node.add(self.band, axes, target, self.runs, self.line, self.dot, self.readout)
        self.at = 0
        self.run_count = 0
        return Built(node, parts={"axes": [axes], "line": [self.line], "runs": [self.runs], "band": [self.band]})

    def x(self, k: float) -> float:
        return math.log10(k) / math.log10(self.n) * self.W

    def trace_points(self, flips: list[int]) -> np.ndarray:
        heads = np.cumsum(flips)
        k = np.arange(1, len(flips) + 1)
        xs = np.log10(k) / math.log10(self.n) * self.W
        return np.stack([xs, heads / k * self.H], axis=1)

    def label(self, tex: str, color: str, at, toward) -> Node:
        lab = mmath(tex, self.label_size, color)
        lab.font_floor = self.label_floor
        self.place(lab, at, toward)
        return lab

    @staticmethod
    def place(node: Node, at, toward) -> None:
        b = node.bbox()
        half = np.array([b.width, b.height]) / 2
        node.shift(np.asarray(at, dtype=float) + np.asarray(toward, dtype=float) * half - b.center)

    def action(self, act: str, args: dict) -> list[Animation]:
        self.acted = True
        t = self.theme
        if act == "flip":
            k1 = int(args["to"])
            k0, self.at = self.at, k1
            h = sum(self.flips[:k1])
            out: list[Animation] = [FadeTo(old, 0.0, 0.3) for old in self.readout.children]
            text = rich(f"正面 ${h}/{k1}$", t, size=self.label_size, color=t.A).node
            text.font_floor = self.label_floor
            self.place(text, (self.W, self.top_y), (-1.0, 1.0))
            text.set_style(opacity=0.0)
            self.readout.add(text)
            # 时长由场景里的 dur 定（后端统一改写）；读数在折线画完时出现
            out += [_Trace(self, k0, k1, 2.0), _After(FadeTo(text, 1.0, 0.3), 2.0)]
            return out
        if act == "runs":
            out = []
            for _ in range(int(args["count"])):
                self.run_count += 1
                pts = self.trace_points(coin_flips(self.seed + self.run_count, self.n, self.p))
                line = PathNode(path=polyline(pts), style=Style(stroke=t.DIM, stroke_width=3, opacity=0.0))
                self.runs.add(line)
                out.append(_Show(line, 1.6))
            return out
        if act == "band":
            k = np.unique(np.round(np.logspace(0, math.log10(self.n), 120)).astype(int))
            xs = np.log10(k) / math.log10(self.n) * self.W
            half = 1 / np.sqrt(k)
            hi = np.minimum(1.0, self.p + half) * self.H
            lo = np.maximum(0.0, self.p - half) * self.H
            pts = list(zip(xs, hi)) + list(zip(xs[::-1], lo[::-1]))
            self.band.path = polygon([(float(a), float(b)) for a, b in pts])  # 看不见的节点，建动作时就能画好
            return [FadeTo(self.band, 1.0, 0.8)]
        raise KeyError(f"组件 {self.name} 没有动作 {act}")


class _After(Animation):
    """把一个短动画放到 total 秒的最后播。"""

    def __init__(self, anim: Animation, total: float):
        super().__init__(Node(), total, easing.linear)
        self.anim, self.start = anim, max(0.0, 1.0 - anim.duration / total)
        self.started = False

    def interpolate(self, alpha: float) -> None:
        if alpha < self.start:
            return
        if not self.started:
            self.anim.begin()
            self.started = True
        self.anim.interpolate(self.anim.ease(min(1.0, (alpha - self.start) / max(1e-6, 1.0 - self.start))))

    def finish(self) -> None:
        self.interpolate(1.0)


# ---------- 高尔顿板 ----------

PER_ROW = 5  # 格子里每排堆几个球


class _Drops(Animation):
    """一批球依次落下：第 i 个在 i·gap 秒发出，飞 flight 秒，落到格子里的位置。"""

    def __init__(self, board: Galton, balls: list[tuple[Node, list[np.ndarray]]], duration: float):
        super().__init__(Node(), duration, easing.linear)
        self.board, self.balls = board, balls

    def begin(self) -> None:
        super().begin()  # 时长由后端按场景里的 dur 改写过，到这时才定
        n = len(self.balls)
        self.flight = min(1.6, self.duration * 0.6) if n > 1 else self.duration
        self.gap = (self.duration - self.flight) / max(1, n - 1)

    def interpolate(self, alpha: float) -> None:
        now = alpha * self.duration
        for i, (ball, route) in enumerate(self.balls):
            t0 = i * self.gap
            if now < t0:
                continue
            s = min(1.0, (now - t0) / self.flight)
            ball.set_style(opacity=1.0)
            ball.matrix = T.translate(*along(route, s))

    def finish(self) -> None:
        self.interpolate(1.0)


def along(route: list[np.ndarray], s: float) -> np.ndarray:
    """沿折线 route 走到 s（0..1，按段数均分，每段里先慢后快像往下落）。"""
    segs = len(route) - 1
    f = s * segs
    i = min(segs - 1, int(f))
    u = f - i
    u = u * u if i < segs - 1 else easing.smooth(u)
    return route[i] + (route[i + 1] - route[i]) * u


class Galton(Widget):
    name = "galton"
    S, V = 1.1, 0.42  # 钉子的横向、纵向间距（画面单位，排版前）；扁宽：主讲区高度紧，格子底下的数字要放得下
    PEG = 0.07
    LABEL = 34
    label_floor = "label"

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.acted = False
        self.label_size = float(self.opts.get("label_size", self.LABEL))
        self.rows, self.max_balls, self.seed = int(d["rows"]), int(d["balls"]), int(d["seed"])
        self.paths = galton_paths(self.seed, self.max_balls, self.rows)
        R, S, V = self.rows, self.S, self.V
        self.D = S / PER_ROW  # 球的直径
        node = Group(name=self.obj_id)
        board = Group(name="board")
        self.top = 0.0
        pegs = Path()
        for r in range(R):
            for j in range(r + 1):
                x, y = (j - r / 2) * S, self.top - r * V
                pegs.extend(circle(self.PEG).transformed(T.translate(x, y)))
        board.add(PathNode(path=pegs, style=Style(fill=t.FG)))
        self.bin_top = self.top - R * V + 0.1
        expect_max = max(binomial(R)) / 2 ** R * self.max_balls
        stack_rows = math.ceil(expect_max * 1.15 / PER_ROW)
        self.floor = self.bin_top - stack_rows * self.D - 0.1
        walls = Path()
        for k in range(R + 2):
            x = (k - (R + 1) / 2) * S
            walls.move_to(x, self.bin_top).line_to(x, self.floor)
        half = (R + 1) / 2 * S
        walls.move_to(-half, self.floor).line_to(half, self.floor)
        board.add(PathNode(path=walls, style=Style(stroke=t.FG, stroke_width=3)))
        funnel = Path().move_to(-0.5, self.top + 0.7).line_to(-0.16, self.top + 0.36)
        funnel.move_to(0.5, self.top + 0.7).line_to(0.16, self.top + 0.36)
        board.add(PathNode(path=funnel, style=Style(stroke=t.FG, stroke_width=3)))
        self.balls_layer = Group(name="balls")
        self.marks = Group(name="marks")
        num_h = mmath("8", self.label_size, t.FG).height
        node.add(reserve(2 * half + 0.4, self.top + 0.8 - (self.floor - num_h - 0.3), 0.0,
                         (self.top + 0.8 + self.floor - num_h - 0.3) / 2))
        node.add(board, self.balls_layer, self.marks)
        self.counts = [0] * (R + 1)
        self.dropped = 0
        return Built(node, parts={"board": [board], "balls": [self.balls_layer], "marks": [self.marks]})

    def bin_x(self, k: int) -> float:
        return (k - self.rows / 2) * self.S

    def slot(self, k: int, m: int) -> np.ndarray:
        c, row = m % PER_ROW, m // PER_ROW
        return np.array([self.bin_x(k) + (c - (PER_ROW - 1) / 2) * self.D, self.floor + self.D / 2 + row * self.D])

    def route(self, path: list[int], end: np.ndarray) -> list[np.ndarray]:
        """从漏斗口出发，每层落在钉子上方一点，再偏到左边或右边，最后落进格子里的位置。"""
        pts = [np.array([0.0, self.top + 0.4])]
        j = 0
        for r, step in enumerate(path):
            x = (j - r / 2) * self.S
            pts.append(np.array([x, self.top - r * self.V + self.PEG + self.D / 2]))
            j += step
        pts.append(np.array([self.bin_x(j), self.bin_top]))
        pts.append(end)
        return pts

    def action(self, act: str, args: dict) -> list[Animation]:
        self.acted = True
        t = self.theme
        if act == "drop":
            balls = []
            for _ in range(int(args["count"])):
                path = self.paths[self.dropped]
                k = sum(path)
                end = self.slot(k, self.counts[k])
                self.counts[k] += 1
                self.dropped += 1
                ball = PathNode(path=circle(self.D / 2 * 0.92), style=Style(fill=t.A, opacity=0.0))
                self.balls_layer.add(ball)
                balls.append((ball, self.route(path, end)))
            return [_Drops(self, balls, 4.0)]  # 时长写在场景的 dur 上
        if act == "path":
            i = int(args["ball"])
            path = self.paths[i]
            k = sum(path)
            pts = self.route(path, np.array([self.bin_x(k), self.bin_top]))[:-1]
            line = PathNode(path=polyline(pts), style=Style(stroke=t.color(args.get("role"), t.HL_INK), stroke_width=6,
                                                            opacity=0.0))
            self.marks.add(line)
            return [_Show(line, 1.4)]
        if act == "pascal":
            out = []
            for k, c in enumerate(binomial(self.rows)):
                lab = mmath(str(c), self.label_size, t.HL_INK)
                lab.font_floor = self.label_floor
                lab.move_to([self.bin_x(k), self.floor - 0.2 - lab.height / 2])
                lab.set_style(opacity=0.0)
                self.marks.add(lab)
                out.append(FadeTo(lab, 1.0, 0.4))
            return out
        if act == "expect":
            total = self.dropped
            pts = []
            for k, c in enumerate(binomial(self.rows)):
                h = self.floor + c / 2 ** self.rows * total / PER_ROW * self.D
                x0, x1 = self.bin_x(k) - self.S / 2, self.bin_x(k) + self.S / 2
                pts += [((x0, h), (x1, h))]
            segs = pts + [((pts[i][1][0], pts[i][1][1]), (pts[i + 1][0][0], pts[i + 1][0][1])) for i in range(len(pts) - 1)]
            line = PathNode(path=dashed(segs, 0.12, 0.08), style=Style(stroke=t.HL_INK, stroke_width=5, opacity=0.0))
            self.marks.add(line)
            return [FadeTo(line, 1.0, 0.6)]
        if act == "flat":
            total = max(1, self.dropped)
            h = self.floor + total / (self.rows + 1) / PER_ROW * self.D
            half = (self.rows + 1) / 2 * self.S
            line = PathNode(path=dashed([((-half, h), (half, h))], 0.16, 0.1), style=Style(stroke=t.NO, stroke_width=5,
                                                                                         opacity=0.0))
            self.marks.add(line)
            return [FadeTo(line, 1.0, 0.6)]
        if act == "erase":
            return [FadeTo(m, 0.0, 0.4) for m in self.marks.children]
        raise KeyError(f"组件 {self.name} 没有动作 {act}")


# ---------- 样本空间方格 ----------

class Outcomes(Widget):
    name = "outcomes"
    CW, CH = 2.4, 1.3  # 一格的宽、高
    SIZE = 44

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.rows, self.cols = int(d["rows"]), int(d["cols"])
        labels = d.get("labels") or [""] * (self.rows * self.cols)
        self.labels = labels
        node = Group(name=self.obj_id)
        self.pads: list[PathNode] = []
        self.texts: list[Node] = []
        self.crosses: list[PathNode] = []
        cells: list[Node] = []
        for i, text in enumerate(labels):
            r, c = divmod(i, self.cols)
            cx, cy = self.center(i)
            pad = PathNode(path=rect(self.CW, self.CH).transformed(T.translate(cx, cy)),
                           style=Style(fill=t.CARD, stroke=t.FG, stroke_width=3))
            body = rich(text, t, size=self.SIZE).node if text else Group()
            if text:
                body.move_to([cx, cy])
            x0, y0, x1, y1 = cx - self.CW / 2 + 0.25, cy - self.CH / 2 + 0.2, cx + self.CW / 2 - 0.25, cy + self.CH / 2 - 0.2
            cross = PathNode(path=Path().move_to(x0, y0).line_to(x1, y1).move_to(x0, y1).line_to(x1, y0),
                             style=Style(stroke=t.NO, stroke_width=5, opacity=0.0))
            cell = Group(pad, body, cross)
            cells.append(cell)
            self.pads.append(pad)
            self.texts.append(body)
            self.crosses.append(cross)
        node.add(*cells)
        for key, along_rows in (("row_labels", True), ("col_labels", False)):
            for k, text in enumerate(d.get(key) or []):
                lab = rich(text, t, size=self.SIZE * 0.85, color=t.DIM).node
                if along_rows:
                    cx, cy = self.center(k * self.cols)
                    lab.move_to([cx - self.CW / 2 - 0.25 - lab.width / 2, cy])
                else:
                    cx, cy = self.center(k)
                    lab.move_to([cx, cy + self.CH / 2 + 0.2 + lab.height / 2])
                node.add(lab)
        self.frame = Group(name="frame")
        node.add(self.frame)
        self.fill: list[str | None] = [None] * len(labels)  # 每格垫的色（按动作推演）
        self.struck = [False] * len(labels)
        return Built(node, parts={"cell": cells, "frame": [self.frame]}, items=cells)

    def center(self, i: int) -> tuple[float, float]:
        r, c = divmod(i, self.cols)
        return (c - (self.cols - 1) / 2) * self.CW, ((self.rows - 1) / 2 - r) * self.CH

    def cells(self, value) -> list[int]:
        if value is None:
            return list(range(len(self.pads)))
        items = value if isinstance(value, list) else [value]
        return [c if isinstance(c, int) else self.labels.index(c) for c in items]

    def look(self, i: int) -> Restyle:
        """第 i 格按推演出的状态（垫什么色、有没有被划掉）一次改到位。同一批里几个动作碰同一格时，后建的那个
        带着前面的改动，最后画上去的是它，不会互相覆盖。"""
        t = self.theme
        role = self.fill[i]
        if role is None:
            fill, op = t.CARD, 1.0
        elif role == "HL":
            fill, op = t.MARK, 1.0
        else:
            fill, op = t.color(role), t.fill_opacity()
        if self.struck[i]:
            op = op * 0.35
        return Restyle(self.pads[i], 0.5, fill=fill, fill_opacity=op)

    def action(self, act: str, args: dict) -> list[Animation]:
        t = self.theme
        if act in ("shade", "unshade"):
            cells = self.cells(args["cells"] if act == "shade" else args.get("cells"))
            for i in cells:
                self.fill[i] = args.get("role", "A") if act == "shade" else None
            return [self.look(i) for i in cells]
        if act == "strike":
            out: list[Animation] = []
            for i in self.cells(args["cells"]):
                self.struck[i] = True
                out += [FadeTo(self.texts[i], 0.3, 0.5), _Show(self.crosses[i], 0.6), self.look(i)]
            return out
        if act == "restore":
            out = []
            for i in self.cells(args.get("cells")):
                self.struck[i] = False
                out += [FadeTo(self.texts[i], 1.0, 0.5), FadeTo(self.crosses[i], 0.0, 0.4), self.look(i)]
            return out
        if act == "frame":
            chosen = set(self.cells(args["cells"]))
            edges: dict[tuple, int] = {}
            for i in chosen:
                cx, cy = self.center(i)
                x0, x1, y0, y1 = cx - self.CW / 2, cx + self.CW / 2, cy - self.CH / 2, cy + self.CH / 2
                for e in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x0, y1), (x1, y1)), ((x0, y0), (x0, y1))):
                    key = tuple(sorted((tuple(round(v, 6) for v in e[0]), tuple(round(v, 6) for v in e[1]))))
                    edges[key] = edges.get(key, 0) + 1
            path = Path()
            for (a, b), n in edges.items():
                if n == 1:  # 两个选中的格子共用的边不画：只描外轮廓
                    path.move_to(*a).line_to(*b)
            line = PathNode(path=path, style=Style(stroke=t.color(args.get("role"), t.HL_INK), stroke_width=9, opacity=0.0))
            out = [FadeTo(old, 0.0, 0.3) for old in self.frame.children]
            self.frame.add(line)
            return out + [FadeTo(line, 1.0, 0.5)]
        if act == "unframe":
            return [FadeTo(old, 0.0, 0.3) for old in self.frame.children]
        raise KeyError(f"组件 {self.name} 没有动作 {act}")
