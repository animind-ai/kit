"""函数图像（墨线实现）：坐标轴、函数曲线、曲线上的点、割线、切线。

用在导数一类的洞察集里：割线的第二个点沿曲线滑向第一个点（h → 0），割线逼近切线；
再用镜头（camera）把点附近放大几十倍，看可导的曲线越来越像直线、y=|x| 的尖角怎么放大都还是尖角。

函数写成字符串参数（f: "x^2"、"abs(x)"、"sin(x)"），由 scene_spec.expr 按白名单解析成语法树，从不 eval。
画曲线时用同一棵树做前向自动微分（对偶数），得到每个分点的函数值和（单侧）导数，
每段画成三次 Hermite 贝塞尔：控制点横坐标三等分，所以 x(t) 是线性的、y(t) 就是 Hermite 插值多项式。
- 三次及以下的多项式：一段就和原函数完全相同（放大多少倍都是精确的）；
- 其他函数：在每段的 1/4、1/2、3/4 处和真值比较，误差超过 TOL 就对半再分，误差按 h⁴ 收敛；
- 尖点（abs 的零点）用单侧导数：对偶数的 abs 在 0 处取 |方向|，段首用右导数、段尾用左导数，所以尖角是尖的。
点、点的名字、刻度和坐标轴名字都放在 keep_size 的组里：镜头放大 60 倍，它们跟着走、大小不变。

暴露的子元素（都可以单独 show / focus / mark）：
- axes：坐标轴（含刻度、名字）；plot / plot_label：曲线和它的名字；
- point / dot：曲线上的点（point 含名字，dot 只有圆点，镜头对准它时中心正好是点本身）；
- secant / qpoint / slope：割线、割线的第二个点 Q（含名字）、割线斜率读数（默认在左上角，readout_at 可改，数学坐标）；
- tangent：切线；line：其他直线（竖线 x=c，或过某点、给定斜率的直线）。

动作：
- draw {plot: i}：沿曲线画出来；
- approach {secant: i, h: 目标}：Q 沿曲线滑到 x0+h，h 按几何级数变（1 → 0.1 → 0.01 看起来匀速），
  割线和斜率读数逐帧跟着变。
"""

from __future__ import annotations

import math

import numpy as np
from moxian.anim import Animation, Draw, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import Path, circle, line, polygon
from scene_spec.expr import parse

from .text import mmath, mtext
from .widgets import Built, Widget

TOL = 2e-6  # 曲线贝塞尔近似的容许误差（数学单位）；放大 60 倍时远小于一个像素
MAX_DEPTH = 22
SAMPLES = 600  # 找定义域、找出框位置时的采样数
LABEL_DIRS = {
    "u": (0.0, 1.0), "d": (0.0, -1.0), "l": (-1.0, 0.0), "r": (1.0, 0.0),
    "ul": (-0.7, 0.7), "ur": (0.7, 0.7), "dl": (-0.7, -0.7), "dr": (0.7, -0.7),
}


# ---------- 对偶数：前向自动微分 ----------

class Dual:
    """a + b·ε（ε² = 0）。b 是沿"方向 ±1"的方向导数；abs 在 0 处取 |b|，于是能算单侧导数。"""

    __slots__ = ("v", "d")

    def __init__(self, v: float, d: float = 0.0):
        self.v, self.d = float(v), float(d)

    def __add__(self, o: Dual) -> Dual:
        return Dual(self.v + o.v, self.d + o.d)

    def __sub__(self, o: Dual) -> Dual:
        return Dual(self.v - o.v, self.d - o.d)

    def __mul__(self, o: Dual) -> Dual:
        return Dual(self.v * o.v, self.d * o.v + self.v * o.d)

    def __truediv__(self, o: Dual) -> Dual:
        return Dual(self.v / o.v, (self.d * o.v - self.v * o.d) / (o.v * o.v))

    def __neg__(self) -> Dual:
        return Dual(-self.v, -self.d)


