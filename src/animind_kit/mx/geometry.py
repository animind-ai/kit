r"""综合几何图（墨线实现）：竞赛平面几何的证明图。点由构造算出来，不手摆；整支视频里图不动。

构造求解器和检查在 scene-spec 的 geometry.py（scene_spec.geometry.solve），这里用同一份代码算坐标，
所以 `scene-spec check` 验过的图和渲染出来的图一模一样。没有坐标轴、没有网格。

## 写法

```yaml
fig:
  widget: geometry
  in: stage                # 和题卡并排时写 left_of / right_of，或放进 style.side: column 的一栏
  box: [-3, -3, 3, 3]      # 可选：图框（数学坐标）。直线、射线裁在框里；不写时取所有点和圆的包围盒外扩 0.6
  points:                  # 名字: 定义。名字只用字母数字下划线（选择器 fig.A 要用）
    O: [0, 0]                                   # 坐标（只给"自由"的起始点用）
    A: {on: c, angle: 110}                      # 圆 c 上、从圆心看方向角 110°（度，逆时针，0 指向右）
    P: {on: [A, B], t: 0.3}                     # 线段上：A + t·(B − A)
    M: {mid: [B, C]}                            # 中点
    H: {foot: A, to: [B, C]}                    # 垂足（to 是一条直线，写法见下）
    A1: {reflect: A, over: [B, C]}              # 关于直线对称；over: O 时关于点对称
    X: {meet: [[A, M], [B, N]]}                 # 两直线交点
    D: {meet: [[A, O], c], not: A}              # 直线和圆的交点（两个根，必须说取哪个）
    E: {meet: [c, w], near: B}                  # 两圆交点
    O1: {circumcenter: [A, B, C]}               # 外心；另有 incenter 内心、centroid 重心、orthocenter 垂心
    T: {tangent: P, circle: c, index: 0}        # 从 P 向圆 c 作切线的切点
    K: {meet: [{tangent_at: B, circle: c}, {tangent_at: C, circle: c}]}   # 两条切线的交点
  circles:
    c: {center: O, radius: 2.4}                 # 另有 {center: O, through: A}、{through: [A, B, C]}、{diameter: [A, B]}
  draw:                                         # 线、角、记号（画的先后不影响层次：角和填色垫在线下面，点在最上面）
    - segment: [A, B]                           # 名字默认 AB
    - line: [A, B]                              # 名字默认 line_AB；两端裁在图框上
    - ray: [A, B]                               # 从 A 出发经过 B，名字默认 ray_AB
    - polygon: [A, B, C]                        # 名字默认 ABC；fill: true 一开始就涂色
    - arc: {circle: c, from: B, to: C}          # 从 B 逆时针到 C（ccw: false 顺时针），名字默认 arc_BC
    - angle: [B, A, C]                          # ∠BAC（顶点在中间），名字默认 ang_BAC；90° 自动画直角方块
    - tick: [A, M]                              # AM 中点上的等长记号，count: 1/2/3 道
  assert:                                       # 关键结论，检查器和 kit 都按 1e-6 验算，不成立就报错（SS273）
    - concyclic: [A, B, C, D]
    - collinear: [A, O, D]
    - equal: [[A, B], [A, C]]                   # 线段等长（可以多于两条）
    - equal_angle: [[B, A, D], [D, A, C]]       # 角相等（不带方向，0°…180°）
    - parallel: [[A, B], [C, D]]
    - perpendicular: [[A, H], [B, C]]
    - on: [P, c]                                # 点在圆上
    - concurrent: [[A, M], [B, N], [C, L]]      # 三线共点
```

直线（to、over、meet 里、draw 的 line）写成 `[A, B]`，或 `{through: P, perp: [A, B]}`（过 P 垂直 AB）、
`{through: P, parallel: [A, B]}`、`{tangent_at: A, circle: c}`（过圆上一点的切线）。
两个交点时取哪一个（meet、tangent）：`not: A`（不是 A 的那个，"第二个交点"最常用）、`near: P` / `far: P`、
`index: 0 / 1`（直线和圆：沿直线方向的先后；两圆：从第一个圆心看第二个圆心，左边的是 0；切点：从点看圆心，左边的是 0）。
少写会报 SS272，不会默默取一个。

每个点、圆、draw 项可以加的选项：
- role：主题角色 FG / A / B / C / HL / NO / DIM（线和字用深色，填色用本色）；
- aux: true：新添的辅助线，默认暗红（role NO）；要赤金写 role: HL；
- hidden: true：一开始不画，之后用 `show fig.名字`（淡入）或组件动作 `draw`（沿线画出来）露出来；
- dashed: true（线、圆）；width：线宽（1080p 像素，默认 4）；
- 点：label（公式，不加 $；默认是名字，Ma → M_a、A1 → A_1；空串不写名字）、dir（名字放哪边：u d l r ul ur dl dr，
  只是首选，被线穿过时自动换到附近的空位）、dot: false（只写名字不画点）；
- angle：radius（数学单位）、arcs: 1/2/3（等角记号）、right: true/false（不写时 90° 自动画方块）、fill: true、label（写在角平分线上）；
- segment：label（写在中点旁边，例如边长 a）；
- circle：draw: false（只用来构造，不画出来）；
- 点也可以写 draw: false：只参与构造（例如题设里的辅助点在图框外），不画、不算进图框、不报 SS275，断言照样能用。

## 动作

内置动作直接作用在元素上（每个元素都是一个部件）：`show fig`（整张图，hidden 的除外）、`show fig.AB`（淡入，不平移）、
`hide fig.AB`、`mark fig.A`、`focus fig.ABC`（把它描成强调色、其余调淡，`unfocus fig` 复原）。
先 `show fig` 再显示部件：图还没出现时直接 `show fig.AB`，kit 会只亮出 AB、把其余元素都藏起来（内置 show 对部件的通用做法）。

组件动作（`act: 名字, target: fig, args: {...}`；name 都可以是一个名字或名字列表）：
- draw {name}：沿线画出来（线段从第一个点画起，圆转一圈，角的弧从第一条边扫到第二条边）；点和名字淡入；
- remove {name}：淡出；
- shade {name, role}：给角（扇形）、多边形、圆涂色，默认 HL（角追时把用到的角涂上）；unshade {name} 去掉；
- ring {name, role}：点外面套一个圈，线、圆、角下面垫一道粗的半透明强调色（像荧光笔），默认 HL；unring {name}；
- highlight {name, role}：线和点换成强调色（默认 HL）；
- dim {name} 或 dim {except}：调淡这些 / 除了这些都调淡（没露出来的不会被调出来）；
- restore {name}：颜色和透明度复原（不写 name = 全部）；
- trace {point, to}：反例 / 动态场景专用。圆上的点 {on: c, angle} 转到角 to，线段上的点 {on: [A, B], t} 滑到 t；
  依赖它的点、线、角、记号逐帧重算（名字和点的相对位置不变），检查器沿途取样验证断言一直成立。
  证明图本身不要用：学生要的是安静的画面。

## 版式和检查

整张图按 box 预留位置（名字向外多留 0.55），排版按它缩放；点的名字字号守 label 字号下限（后端按缩放抬字号重建）。
名字垫一块底色、画在所有线上面，并在 16 个方向、三档距离里找不压线、不压别的名字的位置（同 graph 的 _declutter），
inspect 的 cross / overlap 不会报。建组件时再验一遍 assert：不成立直接报错（inspect 看得到），不会渲染出错图。
"""

