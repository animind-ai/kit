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

文字避让（_declutter，建好时和每次参数 / 割线变化后跑一遍）：组件里所有的线（坐标轴、曲线、割线、切线、
直线、斜率三角形的边，不管此刻显不显示，免得名字随显隐跳来跳去）当障碍——
- 曲线名字：右端旁边放不下就往下挪，再不行放到端点正上方、端点左边；
- 点的名字：label_dir（或默认的法线方向）只是首选，被线穿过时在 16 个方向、三档距离里找最近的空位；
  动起来的点（割线的 Q）当前位置还空着就不挪，免得逐帧跳；
- 刻度数字：被线穿过、或被上面这些名字和三角形的标注压住时隐去（刻度线还在）。

暴露的子元素（都可以单独 show / focus / mark）：
- axes：坐标轴（含刻度、名字）；plot / plot_label：曲线和它的名字；
- point / dot：曲线上的点（point 含名字，dot 只有圆点，镜头对准它时中心正好是点本身）；
- secant / qpoint / slope：割线、割线的第二个点 Q（含名字）、割线斜率读数（默认在左上角，readout_at 可改，数学坐标）；
- tangent：切线；line：其他直线（竖线 x=c，或过某点、给定斜率的直线）。

- triangle：斜率三角形（从点出发横走 run、再竖着走到曲线上，两条直角边标上长度）。

场景参数（scene-spec 顶层 params）：曲线的 f、点的 x、直线的 x / slope / through、三角形的 run 都可以写
引用参数的表达式（"k*x+1"、"t"）。组件在 moxian 的参数机制上挂一个 updater：参数一变，
曲线重算贝塞尔、点挪到新位置、三角形和标注重建、割线和切线跟着点走。

动作：
- draw {plot: i}：沿曲线画出来；
- approach {secant: i, h: 目标}：Q 沿曲线滑到 x0+h，h 按几何级数变（1 → 0.1 → 0.01 看起来匀速），
  割线和斜率读数逐帧跟着变。
