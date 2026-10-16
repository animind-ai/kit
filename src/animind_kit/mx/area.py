"""面积模型（墨线实现）：勾股定理的"拼一拼"。

一个边长 a+b 的大正方形，里面放四个全等的直角三角形（直角边 a、b），有两种摆法：

- four_around_c：四个三角形各占一个角，中间空出一个斜放的正方形，边长 c；
- two_rectangles：三角形两两拼成两个 a×b 的长方形，空出边长 a 和边长 b 的两个正方形。

建模方式：每个三角形由"直角顶点 P、直角边 a 的方向 u"确定，直角边 b 的方向是 u 逆时针转 angle 度
（angle=90 就是直角三角形）。两种摆法之间每个三角形只做平移（u 不变），所以全等、总面积不变都是
构造上保证的；slide 时按"目标位置不和别的三角形重叠"的顺序一个一个挪。
非例：bend 把 angle 改成钝角（例如 115），直角边 b 绕直角顶点转开，三角形戳出大正方形、互相压住。

只给一个三角形（count: 1）时画一个单独的直角三角形，标出 a、b、c，用在开头的钩子里。

暴露的子元素：
- items / tri：四个三角形（可以 show pyth[0]、focus pyth.tri[1]）；
- hole：空白区域 [c², a², b²]，每项是填色 + 名字，一开始隐藏；
- area：空白区域的名字 [c², a², b²]（transform 到公式里的对应项时用它）；
- side：边长标注 [a, b, c]。
"""

from __future__ import annotations

import math

import numpy as np
from moxian.anim import Animation, Fade, Restyle, Shift, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import line, polygon

from .text import mmath
from .widgets import Built, Widget

LAYOUTS = ("four_around_c", "two_rectangles")
HOLES = ("c2", "a2", "b2")  # hole / area 的顺序
HOLE_ROLES = {"c2": "HL", "a2": "B", "b2": "C"}
HOLE_TEX = {"c2": "c^2", "a2": "a^2", "b2": "b^2"}
VALID_HOLES = {"four_around_c": ("c2",), "two_rectangles": ("a2", "b2")}

Point = tuple[float, float]


# ---------- 纯几何（大正方形占 [0, a+b]²，测试直接用这些函数）----------

def rotate(v: Point, degrees: float) -> Point:
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


def anchors(layout: str, a: float, b: float) -> list[tuple[Point, Point]]:
    """四个三角形的 (直角顶点 P, 直角边 a 的方向 u)，顺序固定：左下、右下、右上、左上起家的那四块。

    four_around_c 里四块是同一个三角形绕大正方形中心依次转 90°；
    two_rectangles 里每块只是平移：左下块上移 a，右上块左移 b，左上块移 (a, -b)，右下块不动。
    """
    s = a + b
    dirs = [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)]
    if layout == "four_around_c":
        corners = [(0.0, 0.0), (s, 0.0), (s, s), (0.0, s)]
    elif layout == "two_rectangles":
        corners = [(0.0, a), (s, 0.0), (a, s), (a, a)]
    else:
        raise KeyError(f"area_model 没有摆法 {layout!r}（有 {' / '.join(LAYOUTS)}）")
    return list(zip(corners, dirs))


def legs(u: Point, angle: float) -> tuple[Point, Point]:
    """两条"直角边"的方向。直角时就是 u 和 u 逆时针转 90°；夹角变成 angle 时两条边各向外转开一半，
    这样三角形是绕直角顶点对称地"撑开"，不会整块甩到一边去。"""
    half = (angle - 90.0) / 2
    u2 = rotate(u, -half)
    return u2, rotate(u2, angle)


def triangle(p: Point, u: Point, a: float, b: float, angle: float = 90.0) -> list[Point]:
    """顶点 [P, P + a·u', P + b·v']，(u', v') = legs(u, angle)。直角时 u' = u。"""
    u, v = legs(u, angle)
    return [p, (p[0] + a * u[0], p[1] + a * u[1]), (p[0] + b * v[0], p[1] + b * v[1])]


def corner_path(u: Point, angle: float, size: float = 0.24):
    """直角顶点处的角标（局部坐标，顶点在原点）：直角画小方角，其他角画小圆弧。"""
    u, v = legs(u, angle)
    if abs(angle - 90) < 1e-9:
        q1, q3 = (u[0] * size, u[1] * size), (v[0] * size, v[1] * size)
        q2 = (q1[0] + q3[0], q1[1] + q3[1])
        return line(*q1, *q2).extend(line(*q2, *q3))
    pts = [rotate((u[0] * size * 1.3, u[1] * size * 1.3), angle * k / 12) for k in range(13)]
    path = line(*pts[0], *pts[1])
    for p in pts[2:]:
        path.line_to(*p)
    return path