from __future__ import annotations

import math
import re

import numpy as np
from moxian.anim import Animation, Draw, Restyle, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import Path, circle, polygon, rounded_rect
from scene_spec import geometry as G

from ..lint import crosses, segments_of
from .dots import FadeTo, reserve
from .scan import flatten
from .text import label_text
from .widgets import Built, Widget

SIZE = 6.0  # 图框较长的一边画成多少画面单位（排版前）；用户的坐标单位随意，画出来大小一致
DOT = 0.075  # 点的半径（画面单位）
LINE_W = 4  # 线宽（1080p 像素）
MARK_W = 3  # 角弧、等长记号的线宽
HALO_W = 16  # ring 垫的荧光笔宽度
PAD = 0.05  # 名字底色比字大出的边
CLEAR = 0.05  # 名字离线至少这么远
ANGLE_R = 0.42  # 角弧默认半径（画面单位）
ARC_GAP = 0.085  # 等角记号多道弧的间距
TICK_LEN = 0.24  # 等长记号短线长
TICK_GAP = 0.08
LABEL_ROOM = 0.55  # 图框外给名字留的边
AROUND = [math.pi * k / 8 for k in range(16)]
REACH = (1.0, 1.4, 1.9)
LABEL_DIRS = {
    "u": math.pi / 2, "d": -math.pi / 2, "l": math.pi, "r": 0.0,
    "ul": 3 * math.pi / 4, "ur": math.pi / 4, "dl": -3 * math.pi / 4, "dr": -math.pi / 4,
}
Z_ORDER = {"polygon": 0, "angle": 1, "circle": 2, "arc": 3, "line": 4, "ray": 4, "segment": 5, "tick": 6, "point": 7}
_SUB = re.compile(r"^([A-Za-z])_?([A-Za-z0-9]+)$")