def _pow(a: Dual, b: Dual) -> Dual:
    if b.d == 0.0:  # 指数是常数：x^n
        n = b.v
        if n == 0:
            return Dual(1.0, 0.0)
        value = a.v ** n
        if isinstance(value, complex):
            raise ValueError("负数的非整数次幂")
        slope = n * a.v ** (n - 1) * a.d if a.d != 0 else 0.0
        return Dual(value, slope)
    if a.d == 0.0:  # 底数是常数：c^x
        value = a.v ** b.v
        return Dual(value, value * math.log(a.v) * b.d)
    return _call("exp", _call("ln", a) * b)


def _call(name: str, a: Dual) -> Dual:
    v, d = a.v, a.d
    if name == "sin":
        return Dual(math.sin(v), math.cos(v) * d)
    if name == "cos":
        return Dual(math.cos(v), -math.sin(v) * d)
    if name == "tan":
        c = math.cos(v)
        return Dual(math.tan(v), d / (c * c))
    if name == "exp":
        e = math.exp(v)
        return Dual(e, e * d)
    if name in ("ln", "log"):
        return Dual(math.log(v), d / v)
    if name == "sqrt":
        r = math.sqrt(v)
        return Dual(r, d / (2 * r) if d else 0.0)
    if name == "abs":
        if v > 0:
            return Dual(v, d)
        if v < 0:
            return Dual(-v, -d)
        return Dual(0.0, abs(d))  # 尖点：方向导数取绝对值
    raise KeyError(name)


def _dual(node: tuple, x: Dual) -> Dual:
    op = node[0]
    if op == "num":
        return Dual(node[1])
    if op == "x":
        return x
    if op == "neg":
        return -_dual(node[1], x)
    if op == "call":
        return _call(node[1], _dual(node[2], x))
    a, b = _dual(node[1], x), _dual(node[2], x)
    if op == "add":
        return a + b
    if op == "sub":
        return a - b
    if op == "mul":
        return a * b
    if op == "div":
        return a / b
    return _pow(a, b)


class Function:
    """白名单表达式 → 可求值、可求单侧导数的函数。"""

    def __init__(self, text: str):
        self.text = text
        self.tree = parse(text)

    def _at(self, x: float, direction: float) -> Dual | None:
        try:
            r = _dual(self.tree, Dual(x, direction))
        except (ValueError, ZeroDivisionError, OverflowError):
            return None
        return r if math.isfinite(r.v) else None

    def __call__(self, x: float) -> float:
        r = self._at(x, 0.0)
        return math.nan if r is None else r.v

    def right(self, x: float) -> float:
        """右导数 f'₊(x)。"""
        r = self._at(x, 1.0)
        return math.nan if r is None else r.d

    def left(self, x: float) -> float:
        """左导数 f'₋(x)。"""
        r = self._at(x, -1.0)
        return math.nan if r is None else -r.d

    def slope(self, x: float) -> float:
        """导数；左右导数不相等（尖点）时返回 nan。"""
        a, b = self.left(x), self.right(x)
        return a if math.isfinite(a) and math.isfinite(b) and abs(a - b) <= 1e-9 * max(1.0, abs(a)) else math.nan


# ---------- 曲线 → 贝塞尔 ----------

Segment = tuple[float, float, float, float, float, float, float, float]  # x0 y0 c1x c1y c2x c2y x1 y1


def _hermite(f: Function, a: float, b: float) -> Segment | None:
    ya, yb = f(a), f(b)
    da, db = f.right(a), f.left(b)
    if not all(math.isfinite(v) for v in (ya, yb, da, db)):
        return None
    h = b - a
    return (a, ya, a + h / 3, ya + da * h / 3, b - h / 3, yb - db * h / 3, b, yb)


def _bez_y(seg: Segment, t: float) -> float:
    s = 1 - t
    return s * s * s * seg[1] + 3 * s * s * t * seg[3] + 3 * s * t * t * seg[5] + t * t * t * seg[7]


def _segments(f: Function, a: float, b: float, depth: int = 0) -> list[Segment]:
    seg = _hermite(f, a, b)
    ok = seg is not None
    if ok:
        for t in (0.25, 0.5, 0.75):
            truth = f(a + (b - a) * t)
            if not math.isfinite(truth) or abs(_bez_y(seg, t) - truth) > TOL:
                ok = False
                break
    if ok or depth >= MAX_DEPTH:
        return [seg] if seg is not None else []
    m = (a + b) / 2
    return _segments(f, a, m, depth + 1) + _segments(f, m, b, depth + 1)