"""

from __future__ import annotations

import math

import numpy as np
from moxian.anim import Animation, Draw, Param, easing, updater
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import Path, circle, line, polygon
from scene_spec.expr import evaluate, free_names, parse

from ..lint import crosses, segments_of
from .readout import number
from .scan import flatten
from .text import mmath, mtext
from .widgets import Built, Widget

TOL = 2e-6  # 曲线贝塞尔近似的容许误差（数学单位）；放大 60 倍时远小于一个像素
MAX_DEPTH = 22
SAMPLES = 600  # 找定义域、找出框位置时的采样数
CLEAR = 0.04  # 文字离线至少这么远（画面单位）
AROUND = [math.pi * k / 8 for k in range(16)]  # 点的名字找空位时试的方向
REACH = (1.0, 1.5, 2.1)  # …… 和距离（名字中心离点的距离的倍数）
REACH_FAR = (2.8, 3.6)  # v2 抬大字号后近处都放不下时再试的距离
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


def _dual(node: tuple, x: Dual, env: dict[str, float] | None = None) -> Dual:
    op = node[0]
    if op == "num":
        return Dual(node[1])
    if op == "x":
        return x
    if op == "var":
        return Dual(env[node[1]])  # 参数对 x 是常数
    if op == "neg":
        return -_dual(node[1], x, env)
    if op == "call":
        return _call(node[1], _dual(node[2], x, env))
    a, b = _dual(node[1], x, env), _dual(node[2], x, env)
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
    """白名单表达式 → 可求值、可求单侧导数的函数。params 给出时可以引用场景参数（按当前值求值）。"""

    def __init__(self, text: str, params: dict[str, Param] | None = None):
        self.text = text
        self.params = params or {}
        self.tree = parse(text, set(self.params))
        self.names = free_names(self.tree)

    def env(self) -> dict[str, float]:
        return {n: self.params[n].value for n in self.names}

    def _at(self, x: float, direction: float) -> Dual | None:
        try:
            r = _dual(self.tree, Dual(x, direction), self.env() if self.names else None)
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
    # 端点处切线接近竖直（如 √x 在 0）时导数极大：控制柄沿切线方向截短到不超过这一段的跨度，不然会飞出画面
    cap = max(abs(yb - ya), h)
    ka = min(1.0, cap / abs(da * h / 3)) if da else 1.0
    kb = min(1.0, cap / abs(db * h / 3)) if db else 1.0
    return (a, ya, a + ka * h / 3, ya + ka * da * h / 3, b - kb * h / 3, yb - kb * db * h / 3, b, yb)


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

    def _sz(self, size: float) -> float:
        """字号；后端给了 opts["min_text"]（v2 的全局下限换成 kit 字号）时不小于它。"""
        return max(size, float(self.opts.get("min_text", 0.0)))

    def smallest_text(self) -> float:
        return self._sz(self.LABEL * 0.8)

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.xr = [float(v) for v in d["x"]]
        self.yr = [float(v) for v in d["y"]]
        (x0, x1), (y0, y1) = self.xr, self.yr
        self.box = (x0, x1, y0, y1)
        self.u = float(d.get("unit") or min(self.WIDTH / (x1 - x0), self.HEIGHT / (y1 - y0)))
        self.used: set[str] = set()  # 用到的场景参数
        self.refreshers: list = []  # 参数变了要重算的部件：无参函数，按当前参数值更新
        node = Group(name=self.obj_id)
        self.ready = False
        self.tick_slots: list[tuple[Group, Node]] = []  # (刻度的 keep 组, 刻度数字)
        self.axis_lines: list[PathNode] = []
        self.plot_labels: list[tuple[Group, Node, list[np.ndarray]]] = []  # (keep 组, 名字, 候选中心)
        self.marker_labels: list[dict] = []
        parts: dict[str, list[Node]] = {k: [] for k in (
            "axes", "plot", "plot_label", "point", "dot", "secant", "qpoint", "slope", "tangent", "line", "triangle")}

        axes = self._axes(d.get("ticks", 1), d.get("tick_labels", True), d.get("axis_labels", ["x", "y"]))
        node.add(axes)
        parts["axes"].append(axes)

        self.fns: list[Function] = []
        self.point_x = [self._expr(spec["x"]) for spec in d.get("points") or []]
        for i, spec in enumerate(d["plots"]):
            f = Function(spec["f"], self.params)
            self.used |= f.names
            self.fns.append(f)
            dom = spec.get("domain") or self.xr
            curve = PathNode(path=self._curve_path(i, dom), style=Style(stroke=t.color(spec.get("role"), t.A), stroke_width=5),
                             z=2, name=f"plot{i}")
            parts["plot"].append(curve)
            label = Group(name=f"plot_label{i}")
            if spec.get("label"):
                label = self._plot_label(f, spec["label"], t.ink(spec.get("role", "A")), dom)
            parts["plot_label"].append(label)
            node.add(curve, label)
            if f.names or any(self.point_x[j][1] for j, p in enumerate(d.get("points") or []) if p.get("plot", 0) == i):
                self.refreshers.append(lambda i=i, dom=dom, curve=curve, label=label: self._refresh_plot(i, dom, curve, label))

        self.points: list[tuple[int, float]] = []
        for i, spec in enumerate(d.get("points") or []):
            k, x = int(spec.get("plot", 0)), self.point_x[i][0]()
            self.points.append((k, x))
            marker, dot = self._marker((x, self.fns[k](x)), spec.get("label"), t.color(spec.get("role"), t.C),
                                       spec.get("label_dir") or self._label_dir(k, x), f"point{i}")
            parts["point"].append(marker)
            parts["dot"].append(dot)
            node.add(marker)
            if self.point_x[i][1] or self.fns[k].names:
                self.refreshers.append(lambda i=i, marker=marker: self._refresh_point(i, marker))

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
            if self._point_moves(p):
                self.refreshers.append(lambda i=i: self.set_h(i, self.h[i]))

        for i, spec in enumerate(d.get("tangents") or []):
            p = int(spec.get("point", 0))
            seg = PathNode(path=self._tangent_path(p), style=Style(stroke=t.ink(spec.get("role", "HL")), stroke_width=3),
                           z=2.5, name=f"tangent{i}")  # 叠在曲线上面（细线）：放大后能看见它和曲线几乎贴在一起
            parts["tangent"].append(seg)
            node.add(seg)
            if self._point_moves(p):
                self.refreshers.append(lambda p=p, seg=seg: setattr(seg, "path", self._tangent_path(p)))

        for i, spec in enumerate(d.get("lines") or []):
            color = t.color(spec.get("role"), t.NO)
            if "x" in spec:
                xs = self._expr(spec["x"])

                def geom(xs=xs):
                    return (xs[0](), 0.0), (0.0, 1.0)

                moving = xs[1]
            else:
                px, py, sl = self._expr(spec["through"][0]), self._expr(spec["through"][1]), self._expr(spec["slope"])

                def geom(px=px, py=py, sl=sl):
                    return (px[0](), py[0]()), (1.0, sl[0]())

                moving = px[1] or py[1] or sl[1]
            seg = self._line_node(*geom(), color, 3, f"line{i}")
            parts["line"].append(seg)
            node.add(seg)
            if moving:
                self.refreshers.append(lambda seg=seg, geom=geom: setattr(seg, "path", self._line_path(*geom())))

        for i, spec in enumerate(d.get("triangles") or []):
            tri = Group(name=f"triangle{i}")
            tri.z = 1.5
            run = self._expr(spec.get("run", 1))
            role = spec.get("role", "C")
            p = int(spec.get("point", 0))
            labels = bool(spec.get("labels", True))

            def build(tri=tri, p=p, run=run, role=role, labels=labels):
                tri.children = self._triangle(p, run[0](), role, labels)

            build()
            parts["triangle"].append(tri)
            node.add(tri)
            if run[1] or self._point_moves(p):
                self.refreshers.append(build)

        self.parts = parts
        self.ready = True
        self._declutter()
        if self.refreshers:
            # 参数一变就把依赖它的部件全部重算一遍（每帧至多一次；参数不动时不调用），再重新避让
            self.refreshers.append(self._declutter)
            updater(node, lambda _n: [r() for r in self.refreshers], *(self.params[n] for n in sorted(self.used)))

        items = [n for key in ("axes", "plot", "plot_label", "tangent", "line", "triangle", "secant", "point", "qpoint",
                               "slope") for n in parts[key]]
        return Built(node, parts=parts, items=items)

    # ---- 参数 ----

    def _expr(self, value) -> tuple:
        """数或参数表达式 → (取当前值的函数, 是否依赖参数)。"""
        if not isinstance(value, str):
            v = float(value)
            return (lambda: v), False  # noqa: E731
        tree = parse(value, set(self.params), allow_x=False)
        names = free_names(tree)
        self.used |= names
        params = self.params
        return (lambda: evaluate(tree, math.nan, {n: params[n].value for n in names})), bool(names)

    def _point_moves(self, p: int) -> bool:
        k = self.points[p][0]
        return bool(self.point_x[p][1] or self.fns[k].names)

    def _point(self, p: int) -> tuple[int, float]:
        """第 p 个点的 (曲线序号, 当前 x)。"""
        k = self.points[p][0]
        return k, self.point_x[p][0]()

    def _curve_path(self, i: int, dom) -> Path:
        (y0, y1) = self.yr
        breaks = [self.point_x[j][0]() for j, p in enumerate(self.definition.get("points") or []) if p.get("plot", 0) == i]
        return bezier_path(curve_segments(self.fns[i], float(dom[0]), float(dom[1]), y0, y1, breaks), self.u)

    def _refresh_plot(self, i: int, dom, curve: PathNode, label: Group) -> None:
        curve.path = self._curve_path(i, dom)
        if label.children and self.fns[i].names:
            (y0, y1) = self.yr
            runs = visible_runs(self.fns[i], float(dom[0]), float(dom[1]), y0, y1)
            x = runs[-1][1] if runs else float(dom[1])
            label.matrix = T.translate(*self._s(x, self.fns[i](x)))

    def _refresh_point(self, i: int, marker: Group) -> None:
        k, x = self._point(i)
        y = self.fns[k](x)
        if math.isfinite(y):
            marker.matrix = T.translate(*self._s(x, y))

    def _tangent_path(self, p: int) -> Path:
        k, x = self._point(p)
        f = self.fns[k]
        slope = f.slope(x)
        if not math.isfinite(slope):
            if not self._point_moves(p):
                raise ValueError(f"graph {self.obj_id}：{f.text} 在 x={x:g} 处左右导数不相等，没有切线")
            return Path()
        return self._line_path((x, f(x)), (1.0, slope))

    def _triangle(self, p: int, run: float, role: str, labels: bool) -> list[Node]:
        """斜率三角形：P →（横走 run）→ 竖着走到曲线上。两条直角边标上长度（横边在下、竖边在外侧）。"""
        t = self.theme
        k, x = self._point(p)
        f = self.fns[k]
        ax, ay = x, f(x)
        bx = x + run
        by = f(bx)
        if not all(math.isfinite(v) for v in (ay, by)):
            return []
        color = t.color(role, t.C)
        ink = t.ink(role)
        style = Style(stroke=color, stroke_width=4)
        out: list[Node] = [
            PathNode(path=line(*self._s(ax, ay), *self._s(bx, ay)), style=style, name="run"),
            PathNode(path=line(*self._s(bx, ay), *self._s(bx, by)), style=style, name="rise"),
        ]
        if labels:
            rise = by - ay
            size = self._sz(self.LABEL * 0.9)
            lab_run = number(run, size, ink, trimmed=True)
            b = lab_run.bbox()
            below = rise >= 0  # 横边在三角形下沿时标在下面，否则标在上面
            cx, cy = self._s((ax + bx) / 2, ay)
            dx = cx - b.center[0]
            if self.opts.get("min_text") and b.width + 0.12 > abs(run) * self.u:
                # v2 抬大了字号、横边又短（run 很小）：数字比横边宽，居中会伸到 P 的另一侧被直线穿过；
                # 改成从 P 这一端往 run 的方向排（直线在 P 的另一侧）
                start = ax * self.u + (0.06 if run >= 0 else -0.06)
                dx = (start - b.x0) if run >= 0 else (start - b.x1)
            lab_run.shift([dx, cy - (b.y1 + 0.12 if below else b.y0 - 0.12)])
            lab_rise = number(rise, size, ink, trimmed=True)
            b = lab_rise.bbox()
            sx, sy = self._s(bx, (ay + by) / 2)
            side = 1.0 if run >= 0 else -1.0  # 竖边外侧
            if abs(rise) * self.u < b.height * 1.2:  # 竖边几乎没有长度（k≈0）：标在横边右端的上方，别压在线上
                sy = ay * self.u + 0.18 + b.height / 2
            lab_rise.shift([sx + side * (0.14 + b.width / 2) - b.center[0], sy - b.center[1]])
            out += [lab_run, lab_rise]
        return out

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
        self.axis_lines = [PathNode(path=line(*self._s(x0, ax_y), *self._s(x1, ax_y)), style=style),
                           PathNode(path=line(*self._s(ax_x, y0), *self._s(ax_x, y1)), style=style)]
        g.add(*self.axis_lines)
        a = 0.14
        g.add(self._keep(self._s(x1, ax_y), PathNode(path=polygon([(a, 0), (-a * 0.6, a * 0.55), (-a * 0.6, -a * 0.55)]),
                                                      style=Style(fill=t.DIM))))
        g.add(self._keep(self._s(ax_x, y1), PathNode(path=polygon([(0, a), (a * 0.55, -a * 0.6), (-a * 0.55, -a * 0.6)]),
                                                      style=Style(fill=t.DIM))))
        if axis_labels:
            lx = mmath(axis_labels[0], self._sz(self.LABEL), t.DIM)
            lx.move_to([0.0, 0.0]).shift([0.12 + lx.width / 2, -0.28])
            ly = mmath(axis_labels[1], self._sz(self.LABEL), t.DIM)
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
                        lab = mmath(f"{v:g}", self._sz(self.LABEL * 0.8), t.DIM)
                        lab.move_to([0.0, -0.32] if axis == "x" else [-0.3, 0.0])
                        kids.append(lab)
                    slot = self._keep(pos, *kids)
                    if tick_labels:
                        self.tick_slots.append((slot, lab))
                    g.add(slot)
        return g

    def _plot_label(self, f: Function, tex: str, color: str, dom) -> Node:
        """曲线的名字放在右端最后一个可见点的旁边。"""
        (y0, y1) = self.yr
        runs = visible_runs(f, float(dom[0]), float(dom[1]), y0, y1)
        x = runs[-1][1] if runs else float(dom[1])
        y = f(x)
        lab = mmath(tex, self._sz(self.LABEL), color)
        right = np.array([0.15 + lab.width / 2, -0.05])
        if y > y1 - 0.08 * (y1 - y0):  # 顶到上沿：名字放在线的右下
            right[1] -= 0.35
        # 候选：右边，往下挪一两档，端点正上方，端点左边（被线穿过时 _declutter 依次往后试）
        spots = [right, right - [0.0, 0.4], right - [0.0, 0.8], np.array([0.0, 0.12 + lab.height / 2]),
                 np.array([-right[0], right[1]])]
        lab.move_to(spots[0])
        g = self._keep(self._s(x, y), lab, name="plot_label")
        self.plot_labels.append((g, lab, spots))
        return g

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
        lab = None
        if label:
            lab = mmath(label, self._sz(self.LABEL), color)
            v = np.array(LABEL_DIRS[direction] if isinstance(direction, str) else direction, dtype=float)
            v /= np.linalg.norm(v)
            lab.move_to(v * self._reach(lab, v))
            kids.append(lab)
        g = self._keep(self._s(*at), *kids, name=name)
        g.z = 3
        if lab is not None:
            self.marker_labels.append({"group": g, "label": lab, "angle": math.atan2(v[1], v[0]), "at": None})
        return g, dot

    @staticmethod
    def _reach(lab: Node, v) -> float:
        """名字中心离点多远：点的半径加上名字在 v 方向上的半宽。"""
        return 0.16 + 0.5 * math.hypot(lab.width * v[0], lab.height * v[1])

    def _line_path(self, point: tuple[float, float], direction: tuple[float, float]) -> Path:
        seg = clip_line(point, direction, self.box)
        if seg is None:
            return Path()
        (ax, ay), (bx, by) = seg
        return line(*self._s(ax, ay), *self._s(bx, by))

    def _line_node(self, point, direction, color: str, width: float, name: str) -> PathNode:
        return PathNode(path=self._line_path(point, direction), style=Style(stroke=color, stroke_width=width), z=1, name=name)

    # ---- 文字避让 ----

    def _lines(self) -> tuple[np.ndarray, np.ndarray]:
        """组件里所有线段（局部坐标），按 Path 对象缓存折线。"""
        cache = self.__dict__.setdefault("_flat", {})
        nodes: list[PathNode] = [*self.axis_lines]
        for key in ("plot", "secant", "tangent", "line"):
            nodes += self.parts[key]
        for tri in self.parts["triangle"]:
            nodes += [c for c in tri.children if isinstance(c, PathNode)]
        starts, ends = [], []
        for n in nodes:
            path = n.path
            if path is None or path.is_empty():
                continue
            hit = cache.get(id(n))
            if hit is None or hit[0] is not path:
                hit = (path, segments_of(*flatten(path, n.matrix)))
                cache[id(n)] = hit
            starts.append(hit[1][0])
            ends.append(hit[1][1])
        if not starts:
            return np.zeros((0, 2)), np.zeros((0, 2))
        return np.vstack(starts), np.vstack(ends)

    @staticmethod
    def _box(node: Node, parent: np.ndarray) -> tuple[float, float, float, float] | None:
        b = node.bbox(parent)
        return None if b is None else (b.x0, b.y0, b.x1, b.y1)

    @staticmethod
    def _meets(a, b, pad: float = 0.0) -> bool:
        return a[0] - pad < b[2] and b[0] - pad < a[2] and a[1] - pad < b[3] and b[1] - pad < a[3]

    def _declutter(self) -> None:
        a, b = self._lines()

        def clear(box, taken) -> bool:
            grown = (box[0] - CLEAR, box[1] - CLEAR, box[2] + CLEAR, box[3] + CLEAR)
            return not crosses(a, b, grown) and not any(self._meets(box, t, CLEAR) for t in taken)

        taken = []
        for tri in self.parts["triangle"]:
            taken += [bx for c in tri.children if not isinstance(c, PathNode) and (bx := self._box(c, tri.matrix))]
        for g, lab, spots in self.plot_labels:
            for spot in spots:
                lab.move_to(spot)
                if clear(self._box(lab, g.matrix), taken):
                    break
            else:
                lab.move_to(spots[0])
            taken.append(self._box(lab, g.matrix))
        for m in self.marker_labels:
            g, lab = m["group"], m["label"]
            at = g.matrix[:2, 2].copy()
            if m["at"] is not None and not np.allclose(at, m["at"]):
                if clear(self._box(lab, g.matrix), taken):  # 点在动：当前位置还空着就不挪
                    m["at"] = at
                    taken.append(self._box(lab, g.matrix))
                    continue
            m["at"] = at
            best = None
            for angle in AROUND + [m["angle"]]:
                turn = abs((angle - m["angle"] + math.pi) % (2 * math.pi) - math.pi)
                v = np.array([math.cos(angle), math.sin(angle)])
                for k, f in enumerate(REACH):
                    cost = turn / math.pi + 0.6 * k
                    if best is not None and cost >= best[0]:
                        break
                    lab.move_to(v * self._reach(lab, v) * f)
                    if clear(self._box(lab, g.matrix), taken):
                        best = (cost, v * self._reach(lab, v) * f)
                        break
            if best is None and self.opts.get("min_text"):
                # v2 抬大了字号：几条线交在点上（|x| 的尖点、两条割线、坐标轴）时近处的空当放不下，再往远处找
                for f in REACH_FAR:
                    for angle in [m["angle"]] + AROUND:
                        v = np.array([math.cos(angle), math.sin(angle)])
                        lab.move_to(v * self._reach(lab, v) * f)
                        if clear(self._box(lab, g.matrix), taken):
                            best = (0.0, v * self._reach(lab, v) * f)
                            break
                    if best is not None:
                        break
            v = np.array([math.cos(m["angle"]), math.sin(m["angle"])])
            lab.move_to(best[1] if best else v * self._reach(lab, v))
            taken.append(self._box(lab, g.matrix))
        for slot, lab in self.tick_slots:
            box = self._box(lab, slot.matrix)
            shown = clear(box, taken)
            slot.children = [slot.children[0], lab] if shown else [slot.children[0]]

    # ---- 割线 ----

    def set_h(self, i: int, h: float) -> None:
        seg, q, readout, info = self.secants[i]
        k, x = self._point(info["point"]) if hasattr(self, "point_x") else self.points[info["point"]]
        f = self.fns[k]
        self.h[i] = h
        qx = x + h
        q.matrix = T.translate(*self._s(qx, f(qx)))
        slope = secant_slope(f, x, h)
        seg.path = self._line_path((x, f(x)), (1.0, slope))  # 每次换一个新的 Path：渲染器按对象缓存
        if readout is not None:
            readout.children = [self._readout_text(i, f"{info['prefix']} {slope:.2f}", info["ink"])]
        if getattr(self, "ready", False):
            self._declutter()

    def _readout_text(self, i: int, text: str, color: str) -> Node:
        """读数文字按 (第几条割线, 文字) 缓存：同一个节点不会同时挂在两处。"""
        cache = self.__dict__.setdefault("_readouts", {})
        key = (i, text)
        node = cache.get(key)
        if node is None:
            node = mtext(text.replace("-", "−"), self._sz(24), color)
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