def default_label(name: str) -> str:
    """Ma → M_a、A1 → A_1、O' 写不出来（名字里不能有撇），要撇时写 label: "A'"。"""
    m = _SUB.match(name)
    if len(name) > 1 and m and not name.isupper():
        return f"{m.group(1)}_{{{m.group(2)}}}"
    if len(name) > 1 and m and name[1:].isdigit():
        return f"{m.group(1)}_{{{m.group(2)}}}"
    return name


def arc_path(c: np.ndarray, r: float, a0: float, sweep: float, path: Path | None = None) -> Path:
    """圆弧（三次贝塞尔，每段不超过 45°）；sweep 带正负（逆时针为正）。"""
    path = path if path is not None else Path()
    n = max(1, math.ceil(abs(sweep) / (math.pi / 4)))
    h = sweep / n
    k = 4 / 3 * math.tan(h / 4)
    p = c + r * np.array([math.cos(a0), math.sin(a0)])
    path.move_to(*p)
    for i in range(n):
        a, b = a0 + i * h, a0 + (i + 1) * h
        pa = c + r * np.array([math.cos(a), math.sin(a)])
        pb = c + r * np.array([math.cos(b), math.sin(b)])
        ta = np.array([-math.sin(a), math.cos(a)])
        tb = np.array([-math.sin(b), math.cos(b)])
        c1, c2 = pa + ta * r * k, pb - tb * r * k
        path.cubic_to(c1[0], c1[1], c2[0], c2[1], pb[0], pb[1])
    return path


def dash_polyline(pts: list[np.ndarray], dash: float = 0.16, gap: float = 0.1) -> Path:
    """折线按弧长切成虚线（圆、圆弧的虚线用）。"""
    out = Path()
    on, left, pen = True, dash, False
    for p, q in zip(pts, pts[1:]):
        seg = q - p
        length = float(np.linalg.norm(seg))
        s = 0.0
        while s < length - 1e-12:
            step = min(left, length - s)
            a, b = p + seg * (s / length), p + seg * ((s + step) / length)
            if on:
                if not pen:
                    out.move_to(*a)
                    pen = True
                out.line_to(*b)
            s += step
            left -= step
            if left <= 1e-12:
                on = not on
                left = dash if on else gap
                pen = False
    return out


def clip_line(p: np.ndarray, d: np.ndarray, box, ray: bool = False):
    """直线 p + s·d（射线时 s ≥ 0）裁在 box = (x0, y0, x1, y1) 里；不相交时 None。"""
    lo, hi = (0.0 if ray else -math.inf), math.inf
    for k, (a, b) in enumerate(((box[0], box[2]), (box[1], box[3]))):
        if abs(d[k]) < 1e-12:
            if not a - 1e-9 <= p[k] <= b + 1e-9:
                return None
            continue
        s0, s1 = (a - p[k]) / d[k], (b - p[k]) / d[k]
        lo, hi = max(lo, min(s0, s1)), min(hi, max(s0, s1))
    if hi - lo < 1e-9:
        return None
    return p + lo * d, p + hi * d


class _Late(Animation):
    """把一个短动画放到一段时间的末尾播（线画好以后名字再出现）。"""

    def __init__(self, anim: Animation, total: float):
        super().__init__(Node(), total, easing.linear)
        self.anim, self.start, self.started = anim, max(0.0, 1.0 - anim.duration / total), False

    def interpolate(self, alpha: float) -> None:
        if alpha < self.start:
            return
        if not self.started:
            self.anim.begin()
            self.started = True
        self.anim.interpolate(self.anim.ease(min(1.0, (alpha - self.start) / max(1e-6, 1.0 - self.start))))

    def finish(self) -> None:
        self.interpolate(1.0)


class _IfShown(Animation):
    """只在播放开始时节点看得见（透明度 > 0）才做的样式渐变：dim / restore 不会把没露出来的元素调出来。"""

    def __init__(self, node: Node, duration: float, **target):
        super().__init__(node, duration, easing.smooth)
        self.inner = Restyle(node, duration, **target)
        self.active = False

    def begin(self) -> None:
        self.active = (self.node.style or Style()).opacity > 0.01
        if self.active:
            self.inner.begin()

    def interpolate(self, alpha: float) -> None:
        if self.active:
            self.inner.interpolate(alpha)