def _bisect(inside, lo: float, hi: float) -> float:
    """inside(lo) != inside(hi)；返回分界点（偏向 inside 一侧）。"""
    good_lo = inside(lo)
    for _ in range(60):
        mid = (lo + hi) / 2
        if inside(mid) == good_lo:
            lo = mid
        else:
            hi = mid
    return lo if good_lo else hi


def visible_runs(f: Function, x0: float, x1: float, y0: float, y1: float) -> list[tuple[float, float]]:
    """[x0, x1] 里函数有定义、且值落在 [y0, y1] 内的区间（端点用二分求准）。"""
    def inside(x: float) -> bool:
        y = f(x)
        return math.isfinite(y) and y0 - 1e-12 <= y <= y1 + 1e-12

    xs = np.linspace(x0, x1, SAMPLES + 1)
    flags = [inside(float(x)) for x in xs]
    runs: list[tuple[float, float]] = []
    start = float(xs[0]) if flags[0] else None
    for i in range(1, len(xs)):
        if flags[i] and not flags[i - 1]:
            start = _bisect(inside, float(xs[i - 1]), float(xs[i]))
        elif not flags[i] and flags[i - 1] and start is not None:
            runs.append((start, _bisect(inside, float(xs[i - 1]), float(xs[i]))))
            start = None
    if start is not None:
        runs.append((start, float(xs[-1])))
    return [(a, b) for a, b in runs if b - a > 1e-9]


def curve_segments(f: Function, x0: float, x1: float, y0: float, y1: float,
                   breaks: list[float] | None = None) -> list[list[Segment]]:
    """每个可见区间一条子路径；breaks（例如曲线上标的点）一定是分点，尖点就不会被一段跨过去。"""
    out = []
    for a, b in visible_runs(f, x0, x1, y0, y1):
        cuts = sorted({a, b, *(x for x in breaks or [] if a < x < b)})
        # 至少分 4 段，让 Draw 的进度和弧长大致成正比
        pieces: list[Segment] = []
        for p, q in zip(cuts, cuts[1:]):
            n = max(1, math.ceil(4 * (q - p) / (b - a)))
            for k in range(n):
                pieces += _segments(f, p + (q - p) * k / n, p + (q - p) * (k + 1) / n)
        if pieces:
            out.append(pieces)
    return out


def bezier_path(subpaths: list[list[Segment]], unit: float) -> Path:
    p = Path()
    for segs in subpaths:
        p.move_to(segs[0][0] * unit, segs[0][1] * unit)
        for s in segs:
            p.cubic_to(*(v * unit for v in s[2:]))
    return p