def triangles(layout: str, a: float, b: float, angle: float = 90.0) -> list[list[Point]]:
    return [triangle(p, u, a, b, angle) for p, u in anchors(layout, a, b)]


def holes(layout: str, a: float, b: float) -> dict[str, list[Point]]:
    """直角时空出来的区域（逆时针多边形）。"""
    s = a + b
    if layout == "four_around_c":
        return {"c2": [(a, 0.0), (s, a), (b, s), (0.0, b)]}
    return {"a2": [(0.0, 0.0), (a, 0.0), (a, a), (0.0, a)], "b2": [(a, a), (s, a), (s, s), (a, s)]}


def area(points: list[Point]) -> float:
    """多边形面积（鞋带公式，取绝对值）。"""
    n = len(points)
    twice = sum(points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1] for i in range(n))
    return abs(twice) / 2


def overlap(p: list[Point], q: list[Point], eps: float = 1e-9) -> bool:
    """两个凸多边形的内部是否相交（分离轴；只碰到边不算）。"""
    for poly in (p, q):
        n = len(poly)
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            nx, ny = y0 - y1, x1 - x0
            pa = [nx * x + ny * y for x, y in p]
            qa = [nx * x + ny * y for x, y in q]
            if min(pa) >= max(qa) - eps or min(qa) >= max(pa) - eps:
                return False
    return True


def slide_order(old: list[list[Point]], new: list[list[Point]]) -> list[list[int]]:
    """挪动顺序：每一步挪一块"目标位置不和其余各块当前位置重叠"的；没有这样的块就剩下的一起挪。

    不动的块不出现在结果里。返回 [[i], [j], …]，每项是同一步一起挪的块。
    """
    current = [list(t) for t in old]
    pending = [i for i in range(len(old)) if any(math.dist(x, y) > 1e-9 for x, y in zip(old[i], new[i]))]
    steps: list[list[int]] = []
    while pending:
        free = next((i for i in pending
                     if not any(overlap(new[i], current[j]) for j in range(len(current)) if j != i)), None)
        if free is None:
            steps.append(pending)
            break
        steps.append([free])
        current[free] = new[free]
        pending.remove(free)
    return steps


# ---------- 动画 ----------

class _Staged(Animation):
    """几步依次播放；每步是一组同时开始的动画，时长按步数均分（相邻两步略有重叠，动作更连贯）。

    extra 里是 (开始, 结束, 动画)：在整段时长的这一比例区间里播放，例如开头 20% 先淡掉旧的空白区域。
    """

    OVERLAP = 0.15

    def __init__(self, steps: list[list[Animation]], extra: list[tuple[float, float, Animation]] | None = None,
                 duration: float = 2.0):
        super().__init__(Node(), duration, easing.linear)
        n = len([s for s in steps if s])
        width = 1.0 / (n - (n - 1) * self.OVERLAP) if n else 1.0
        self.windows = [(k * width * (1 - self.OVERLAP), k * width * (1 - self.OVERLAP) + width, a)
                        for k, step in enumerate(s for s in steps if s) for a in step]
        self.windows += extra or []
        self.started: set[int] = set()

    def interpolate(self, alpha: float) -> None:
        for k, (start, end, a) in enumerate(self.windows):
            if alpha < start:
                continue
            if k not in self.started:
                a.begin()
                self.started.add(k)
            local = min(1.0, (alpha - start) / max(1e-6, end - start))
            a.interpolate(a.ease(local))

    def finish(self) -> None:
        self.interpolate(1.0)


class _Relabel(Animation):
    """标注换位置：先淡出，在看不见时挪过去，再淡入（不让 a、b 两个标注在半路交叉）。"""

    def __init__(self, node: Node, delta, duration: float = 1.0):
        super().__init__(node, duration, easing.linear)
        self.delta = np.asarray(delta, dtype=float)

    def begin(self) -> None:
        super().begin()
        self.base = self.node.matrix.copy()

    def interpolate(self, alpha: float) -> None:
        moved = alpha >= 0.5
        self.node.matrix = (T.translate(*self.delta) @ self.base) if moved else self.base
        self.node.set_style(opacity=easing.smooth(abs(alpha - 0.5) * 2))


class _Hinge(Animation):
    """把四个三角形的夹角从 start 渐变到 end：直角边 b 绕直角顶点转。"""

    def __init__(self, model: AreaModel, start: float, end: float, duration: float = 1.5):
        super().__init__(model.node, duration, easing.smooth)
        self.model, self.start, self.end = model, start, end

    def interpolate(self, alpha: float) -> None:
        self.model.draw_angle(self.start + (self.end - self.start) * alpha)


# ---------- 组件 ----------