class _Trace(Animation):
    """自由点的参数从 start 渐变到 end，每帧重解整张图、重画所有元素。"""

    def __init__(self, geo: Geometry, point: str, start: float, end: float, duration: float = 2.0):
        super().__init__(Node(), duration, easing.smooth)
        self.geo, self.point, self.start, self.end = geo, point, start, end

    def interpolate(self, alpha: float) -> None:
        self.geo.overrides[self.point] = self.start + (self.end - self.start) * alpha
        self.geo.repaint(G.solve(self.geo.definition, self.geo.overrides))


class Part:
    """一个元素：组节点（部件，show / hide 作用在它上面）+ 描线的子节点 + 可选的填色、名字。"""

    def __init__(self, el: G.Element, node: Group):
        self.el, self.node = el, node
        self.strokes: list[PathNode] = []  # 线、弧、记号（highlight 改它们的颜色）
        self.dots: list[PathNode] = []  # 点（填色）
        self.fill: PathNode | None = None  # 角扇形、多边形内部、圆盘（shade）
        self.label: Group | None = None
        self.offset: np.ndarray | None = None  # 名字中心相对锚点的位置（trace 时保持）
        self.halo: Group | None = None


class Geometry(Widget):
    name = "geometry"
    LABEL = 34  # 点名字的默认字号（kit 字号）；opts["label_size"] / definition label_size 覆盖
    label_floor = "label"
    calm = True  # 内置 show 显示部件时不带入场位移（几何图要安静）

    def build(self) -> Built:
        d = self.definition
        self.acted = False
        self.label_size = float(self.opts.get("label_size", d.get("label_size", self.LABEL)))
        try:
            self.fig = G.solve(d)
            self.els = G.elements(d)
            for e in self.els:
                G.element_points(self.fig, d, e)
        except G.GeoError as err:
            raise ValueError(f"几何图 {self.obj_id}：{err.path}：{err.msg}") from None
        bad = G.check_asserts(self.fig, d)
        if bad:
            raise ValueError(f"几何图 {self.obj_id} 的断言不成立：" + "；".join(f"{p}：{m}" for p, m in bad))
        self.box = G.figure_box(self.fig, d, self.els)
        x0, y0, x1, y1 = self.box
        self.u = SIZE / max(x1 - x0, y1 - y0)
        self.center = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        self.overrides: dict[str, float] = {}
        node = Group(name=self.obj_id)
        w, h = (x1 - x0) * self.u, (y1 - y0) * self.u
        node.add(reserve(w + 2 * LABEL_ROOM, h + 2 * LABEL_ROOM))
        self.halos = Group(name="halos")  # ring 的荧光笔垫在所有线下面
        node.add(self.halos)
        self.parts: dict[str, Part] = {}
        for e in sorted(self.els, key=lambda e: Z_ORDER[e.kind]):  # sorted 是稳定的：同类按写的先后
            part = self._make(e)
            self.parts[e.name] = part
            node.add(part.node)
        self.repaint(self.fig)
        self._place_labels()
        parts = {name: [p.node] for name, p in self.parts.items()}
        return Built(node, parts=parts, items=[p.node for p in self.parts.values()])

    # ---------- 坐标、颜色 ----------

    def W(self, p) -> np.ndarray:  # noqa: N802
        return (np.asarray(p, dtype=float) - self.center) * self.u

    def role(self, e: G.Element) -> str | None:
        return e.opts.get("role") or ("NO" if e.opts.get("aux") else None)

    def stroke_color(self, e: G.Element) -> str:
        return self.theme.ink(self.role(e))

    def fill_color(self, e: G.Element, role: str | None = None) -> str:
        return self.theme.color(role or self.role(e) or "HL")

    # ---------- 建元素 ----------

    def _make(self, e: G.Element) -> Part:
        t = self.theme
        node = Group(name=e.name)
        part = Part(e, node)
        if e.opts.get("hidden"):
            node.set_style(opacity=0.0)
        width = float(e.opts.get("width", MARK_W if e.kind in ("angle", "tick") else LINE_W))
        color = self.stroke_color(e)
        if e.kind in ("angle", "polygon", "circle"):
            filled = bool(e.opts.get("fill"))
            part.fill = PathNode(path=Path(), style=Style(fill=self.fill_color(e), fill_opacity=t.fill_opacity(),
                                                          opacity=1.0 if filled else 0.0))
            node.add(part.fill)
        if e.kind == "point":
            if e.opts.get("dot", True):
                dot = PathNode(path=circle(DOT), style=Style(fill=t.ink(self.role(e))))
                part.dots.append(dot)
                node.add(dot)
        else:
            stroke = PathNode(path=Path(), style=Style(stroke=color, stroke_width=width))
            part.strokes.append(stroke)
            node.add(stroke)
        text = e.opts.get("label", default_label(e.name) if e.kind == "point" else None)
        if text:
            body = label_text(text, self.label_size, t.ink(self.role(e)), t)
            body.font_floor = self.label_floor
            bg = PathNode(path=rounded_rect(body.width + 2 * PAD, body.height + 2 * PAD, 0.05),
                          style=Style(fill=t.BG, fill_opacity=0.92))
            bg.move_to(body.center())
            inner = Group(bg, body)
            inner.matrix = T.translate(*(-inner.center()))  # 中心在局部原点，摆放时只改平移
            part.label = Group(inner)
            node.add(part.label)
        return part

    # ---------- 按解出的图重画 ----------

    def repaint(self, fig: G.Figure) -> None:
        """按 fig 重画每个元素的路径；名字按记下的偏移跟着锚点走（偏移在 _place_labels 里定）。"""
        self.fig_now = fig
        for part in self.parts.values():
            self._paint(part, fig)
            if part.label is not None and part.offset is not None:
                part.label.matrix = T.translate(*(self._anchor(part, fig) + part.offset))

    def _paint(self, part: Part, fig: G.Figure) -> None:
        e = part.el
        P = lambda n: self.W(fig.points[n])  # noqa: E731
        dashed = bool(e.opts.get("dashed"))
        box = tuple(float(v) for v in ((np.array(self.box[:2]) - self.center) * self.u).tolist()
                    + ((np.array(self.box[2:]) - self.center) * self.u).tolist())
        if e.kind == "point":
            for dot in part.dots:
                dot.matrix = T.translate(*P(e.name))
            return
        stroke = part.strokes[0]
        if e.kind in ("segment", "line", "ray"):
            if e.kind == "segment":
                ends = (P(e.spec[0]), P(e.spec[1]))
            else:
                ln = G.line_of(fig, self.definition, e.spec, e.path)
                p, dvec = self.W(ln.p), np.array(ln.d, dtype=float)
                if e.kind == "ray":
                    p = P(e.spec[0])
                    dvec = P(e.spec[1]) - p
                ends = clip_line(p, dvec, box, ray=e.kind == "ray") or (p, p)
            stroke.path = self._line(ends[0], ends[1], dashed)
        elif e.kind == "polygon":
            pts = [P(n) for n in e.spec]
            stroke.path = dash_polyline(pts + [pts[0]]) if dashed else polygon([tuple(p) for p in pts])
            part.fill.path = polygon([tuple(p) for p in pts])
        elif e.kind == "circle":
            c = fig.circles[e.name]
            cc, r = self.W(c.c), c.r * self.u
            if dashed:
                pts = [cc + r * np.array([math.cos(a), math.sin(a)]) for a in np.linspace(0, 2 * math.pi, 241)]
                stroke.path = dash_polyline(pts)
            else:
                stroke.path = arc_path(cc, r, 0.0, 2 * math.pi)
            part.fill.path = circle(r).transformed(T.translate(*cc))
        elif e.kind == "arc":
            s = e.spec
            c = fig.circles[s["circle"]]
            cc, r = self.W(c.c), c.r * self.u
            a0 = math.atan2(*(P(s["from"]) - cc)[::-1])
            a1 = math.atan2(*(P(s["to"]) - cc)[::-1])
            ccw = e.opts.get("ccw", True)
            sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
            if dashed:
                pts = [cc + r * np.array([math.cos(a0 + sweep * k / 120), math.sin(a0 + sweep * k / 120)]) for k in range(121)]
                stroke.path = dash_polyline(pts)
            else:
                stroke.path = arc_path(cc, r, a0, sweep)
        elif e.kind == "tick":
            a, b = P(e.spec[0]), P(e.spec[1])
            m, u = (a + b) / 2, (b - a) / np.linalg.norm(b - a)
            n = np.array([-u[1], u[0]])
            path = Path()
            count = int(e.opts.get("count", 1))
            for k in range(count):
                c = m + u * TICK_GAP * (k - (count - 1) / 2)
                p, q = c - n * TICK_LEN / 2, c + n * TICK_LEN / 2
                path.move_to(*p).line_to(*q)
            stroke.path = path
        elif e.kind == "angle":
            self._paint_angle(part, fig)

    def _line(self, a: np.ndarray, b: np.ndarray, dashed: bool) -> Path:
        if dashed:
            return dash_polyline([a, b])
        return Path().move_to(*a).line_to(*b)

    def angle_geom(self, e: G.Element, fig: G.Figure) -> tuple[np.ndarray, float, float, float, bool]:
        """角的 (顶点, 半径, 起始方向角, 转过的角（带符号，|·| < π）, 是否画直角方块)。"""
        b, a, c = (self.W(fig.points[n]) for n in e.spec)
        u, v = b - a, c - a
        a0 = math.atan2(u[1], u[0])
        sweep = math.atan2(u[0] * v[1] - u[1] * v[0], float(np.dot(u, v)))
        shortest = min(np.linalg.norm(u), np.linalg.norm(v))
        r = float(e.opts["radius"]) * self.u if "radius" in e.opts else min(ANGLE_R, 0.38 * shortest)
        right = e.opts.get("right")
        if right is None:
            right = abs(abs(sweep) - math.pi / 2) < G.TOL
        return a, r, a0, sweep, bool(right)

    def _paint_angle(self, part: Part, fig: G.Figure) -> None:
        e = part.el
        a, r, a0, sweep, right = self.angle_geom(e, fig)
        if right:
            s = r * 0.72
            u = np.array([math.cos(a0), math.sin(a0)])
            v = np.array([math.cos(a0 + sweep), math.sin(a0 + sweep)])
            part.strokes[0].path = Path().move_to(*(a + u * s)).line_to(*(a + (u + v) * s)).line_to(*(a + v * s))
            part.fill.path = polygon([tuple(a), tuple(a + u * s), tuple(a + (u + v) * s), tuple(a + v * s)])
            return
        path = Path()
        for k in range(int(e.opts.get("arcs", 1))):
            arc_path(a, r + k * ARC_GAP, a0, sweep, path)
        part.strokes[0].path = path
        fill = Path().move_to(*a)
        fill.line_to(*(a + r * np.array([math.cos(a0), math.sin(a0)])))
        n = max(1, math.ceil(abs(sweep) / (math.pi / 4)))
        h = sweep / n
        k4 = 4 / 3 * math.tan(h / 4)
        for i in range(n):
            x, y = a0 + i * h, a0 + (i + 1) * h
            pa = a + r * np.array([math.cos(x), math.sin(x)])
            pb = a + r * np.array([math.cos(y), math.sin(y)])
            c1 = pa + np.array([-math.sin(x), math.cos(x)]) * r * k4
            c2 = pb - np.array([-math.sin(y), math.cos(y)]) * r * k4
            fill.cubic_to(c1[0], c1[1], c2[0], c2[1], pb[0], pb[1])
        fill.close()
        part.fill.path = fill

    # ---------- 名字 ----------

    def _anchor(self, part: Part, fig: G.Figure) -> np.ndarray:
        e = part.el
        if e.kind == "point":
            return self.W(fig.points[e.name])
        if e.kind == "angle":
            return self.angle_geom(e, fig)[0]
        if e.kind == "segment":
            return (self.W(fig.points[e.spec[0]]) + self.W(fig.points[e.spec[1]])) / 2
        return np.zeros(2)

    @staticmethod
    def _half(lab: Group) -> np.ndarray:
        b = lab.children[0].bbox()
        return np.array([b.width, b.height]) / 2

    def _place_labels(self) -> None:
        """名字避让：所有线（显示不显示都算，免得名字随显隐跳）当障碍；角和线段的名字先放，点的名字后放。"""
        a_all, b_all = [], []
        for part in self.parts.values():
            for s in part.strokes:
                curves, closed = flatten(s.path, np.eye(3))
                a, b = segments_of(curves, closed)
                a_all.append(a)
                b_all.append(b)
        A = np.vstack(a_all) if a_all else np.zeros((0, 2))
        B = np.vstack(b_all) if b_all else np.zeros((0, 2))
        dots = [self.W(self.fig.points[p.el.name]) for p in self.parts.values() if p.el.kind == "point" and p.dots]
        taken: list[tuple[float, float, float, float]] = [(d[0] - DOT, d[1] - DOT, d[0] + DOT, d[1] + DOT) for d in dots]
        pts = np.array([self.W(p) for p in self.fig.points.values()])
        middle = pts.mean(axis=0) if len(pts) else np.zeros(2)

        def box_at(c: np.ndarray, half: np.ndarray) -> tuple[float, float, float, float]:
            return (c[0] - half[0], c[1] - half[1], c[0] + half[0], c[1] + half[1])

        def clear(bx, own_dot: np.ndarray | None) -> bool:
            grown = (bx[0] - CLEAR, bx[1] - CLEAR, bx[2] + CLEAR, bx[3] + CLEAR)
            if crosses(A, B, grown):
                return False
            for tb in taken:
                if own_dot is not None and abs((tb[0] + tb[2]) / 2 - own_dot[0]) < 1e-9 and abs((tb[1] + tb[3]) / 2 - own_dot[1]) < 1e-9:
                    continue
                if tb[0] < grown[2] and tb[2] > grown[0] and tb[1] < grown[3] and tb[3] > grown[1]:
                    return False
            return True

        def reach(half: np.ndarray, v: np.ndarray, base: float) -> float:
            return base + float(np.dot(np.abs(v), half))

        order = sorted((p for p in self.parts.values() if p.label is not None), key=lambda p: p.el.kind == "point")
        for part in order:
            e = part.el
            half = self._half(part.label)
            anchor = self._anchor(part, self.fig)
            if e.kind == "angle":
                a, r, a0, sweep, _ = self.angle_geom(e, self.fig)
                mid = a0 + sweep / 2
                # 名字写在角里、弧外面；首选角平分线，被别的线挡住时偏向一边。角很尖时从楔形放得下这块字的距离起找
                fit = float(np.linalg.norm(half)) / max(math.sin(abs(sweep) / 2), 0.05)
                start = max(r + 0.1, fit + CLEAR - float(np.dot(np.abs([math.cos(mid), math.sin(mid)]), half)))
                cands = [(mid + f * sweep / 2, k) for k in range(6) for f in (0.0, 0.4, -0.4, 0.7, -0.7)]
                base = lambda k, s0=start: s0 + k * 0.12  # noqa: E731
                prefer, strict = mid, 1.5
            elif e.kind == "segment":
                p, q = (self.W(self.fig.points[n]) for n in e.spec)
                nrm = math.atan2(-(q - p)[0], (q - p)[1])
                out = nrm if np.dot([math.cos(nrm), math.sin(nrm)], anchor - middle) >= 0 else nrm + math.pi
                cands = [(ang, k) for k in range(3) for ang in (out, out + math.pi)]
                base = lambda k: 0.12 + k * 0.2  # noqa: E731
                prefer, strict = out, 1.0
            else:
                given = e.opts.get("dir")
                away = anchor - middle
                prefer = LABEL_DIRS[given] if given else (math.atan2(away[1], away[0]) if np.linalg.norm(away) > 1e-6 else math.pi / 4)
                strict = 2.0 if given else 1.0
                cands = [(ang, k) for k in range(len(REACH)) for ang in AROUND + [prefer]]
                base = lambda k: (DOT + 0.07) * REACH[k] + 0.05 * k  # noqa: E731
            best = None
            for ang, k in cands:
                turn = abs((ang - prefer + math.pi) % (2 * math.pi) - math.pi)
                cost = strict * turn / math.pi + 0.6 * k
                if best is not None and cost >= best[0]:
                    continue
                v = np.array([math.cos(ang), math.sin(ang)])
                c = anchor + v * reach(half, v, base(k))
                if clear(box_at(c, half), anchor if e.kind == "point" else None):
                    best = (cost, c)
            if best is None:
                v = np.array([math.cos(prefer), math.sin(prefer)])
                best = (0.0, anchor + v * reach(half, v, base(0)))
            part.offset = best[1] - anchor
            part.label.matrix = T.translate(*best[1])
            taken.append(box_at(best[1], half))

    # ---------- 动作 ----------

    def _names(self, value) -> list[Part]:
        if value is None:
            return list(self.parts.values())
        names = [value] if isinstance(value, str) else list(value)
        missing = [n for n in names if n not in self.parts]
        if missing:
            raise KeyError(f"几何图 {self.obj_id} 里没有 {'、'.join(missing)}")
        return [self.parts[n] for n in names]

    def action(self, act: str, args: dict) -> list[Animation]:
        self.acted = True
        t = self.theme
        if act == "draw":
            out: list[Animation] = []
            for part in self._names(args.get("name")):
                out.append(FadeTo(part.node, 1.0, 0.05))
                for s in part.strokes:
                    out.append(Draw(s, 0.9))
                for dot in part.dots:
                    dot.set_style(opacity=0.0)
                    out.append(FadeTo(dot, 1.0, 0.4))
                if part.label is not None:
                    part.label.set_style(opacity=0.0)
                    out.append(_Late(FadeTo(part.label, 1.0, 0.3), 0.9))
            return out
        if act == "remove":
            return [FadeTo(p.node, 0.0, 0.4) for p in self._names(args.get("name"))]
        if act in ("shade", "unshade"):
            out = []
            for part in self._names(args.get("name")):
                if part.fill is None:
                    raise KeyError(f"shade 只涂角、多边形和圆（{part.el.name} 是 {part.el.kind}）")
                if act == "shade":
                    if args.get("role"):
                        part.fill.style = part.fill.style.with_(fill=self.fill_color(part.el, args["role"]))
                    out += [FadeTo(part.node, 1.0, 0.3), FadeTo(part.fill, 1.0, 0.5)]
                else:
                    out.append(FadeTo(part.fill, 0.0, 0.4))
            return out
        if act in ("ring", "unring"):
            out = []
            for part in self._names(args.get("name")):
                if act == "unring":
                    if part.halo is not None:
                        out.append(FadeTo(part.halo, 0.0, 0.4))
                        part.halo = None
                    continue
                color = t.color(args.get("role") or "HL")
                if part.el.kind == "point":
                    halo = Group(PathNode(path=circle(DOT * 3.2).transformed(T.translate(*self.W(self.fig_now.points[part.el.name]))),
                                          style=Style(stroke=t.ink(args.get("role") or "HL"), stroke_width=LINE_W)))
                    part.node.add(halo)  # 点的圈画在点上面
                else:
                    halo = Group(*[PathNode(path=s.path.copy(), style=Style(stroke=color, stroke_width=HALO_W,
                                                                              stroke_opacity=0.45)) for s in part.strokes])
                    self.halos.add(halo)
                halo.set_style(opacity=0.0)
                part.halo = halo
                out += [FadeTo(part.node, 1.0, 0.3), FadeTo(halo, 1.0, 0.4)]
            return out
        if act == "highlight":
            return self._recolor(self._names(args.get("name")), args.get("role") or "HL")
        if act == "dim":
            if "except" in args:
                keep = {p.el.name for p in self._names(args["except"])}
                parts = [p for p in self.parts.values() if p.el.name not in keep]
            else:
                parts = self._names(args.get("name"))
            return [_IfShown(p.node, 0.4, opacity=0.25) for p in parts]
        if act == "restore":
            return self._restore(self._names(args.get("name")))
        if act == "trace":
            name = args["point"]
            kind = self.fig.free.get(name)
            if kind is None:
                raise KeyError(f"trace 的点 {name} 不是圆上 / 线段上的自由点")
            pdef = self.definition["points"][name]
            start = self.overrides.get(name, float(pdef["angle" if kind == "angle" else "t"]))
            end = float(args["to"])
            self.overrides[name] = end  # 建动作时推演：之后的动作看到的是滑完的位置
            self.fig_now = G.solve(self.definition, self.overrides)
            return [_Trace(self, name, start, end, duration=2.0)]
        raise KeyError(f"组件 {self.name} 没有动作 {act}")

    def _recolor(self, parts: list[Part], role: str | None) -> list[Animation]:
        t = self.theme
        out: list[Animation] = []
        for part in parts:
            ink = t.ink(role) if role else self.stroke_color(part.el)
            for s in part.strokes:
                out.append(Restyle(s, 0.4, stroke=ink))
            for dot in part.dots:
                out.append(Restyle(dot, 0.4, fill=t.ink(role) if role else t.ink(self.role(part.el))))
        return out

    def _restore(self, parts: list[Part]) -> list[Animation]:
        return self._recolor(parts, None) + [_IfShown(p.node, 0.4, opacity=1.0) for p in parts]

    def focus_part(self, part: str) -> list[Animation] | None:
        """内置 focus fig.X：X 描成强调色、其余看得见的调淡。"""
        if part not in self.parts:
            return None
        others = [p for p in self.parts.values() if p.el.name != part]
        return (self._restore([self.parts[part]]) + self._recolor([self.parts[part]], "HL")
                + [_IfShown(p.node, 0.4, opacity=0.3) for p in others])

    def focus(self, index: int | None) -> list[Animation] | None:
        if index is None:  # unfocus：全部复原
            return self._restore(list(self.parts.values()))
        return None