def clip_line(point: tuple[float, float], direction: tuple[float, float],
              box: tuple[float, float, float, float]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """过 point、方向 direction 的直线被矩形 box=(x0, x1, y0, y1) 截下的线段（Liang–Barsky）。"""
    (px, py), (dx, dy) = point, direction
    lo, hi = -math.inf, math.inf
    for p0, d, a, b in ((px, dx, box[0], box[1]), (py, dy, box[2], box[3])):
        if abs(d) < 1e-15:
            if not a <= p0 <= b:
                return None
            continue
        t1, t2 = (a - p0) / d, (b - p0) / d
        lo, hi = max(lo, min(t1, t2)), min(hi, max(t1, t2))
    if lo >= hi:
        return None
    return (px + lo * dx, py + lo * dy), (px + hi * dx, py + hi * dy)


def secant_slope(f: Function, x0: float, h: float) -> float:
    return (f(x0 + h) - f(x0)) / h


# ---------- 动画 ----------

class _Approach(Animation):
    """割线的 h 从当前值变到目标值：同号时按几何级数（对数插值），否则线性。"""

    def __init__(self, graph: Graph, index: int, target: float, duration: float = 2.0):
        super().__init__(graph.node, duration, easing.smooth)
        self.graph, self.index, self.target = graph, index, float(target)

    def begin(self) -> None:
        super().begin()
        self.h0 = self.graph.h[self.index]

    def interpolate(self, alpha: float) -> None:
        h0, h1 = self.h0, self.target
        h = h0 * (h1 / h0) ** alpha if h0 * h1 > 0 else h0 + (h1 - h0) * alpha
        if abs(h) < 1e-9:
            h = math.copysign(1e-9, h1 or 1.0)
        self.graph.set_h(self.index, h)

    def finish(self) -> None:
        self.graph.set_h(self.index, self.target)


# ---------- 组件 ----------

class Graph(Widget):
    name = "graph"
    WIDTH, HEIGHT = 9.4, 6.4  # 自动单位长度时，坐标区域大致占这么大（排版时只缩不放）
    DOT = 0.085
    LABEL = 30
    TICK = 0.09

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.xr = [float(v) for v in d["x"]]
        self.yr = [float(v) for v in d["y"]]
        (x0, x1), (y0, y1) = self.xr, self.yr
        self.box = (x0, x1, y0, y1)
        self.u = float(d.get("unit") or min(self.WIDTH / (x1 - x0), self.HEIGHT / (y1 - y0)))
        node = Group(name=self.obj_id)
        parts: dict[str, list[Node]] = {k: [] for k in (
            "axes", "plot", "plot_label", "point", "dot", "secant", "qpoint", "slope", "tangent", "line")}

        axes = self._axes(d.get("ticks", 1), d.get("tick_labels", True), d.get("axis_labels", ["x", "y"]))
        node.add(axes)
        parts["axes"].append(axes)

        self.fns: list[Function] = []
        for i, spec in enumerate(d["plots"]):
            f = Function(spec["f"])
            self.fns.append(f)
            dom = spec.get("domain") or self.xr
            breaks = [p["x"] for p in d.get("points") or [] if p.get("plot", 0) == i]
            path = bezier_path(curve_segments(f, float(dom[0]), float(dom[1]), y0, y1, breaks), self.u)
            curve = PathNode(path=path, style=Style(stroke=t.color(spec.get("role"), t.A), stroke_width=5), z=2,
                             name=f"plot{i}")
            parts["plot"].append(curve)
            label = Group(name=f"plot_label{i}")
            if spec.get("label"):
                label = self._plot_label(f, spec["label"], t.ink(spec.get("role", "A")), dom)
            parts["plot_label"].append(label)
            node.add(curve, label)

        self.points: list[tuple[int, float]] = []
        for i, spec in enumerate(d.get("points") or []):
            k, x = int(spec.get("plot", 0)), float(spec["x"])
            self.points.append((k, x))
            marker, dot = self._marker((x, self.fns[k](x)), spec.get("label"), t.color(spec.get("role"), t.C),
                                       spec.get("label_dir") or self._label_dir(k, x), f"point{i}")
            parts["point"].append(marker)
            parts["dot"].append(dot)
            node.add(marker)

        # 割线：一条直线 + 第二个点 Q（+ 可选的斜率读数）。h 存在 self.h 里，approach 逐帧改
        self.h: list[float] = []
        self.secants: list[tuple[PathNode, Group, Group | None, dict]] = []
        for i, spec in enumerate(d.get("secants") or []):
            p = int(spec.get("point", 0))
            color = t.color(spec.get("role"), t.B)
            ink = t.ink(spec.get("role", "B"))
            seg = PathNode(style=Style(stroke=color, stroke_width=3), z=1, name=f"secant{i}")
            k, x = self.points[p]
            q, _qdot = self._marker((x, x), spec.get("label"), color, spec.get("label_dir") or "ur", f"qpoint{i}")
            readout = None
            if spec.get("readout"):
                readout = Group(name=f"slope{i}")
                ax, ay = d.get("readout_at") or (x0 + 0.03 * (x1 - x0), y1 - 0.06 * (y1 - y0))
                corner = (float(ax), float(ay) - 0.11 * (y1 - y0) * len(parts["slope"]))
                readout.matrix = T.translate(corner[0] * self.u, corner[1] * self.u)
                parts["slope"].append(readout)
                node.add(readout)
            self.secants.append((seg, q, readout, {"point": p, "ink": ink, "prefix": spec.get("readout_text", "割线斜率")}))
            self.h.append(float(spec["h"]))
            parts["secant"].append(seg)
            parts["qpoint"].append(q)
            node.add(seg, q)
            self.set_h(i, self.h[i])

        for i, spec in enumerate(d.get("tangents") or []):
            k, x = self.points[int(spec.get("point", 0))]
            f = self.fns[k]
            slope = f.slope(x)
            if not math.isfinite(slope):
                raise ValueError(f"graph {self.obj_id}：{f.text} 在 x={x:g} 处左右导数不相等，没有切线")
            seg = self._line_node((x, f(x)), (1.0, slope), t.ink(spec.get("role", "HL")), 3, f"tangent{i}")
            seg.z = 2.5  # 叠在曲线上面（细线）：放大后能看见它和曲线几乎贴在一起
            parts["tangent"].append(seg)
            node.add(seg)

        for i, spec in enumerate(d.get("lines") or []):
            color = t.color(spec.get("role"), t.NO)
            if "x" in spec:
                seg = self._line_node((float(spec["x"]), 0.0), (0.0, 1.0), color, 3, f"line{i}")
            else:
                px, py = (float(v) for v in spec["through"])
                seg = self._line_node((px, py), (1.0, float(spec["slope"])), color, 3, f"line{i}")
            parts["line"].append(seg)
            node.add(seg)

        items = [n for key in ("axes", "plot", "plot_label", "tangent", "line", "secant", "point", "qpoint", "slope")
                 for n in parts[key]]
        return Built(node, parts=parts, items=items)

    # ---- 构件 ----

    def _s(self, x: float, y: float) -> tuple[float, float]:
        return x * self.u, y * self.u

    def _keep(self, at: tuple[float, float], *children: Node, name: str = "") -> Group:
        """局部原点在 at 的 keep_size 组：镜头放大时绕 at 缩回去，大小不变。"""
        g = Group(*children, name=name)
        g.matrix = T.translate(*at)
        g.keep_size = True
        return g

    def _axes(self, ticks, tick_labels: bool, axis_labels) -> Group:
        t = self.theme
        (x0, x1), (y0, y1) = self.xr, self.yr
        ax_y = 0.0 if y0 <= 0 <= y1 else y0  # x 轴画在 y=0（不在范围内就画在底边）
        ax_x = 0.0 if x0 <= 0 <= x1 else x0
        style = Style(stroke=t.DIM, stroke_width=2.5)
        g = Group(name="axes")
        g.add(PathNode(path=line(*self._s(x0, ax_y), *self._s(x1, ax_y)), style=style))
        g.add(PathNode(path=line(*self._s(ax_x, y0), *self._s(ax_x, y1)), style=style))
        a = 0.14
        g.add(self._keep(self._s(x1, ax_y), PathNode(path=polygon([(a, 0), (-a * 0.6, a * 0.55), (-a * 0.6, -a * 0.55)]),
                                                      style=Style(fill=t.DIM))))
        g.add(self._keep(self._s(ax_x, y1), PathNode(path=polygon([(0, a), (a * 0.55, -a * 0.6), (-a * 0.55, -a * 0.6)]),
                                                      style=Style(fill=t.DIM))))
        if axis_labels:
            lx = mmath(axis_labels[0], self.LABEL, t.DIM)
            lx.move_to([0.0, 0.0]).shift([0.12 + lx.width / 2, -0.28])
            ly = mmath(axis_labels[1], self.LABEL, t.DIM)
            ly.move_to([0.0, 0.0]).shift([-0.3, 0.05 + ly.height / 2])
            g.add(self._keep(self._s(x1, ax_y), lx), self._keep(self._s(ax_x, y1), ly))
        if ticks:
            step = float(ticks)
            for axis, (lo, hi) in (("x", self.xr), ("y", self.yr)):
                k = math.ceil(lo / step - 1e-9)
                while k * step <= hi - 0.3 * step:
                    v = k * step
                    k += 1
                    if abs(v) < 1e-9:
                        continue
                    pos = self._s(v, ax_y) if axis == "x" else self._s(ax_x, v)
                    mark = line(0, -self.TICK, 0, self.TICK) if axis == "x" else line(-self.TICK, 0, self.TICK, 0)
                    kids: list[Node] = [PathNode(path=mark, style=style)]
                    if tick_labels:
                        lab = mmath(f"{v:g}", self.LABEL * 0.8, t.DIM)
                        lab.move_to([0.0, -0.32] if axis == "x" else [-0.3, 0.0])
                        kids.append(lab)
                    g.add(self._keep(pos, *kids))
        return g

    def _plot_label(self, f: Function, tex: str, color: str, dom) -> Node:
        """曲线的名字放在右端最后一个可见点的旁边。"""
        (y0, y1) = self.yr
        runs = visible_runs(f, float(dom[0]), float(dom[1]), y0, y1)
        x = runs[-1][1] if runs else float(dom[1])
        y = f(x)
        lab = mmath(tex, self.LABEL, color)
        lab.move_to([0.0, 0.0]).shift([0.15 + lab.width / 2, -0.05])
        if y > y1 - 0.08 * (y1 - y0):  # 顶到上沿：名字放在线的右下
            lab.shift([0.0, -0.35])
        return self._keep(self._s(x, y), lab, name="plot_label")

    def _label_dir(self, k: int, x: float) -> tuple[float, float]:
        """点的名字默认放在曲线凸出的那一侧的反面：沿法线，避开曲线和切线。"""
        f = self.fns[k]
        s = f.slope(x)
        if not math.isfinite(s):
            return (0.75, -0.66)
        e = 1e-3 * max(1.0, abs(x))
        bend = f(x + e) + f(x - e) - 2 * f(x)
        n = np.array([s, -1.0]) if bend >= 0 else np.array([-s, 1.0])  # 下凸：曲线在切线上方，名字放下方
        n /= np.linalg.norm(n)
        return float(n[0]), float(n[1])

    def _marker(self, at: tuple[float, float], label: str | None, color: str, direction, name: str) -> tuple[Group, PathNode]:
        dot = PathNode(path=circle(self.DOT), style=Style(fill=color), name="dot")
        kids: list[Node] = [dot]
        if label:
            lab = mmath(label, self.LABEL, color)
            v = np.array(LABEL_DIRS[direction] if isinstance(direction, str) else direction, dtype=float)
            v /= np.linalg.norm(v)
            r = 0.16 + 0.5 * math.hypot(lab.width * v[0], lab.height * v[1])
            lab.move_to(v * r)
            kids.append(lab)
        g = self._keep(self._s(*at), *kids, name=name)
        g.z = 3
        return g, dot

    def _line_path(self, point: tuple[float, float], direction: tuple[float, float]) -> Path:
        seg = clip_line(point, direction, self.box)
        if seg is None:
            return Path()
        (ax, ay), (bx, by) = seg
        return line(*self._s(ax, ay), *self._s(bx, by))

    def _line_node(self, point, direction, color: str, width: float, name: str) -> PathNode:
        return PathNode(path=self._line_path(point, direction), style=Style(stroke=color, stroke_width=width), z=1, name=name)

    # ---- 割线 ----

    def set_h(self, i: int, h: float) -> None:
        seg, q, readout, info = self.secants[i]
        k, x = self.points[info["point"]]
        f = self.fns[k]
        self.h[i] = h
        qx = x + h
        q.matrix = T.translate(*self._s(qx, f(qx)))
        slope = secant_slope(f, x, h)
        seg.path = self._line_path((x, f(x)), (1.0, slope))  # 每次换一个新的 Path：渲染器按对象缓存
        if readout is not None:
            readout.children = [self._readout_text(i, f"{info['prefix']} {slope:.2f}", info["ink"])]

    def _readout_text(self, i: int, text: str, color: str) -> Node:
        """读数文字按 (第几条割线, 文字) 缓存：同一个节点不会同时挂在两处。"""
        cache = self.__dict__.setdefault("_readouts", {})
        key = (i, text)
        node = cache.get(key)
        if node is None:
            node = mtext(text.replace("-", "−"), 24, color)
            b = node.bbox()
            node.shift([-b.x0, -b.y1])  # 左上角对齐到读数的位置
            if len(cache) < 2000:
                cache[key] = node
        return node

    # ---- 动作 ----

    def action(self, act: str, args: dict) -> list[Animation]:
        if act == "draw":
            return [Draw(self.built.parts["plot"][int(args.get("plot", 0))], 1.5)]
        if act == "approach":
            return [_Approach(self, int(args.get("secant", 0)), float(args["h"]))]
        raise KeyError(f"组件 {self.name} 没有动作 {act}")