class AreaModel(Widget):
    name = "area_model"
    SIDE = 6.0  # 大正方形的边长（画面单位，排版前）
    SINGLE = 1.25  # 单个三角形相对拼图里的三角形放大的倍数
    LABEL = 36
    AREA_LABEL = 40

    def build(self) -> Built:
        d = self.definition
        kind = d.get("kind")
        if kind != "right_triangles":
            raise NotImplementedError(f"kit（墨线）的 area_model 目前只支持 kind: right_triangles（对象 {self.obj_id}）")
        a, b = (float(x) for x in d.get("legs", [3, 4]))
        self.k = self.SIDE / (a + b)
        self.a, self.b = a * self.k, b * self.k
        self.angle = float(d.get("angle", 90))
        self.layout = d.get("layout", "four_around_c")
        if self.layout not in LAYOUTS:
            raise KeyError(f"area_model 没有摆法 {self.layout!r}（有 {' / '.join(LAYOUTS)}）")
        self.count = int(d.get("count", 4))
        t = self.theme
        self.ink = t.color(d.get("role"), t.A)
        if self.count == 1:
            return self._build_single()
        if self.count != 4:
            raise NotImplementedError("area_model 的 right_triangles 只支持 count: 1 或 4")
        return self._build_square(d.get("pieces", "shown") == "shown")

    # ---- 一个三角形：钩子用 ----

    def _build_single(self) -> Built:
        t, a, b = self.theme, self.a * self.SINGLE, self.b * self.SINGLE
        pts = triangle((0.0, 0.0), (1.0, 0.0), a, b, self.angle)
        tri = PathNode(path=polygon(pts), style=self._tri_style(self.ink))
        corner = PathNode(path=corner_path((1.0, 0.0), self.angle), style=Style(stroke=self.ink, stroke_width=2))
        la = mmath("a", self.LABEL, t.FG).move_to([a / 2, -0.32])
        lb = mmath("b", self.LABEL, t.FG).move_to([-0.32, b / 2])
        mid = np.array([(pts[1][0] + pts[2][0]) / 2, (pts[1][1] + pts[2][1]) / 2])
        normal = np.array([pts[2][1] - pts[1][1], pts[1][0] - pts[2][0]])  # 斜边的外法向
        normal /= np.linalg.norm(normal)
        lc = mmath("c", self.LABEL, t.FG).move_to(mid + normal * 0.34)
        self.tris = [tri]
        node = Group(tri, corner, la, lb, lc)
        node.shift(-node.center())
        return Built(node, parts={"tri": [tri], "side": [la, lb, lc], "hole": [], "area": []}, items=[tri])

    # ---- 大正方形 + 四个三角形 ----

    def _build_square(self, pieces_shown: bool) -> Built:
        t, a, b = self.theme, self.a, self.b
        s = a + b
        self.origin = np.array([-s / 2, -s / 2])  # 局部坐标：大正方形中心在原点
        node = Group(name=self.obj_id)
        frame = PathNode(path=polygon(self._pts([(0, 0), (s, 0), (s, s), (0, s)])),
                         style=Style(stroke=t.FG, stroke_width=3))

        # 空白区域：两种摆法的都建好，一开始全部隐藏
        self.holes: dict[str, Group] = {}
        self.area_labels: dict[str, Node] = {}
        polys = {**holes("four_around_c", a, b), **holes("two_rectangles", a, b)}
        for name in HOLES:
            role = HOLE_ROLES[name]
            pts = self._pts(polys[name])
            fill = PathNode(path=polygon(pts), style=Style(fill=t.color(role), fill_opacity=t.fill_opacity()))
            label = mmath(HOLE_TEX[name], self.AREA_LABEL, t.ink(role))
            label.move_to(np.mean(np.array(pts), axis=0))
            group = Group(fill, label, name=name)
            group.set_style(opacity=0.0)
            self.holes[name] = group
            self.area_labels[name] = label

        # 三角形：路径以直角顶点为原点，位置放在节点矩阵里，slide 只改矩阵
        self.tris: list[PathNode] = []
        for p, _u in anchors(self.layout, a, b):
            tri = PathNode(path=polygon([(0, 0), (1, 0), (0, 1)]), style=self._tri_style(self.ink))
            tri.add(PathNode(name="corner"))  # 角标：直角是小方角，钝角是小圆弧；样式跟三角形走
            tri.matrix = T.translate(*(np.array(p) + self.origin))
            if not pieces_shown:
                tri.set_style(opacity=0.0)
            self.tris.append(tri)

        # 边长标注：底边的 a、b 两种摆法都对；左边的 a、b 随摆法换位置；c 跟着第一块三角形走
        self.la = mmath("a", self.LABEL, t.FG)
        self.lb = mmath("b", self.LABEL, t.FG)
        self.la.move_to(self._pt((a / 2, -0.34)))
        self.lb.move_to(self._pt((a + b / 2, -0.34)))
        self.left_a = mmath("a", self.LABEL, t.FG)
        self.left_b = mmath("b", self.LABEL, t.FG)
        for lab, y in zip((self.left_a, self.left_b), self._left_label_y(self.layout)):
            lab.move_to(self._pt((-0.34, y)))
        self.lc = mmath("c", self.LABEL, t.FG)
        self.tris[0].add(self.lc)
        self.draw_angle(self.angle)  # 画出三角形、角标和 c 的位置

        node.add(*self.holes.values(), *self.tris, frame, self.la, self.lb, self.left_a, self.left_b)
        return Built(
            node,
            parts={
                "tri": list(self.tris),
                "hole": [self.holes[h] for h in HOLES],
                "area": [self.area_labels[h] for h in HOLES],
                "side": [self.la, self.lb, self.lc],
            },
            items=list(self.tris),
        )

    # ---- 工具 ----

    def _tri_style(self, color: str) -> Style:
        return Style(fill=color, fill_opacity=self.theme.fill_opacity(), stroke=color, stroke_width=3)

    def _pt(self, p: Point) -> np.ndarray:
        return np.array(p, dtype=float) + self.origin

    def _pts(self, pts: list[Point]) -> list[Point]:
        return [tuple(self._pt(p)) for p in pts]

    def _left_label_y(self, layout: str) -> tuple[float, float]:
        """左边上 a、b 两个标注的高度：(a 的, b 的)。"""
        a, b = self.a, self.b
        return (b + a / 2, b / 2) if layout == "four_around_c" else (a / 2, a + b / 2)

    def draw_angle(self, angle: float) -> None:
        """按夹角重画四个三角形（局部坐标，直角顶点在原点），c 的标注放在第一块斜边内侧。"""
        a, b = self.a, self.b
        for tri, (_p, u) in zip(self.tris, anchors(self.layout, a, b)):
            tri.path = polygon(triangle((0.0, 0.0), u, a, b, angle))
            tri.children[0].path = corner_path(u, angle)
        _p, u = anchors(self.layout, a, b)[0]
        pts = np.array(triangle((0.0, 0.0), u, a, b, angle))
        mid = (pts[1] + pts[2]) / 2
        inward = -mid / np.linalg.norm(mid)  # 从斜边中点指向直角顶点
        self.lc.matrix = T.identity()
        self.lc.move_to(mid + inward * 0.36)

    # ---- 动作 ----

    def action(self, act: str, args: dict) -> list[Animation]:
        if self.count != 4:
            raise KeyError(f"只有一个三角形的 area_model 没有动作 {act}")
        if act == "slide":
            return [self._slide(args["layout"])]
        if act == "bend":
            return self._bend(float(args["angle"]))
        raise KeyError(f"组件 {self.name} 没有动作 {act}")

    def _slide(self, layout: str) -> Animation:
        a, b = self.a, self.b
        old_anchors, new_anchors = anchors(self.layout, a, b), anchors(layout, a, b)
        old = triangles(self.layout, a, b, self.angle)
        new = triangles(layout, a, b, self.angle)
        steps: list[list[Animation]] = []
        for group in slide_order(old, new):
            steps.append([Shift(self.tris[i], np.subtract(new_anchors[i][0], old_anchors[i][0]), 1.0) for i in group])
        # 目标摆法里不存在的空白区域在开头先淡掉；左边的 a、b 标注在开头淡出、结尾在新位置淡入
        extra: list[tuple[float, float, Animation]] = []
        for name in HOLES:
            if name not in VALID_HOLES[layout]:
                extra.append((0.0, 0.2, _FadeTo(self.holes[name], 0.0)))
        for lab, y0, y1 in zip((self.left_a, self.left_b), self._left_label_y(self.layout), self._left_label_y(layout)):
            if abs(y1 - y0) > 1e-9:
                extra.append((0.0, 1.0, _Relabel(lab, [0.0, y1 - y0])))
        self.layout = layout
        return _Staged(steps, extra)

    def _bend(self, angle: float) -> list[Animation]:
        color = self.ink if abs(angle - 90) < 1e-9 else self.theme.NO
        anims: list[Animation] = [_Hinge(self, self.angle, angle)]
        anims += [Restyle(tri, 1.0, fill=color, stroke=color) for tri in self.tris]
        for name in HOLES:  # 夹角一变，空白区域就不再是那几个正方形了
            if abs(angle - 90) > 1e-9:
                anims.append(_FadeTo(self.holes[name], 0.0))
        self.angle = angle
        return anims


class _FadeTo(Fade):
    """从当前透明度渐变到目标值（已经是目标值时什么也不变）。"""

    def __init__(self, node: Node, end: float, duration: float = 0.4):
        super().__init__(node, 0.0, end, duration)

    def begin(self) -> None:
        super().begin()
        self.start = (self.node.style or Style()).opacity
