"""立体组件 solid（墨线实现）：正方体、长方体、四棱锥、圆柱、圆锥的轴测图（洞察 #14 圆锥体积）。

参数和检查见 scene_spec/solid.py。坐标 x 向右、y 向里、z 向上；每个立体的 at 是底面中心。

投影是正交轴测：从方位角 azimuth（默认 30°，看向右前方）、仰角 elevation（默认 20°）看过去。不用严格的等轴测
（35.26°、45°），因为那样正方体的前上角和后下角投到同一点，虚线的棱全叠在中间。

- 多面体（正方体、长方体、四棱锥、剖出来的块）：朝向观者的面涂淡色（按朝向深浅不同），两侧有一个面朝外的棱画实线，
  两侧都背对的棱画虚线；
- 圆柱、圆锥：轮廓线 + 底面椭圆，看不见的那半段椭圆画虚线。

不写 at 的立体沿画面水平方向排成一行（底面中心在画面上同一高度，互不遮挡）。

动作（颜色、透明度、位置都在动画开始播放时才改，建动作时只推演状态）：

- split {split}：正方体淡出，三个四棱锥在原处出现（公共顶点是左后上角，底面依次是下、前、右三个面，默认 A / B / C 色）；
- apart {split}：底下那块往下、前面那块往画面左边、右边那块往画面右边，各挪 gap；join {split}：拼回去；
  unsplit {split}：换回整个正方体；
- sweep {z}：截平面（plane.bodies 上方的一块水平面）移到高度 z，每个立体的截面跟着变；第一次 sweep 时截平面出现；
- compare：截面从立体里"拿出来"平铺到旁边（compare: right / below），是俯视的真实形状，大小可以直接比；之后的 sweep 让它们跟着变。

暴露：每个立体和剖出来的块是片段（obj#C），plane / section[i] / flat[i] 是部件。内置的 show / hide / mark / focus 都能用；
set {role} 由 restyle 接管：面换填充色、棱换描边色（立体的样式在面和棱各自的节点上，改组的 fill 不起作用）。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from moxian.anim import Animation, Restyle, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import Path as VPath
from moxian.path import polygon
from scene_spec.selectors import Selector
from scene_spec.solid import body_size

from .dots import FadeTo, Travel, reserve, straight
from .text import rich
from .widgets import Built, Widget

VIEW = (30.0, 20.0)  # 默认方位角、仰角（度）
FIT_W, FIT_H = 9.0, 5.0  # 图形撑到的大小（画面单位，排版前）
LABEL = 32
EDGE, HIDDEN = 3.0, 2.0  # 实线棱、虚线棱的粗细（1080p 像素）
HIDDEN_OPACITY = 0.55
DASH, GAP = 0.13, 0.09  # 虚线（画面单位）
FILL = 0.22  # 面的基础填充不透明度
SECTION_FILL = 0.7
LIGHT = np.array([-0.3, -0.5, 1.0]) / np.linalg.norm([-0.3, -0.5, 1.0])
N_ROUND = 96  # 圆周取多少个点
SPLIT_ROLES = ("A", "B", "C")


# ---------- 投影 ----------

class Camera:
    """正交投影：R 是画面向右，U 是画面向上，V 指向观者（点乘越大越近）。"""

    def __init__(self, azimuth: float, elevation: float):
        a, e = math.radians(azimuth), math.radians(elevation)
        self.a, self.e = a, e
        self.R = np.array([math.cos(a), math.sin(a), 0.0])
        self.U = np.array([-math.sin(a) * math.sin(e), math.cos(a) * math.sin(e), math.cos(e)])
        self.V = np.array([math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)])

    def p(self, pts) -> np.ndarray:
        """3D 点（n×3 或 3）→ 画面点（n×2 或 2）。"""
        pts = np.asarray(pts, dtype=float)
        return np.stack([pts @ self.R, pts @ self.U], axis=-1)


# ---------- 立体的几何（局部坐标：底面中心在原点） ----------

@dataclass
class Shape:
    kind: str  # poly / cylinder / cone
    verts: np.ndarray | None = None  # poly：n×3
    faces: list[list[int]] = field(default_factory=list)  # poly：每个面的顶点序号，从外面看逆时针
    r: float = 0.0
    h: float = 0.0

    def footprint(self) -> tuple[float, float, float, float]:
        """俯视的范围 (x0, y0, x1, y1)。"""
        if self.kind == "poly":
            xs, ys = self.verts[:, 0], self.verts[:, 1]
            return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())
        return -self.r, -self.r, self.r, self.r

    def outline_pts(self) -> np.ndarray:
        """算包围盒用的 3D 点。"""
        if self.kind == "poly":
            return self.verts
        t = np.linspace(0, 2 * math.pi, 64, endpoint=False)
        ring = np.stack([self.r * np.cos(t), self.r * np.sin(t), np.zeros_like(t)], axis=1)
        top = ring + [0, 0, self.h] if self.kind == "cylinder" else np.array([[0.0, 0.0, self.h]])
        return np.vstack([ring, top])

    def section(self, z: float) -> np.ndarray | None:
        """高度 z（局部）处的水平截面，m×3 的点（一圈）；平面碰不到时返回 None。"""
        eps = 1e-9
        if z < -eps or z > self.h + eps:
            return None
        if self.kind in ("cylinder", "cone"):
            r = self.r if self.kind == "cylinder" else self.r * (1 - z / self.h)
            if r < 1e-6:
                return None
            t = np.linspace(0, 2 * math.pi, N_ROUND, endpoint=False)
            return np.stack([r * np.cos(t), r * np.sin(t), np.full_like(t, z)], axis=1)
        pts = []
        edges = {tuple(sorted((f[i], f[(i + 1) % len(f)]))) for f in self.faces for i in range(len(f))}
        for i, j in edges:
            a, b = self.verts[i], self.verts[j]
            da, db = a[2] - z, b[2] - z
            if abs(da) < eps:
                pts.append(a)
            if abs(db) < eps:
                pts.append(b)
            if da * db < 0:
                pts.append(a + (b - a) * (da / (da - db)))
        uniq: list[np.ndarray] = []
        for p in pts:
            if all(np.linalg.norm(p - q) > 1e-7 for q in uniq):
                uniq.append(p)
        if len(uniq) < 3:
            return None
        arr = np.array(uniq)
        c = arr.mean(axis=0)
        order = np.argsort(np.arctan2(arr[:, 1] - c[1], arr[:, 0] - c[0]))
        arr = arr[order]
        area = 0.5 * abs(sum(arr[i, 0] * arr[i - 1, 1] - arr[i - 1, 0] * arr[i, 1] for i in range(len(arr))))
        return arr if area > 1e-9 else None


def oriented(verts: np.ndarray, faces: list[list[int]]) -> list[list[int]]:
    """把每个面的顶点顺序调成从外面看逆时针（法向背离重心）。"""
    c = verts.mean(axis=0)
    out = []
    for f in faces:
        n = np.cross(verts[f[1]] - verts[f[0]], verts[f[2]] - verts[f[0]])
        out.append(f if np.dot(n, verts[f].mean(axis=0) - c) > 0 else f[::-1])
    return out


def box(w: float, d: float, h: float) -> Shape:
    x, y = w / 2, d / 2
    verts = np.array([[-x, -y, 0], [x, -y, 0], [x, y, 0], [-x, y, 0],
                      [-x, -y, h], [x, -y, h], [x, y, h], [-x, y, h]], dtype=float)
    faces = [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return Shape("poly", verts, oriented(verts, faces), h=h)


def pyramid(base: np.ndarray, apex: np.ndarray) -> Shape:
    """底面多边形（按顺序的顶点）+ 顶点。"""
    verts = np.vstack([base, apex[None, :]])
    n = len(base)
    faces = [list(range(n))] + [[i, (i + 1) % n, n] for i in range(n)]
    return Shape("poly", verts, oriented(verts, faces), h=float(verts[:, 2].max() - verts[:, 2].min()))


def make_shape(body: dict) -> Shape:
    kind, w, d, h = body_size(body)
    if kind in ("cube", "box"):
        return box(w, d, h)
    if kind == "pyramid":
        x, y = w / 2, d / 2
        dx, dy = body["pyramid"].get("apex", [0, 0])
        return pyramid(np.array([[-x, -y, 0], [x, -y, 0], [x, y, 0], [-x, y, 0]], dtype=float), np.array([dx, dy, h], dtype=float))
    return Shape(kind, r=w / 2, h=h)


def cube_pieces(a: float) -> list[tuple[Shape, np.ndarray]]:
    """棱长 a 的正方体剖成三个全等的四棱锥：公共顶点是左后上角，底面依次是下、前、右三个面。返回 (块, 底面外法向)。"""
    s = a / 2
    apex = np.array([-s, s, a])
    bases = [
        ([[-s, -s, 0], [s, -s, 0], [s, s, 0], [-s, s, 0]], [0, 0, -1]),
        ([[-s, -s, 0], [s, -s, 0], [s, -s, a], [-s, -s, a]], [0, -1, 0]),
        ([[s, -s, 0], [s, s, 0], [s, s, a], [s, -s, a]], [1, 0, 0]),
    ]
    return [(pyramid(np.array(b, dtype=float), apex), np.array(n, dtype=float)) for b, n in bases]


# ---------- 画线 ----------

def dashed(pts: np.ndarray, dash: float, gap: float, closed: bool = False) -> VPath:
    """沿折线按弧长画虚线（折线可以是取样的椭圆弧，虚线不在每个取样点重新开始）。"""
    pts = np.asarray(pts, dtype=float)
    if closed:
        pts = np.vstack([pts, pts[:1]])
    path = VPath()
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    total = float(seg.sum())
    if total < 1e-9:
        return path
    n = max(1, round((total + gap) / (dash + gap)))
    d = (total - (n - 1) * gap) / n if n > 1 else total
    cum = np.concatenate([[0.0], np.cumsum(seg)])

    def at(s: float) -> np.ndarray:
        i = min(len(seg) - 1, max(0, int(np.searchsorted(cum, s, side="right")) - 1))
        f = 0.0 if seg[i] < 1e-12 else (s - cum[i]) / seg[i]
        return pts[i] + (pts[i + 1] - pts[i]) * f

    s = 0.0
    for _ in range(n):
        e = min(total, s + d)
        p = at(s)
        path.move_to(float(p[0]), float(p[1]))
        for k in range(len(cum)):
            if s < cum[k] < e:
                path.line_to(float(pts[k][0]), float(pts[k][1]))
        q = at(e)
        path.line_to(float(q[0]), float(q[1]))
        s += d + gap
    return path


def polyline(pts: np.ndarray, closed: bool = False, path: VPath | None = None) -> VPath:
    path = path or VPath()
    path.move_to(float(pts[0][0]), float(pts[0][1]))
    for p in pts[1:]:
        path.line_to(float(p[0]), float(p[1]))
    if closed:
        path.close()
    return path


def loop_path(pts: np.ndarray | None) -> VPath:
    return VPath() if pts is None or len(pts) < 3 else polyline(pts, closed=True)


class _Func(Animation):
    """按 alpha 调一个函数（截平面扫过、截面平铺）；on_begin 在播放开始时调。"""

    def __init__(self, fn: Callable[[float], None], duration: float = 1.0, on_begin: Callable[[], None] | None = None,
                 ease=easing.smooth):
        super().__init__(Node(), duration, ease)
        self.fn, self.on_begin = fn, on_begin

    def begin(self) -> None:
        super().begin()
        if self.on_begin:
            self.on_begin()

    def interpolate(self, alpha: float) -> None:
        self.fn(alpha)


# ---------- 组件 ----------

@dataclass
class _Body:
    name: str
    shape: Shape
    at: np.ndarray
    role: str
    node: Group = None  # type: ignore[assignment]
    faces: list[PathNode] = field(default_factory=list)  # 换颜色时改 fill
    lines: list[PathNode] = field(default_factory=list)  # 换颜色时改 stroke


class Solid(Widget):
    name = "solid"

    def build(self) -> Built:
        d = self.definition
        view = d.get("view", {})
        self.cam = Camera(float(view.get("azimuth", VIEW[0])), float(view.get("elevation", VIEW[1])))
        self.label_floor = "label"
        self.label_size = float(self.opts.get("label_size", LABEL))
        self.acted = False
        base_role = d.get("role", "A")

        self.bodies: dict[str, _Body] = {}
        for name, body in d["bodies"].items():
            self.bodies[name] = _Body(name, make_shape(body), np.array(body.get("at", [0, 0, 0]), dtype=float),
                                      body.get("role", base_role))
        self._auto_row([n for n, body in d["bodies"].items() if "at" not in body])
        # 剖开：块的名字 → (所在的 split, 底面外法向)
        self.splits: dict[str, dict] = {}
        for sname, sp in (d.get("splits") or {}).items():
            cube = self.bodies[sp["body"]]
            a = cube.shape.h
            roles = sp.get("roles", SPLIT_ROLES)
            names = list(sp["into"])
            dirs = []
            # 挪开的方向：底下那块往下，前面那块往画面左边，右边那块往画面右边（沿底面法向挪时前面和底下两块在画面上叠在一起）
            away = [np.array([0.0, 0.0, -1.0]), -self.cam.R, self.cam.R]
            for name, (shape, _normal), role, n in zip(names, cube_pieces(a), roles, away):
                self.bodies[name] = _Body(name, shape, cube.at.copy(), role)
                dirs.append(n)
            self.splits[sname] = {"body": sp["body"], "pieces": names, "dirs": dirs, "gap": float(sp.get("gap", 0.75 * a)),
                                  "state": "whole"}
        piece_names = {n for sp in self.splits.values() for n in sp["pieces"]}

        plane = d.get("plane")
        self.plane_bodies = list(plane["bodies"]) if plane else []
        if plane:
            lo = min(self.bodies[n].at[2] for n in self.plane_bodies)
            hi = max(self.bodies[n].at[2] + self.bodies[n].shape.h for n in self.plane_bodies)
            self.z_range = (lo, hi)
            self.z = float(plane.get("z", (lo + hi) / 2))
        self.compare = plane.get("compare") if plane else None
        self.compared = False
        self.plane_shown = False

        # 先不带标签算一次缩放，按它把标签的高度换成图形单位，再算最终的缩放
        self.label_h = rich("Ag", self.theme, size=self.label_size).node.height  # 一行标签多高（画面单位）
        k = self._fit(0.0)
        for _ in range(2):
            k = self._fit(self.label_h / k)
        self.k = k
        x0, y0, x1, y1 = self.box
        fig = Group(name="fig")
        fig.matrix = T.scale(k) @ T.translate(-(x0 + x1) / 2, -(y0 + y1) / 2)
        self.fig = fig
        fig.add(reserve(x1 - x0, y1 - y0, (x0 + x1) / 2, (y0 + y1) / 2))

        for name, b in self.bodies.items():
            fig.add(self._body_node(b))
            hidden = name in piece_names or (name in d["bodies"] and d["bodies"][name].get("hidden", False))
            b.node.set_style(opacity=0.0 if hidden else 1.0)

        spans: dict[str, Node] = {n: b.node for n, b in self.bodies.items()}
        parts: dict[str, list[Node]] = {}
        if plane:
            self._build_plane(fig)
            parts["plane"] = [self.plane_node]
            parts["section"] = list(self.sections)
            if self.compare:
                parts["flat"] = [Group(f, *([lab] if lab else [])) for f, lab in zip(self.flats, self.flat_labels)]
        self.items = [b.node for n, b in self.bodies.items() if n not in piece_names]
        return Built(Group(fig, name=self.obj_id), spans=spans, parts=parts, items=self.items)

    # ---------- 版面 ----------

    def _proj_box(self, pts3: np.ndarray) -> tuple[float, float, float, float]:
        p = self.cam.p(pts3)
        return float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())

    def _fit(self, label_h: float) -> float:
        """按现在的标签高度（图形单位）排好平铺截面的位置，算出包围盒和缩放。"""
        boxes = []
        self.body_box: dict[str, tuple[float, float, float, float]] = {}
        for name, b in self.bodies.items():
            box_ = self._proj_box(b.shape.outline_pts() + b.at)
            self.body_box[name] = box_
            boxes.append(box_)
            if self.definition["bodies"].get(name, {}).get("label"):
                boxes.append((box_[0], box_[1] - 0.15 * label_h - label_h * 1.1, box_[2], box_[1]))
        for sp in self.splits.values():
            for name, n in zip(sp["pieces"], sp["dirs"]):
                b = self.bodies[name]
                boxes.append(self._proj_box(b.shape.outline_pts() + b.at + n * sp["gap"]))
        if self.plane_bodies:
            lo, hi = self.z_range
            boxes += [self._proj_box(self._plane_corners(z)) for z in (lo, hi)]
            if self.compare:
                self._layout_flats(_union(boxes), label_h)
                for i, slot in enumerate(self.slots):
                    fx0, fy0, fx1, fy1 = self.slot_size[i]
                    boxes.append((slot[0] + fx0, slot[1] + fy0, slot[0] + fx1, slot[1] + fy1))
                    if self.definition["plane"].get("labels"):
                        boxes.append((slot[0] + fx0, slot[1] + fy0 - 0.25 * label_h - label_h * 1.1, slot[0] + fx1, slot[1] + fy0))
        x0, y0, x1, y1 = _union(boxes)
        pad = 0.04 * max(x1 - x0, y1 - y0)
        self.box = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
        return min(FIT_W / (x1 - x0 + 2 * pad), FIT_H / (y1 - y0 + 2 * pad))

    def _auto_row(self, names: list[str]) -> None:
        """不写 at 的立体排成画面上的一行：底面中心沿画面向右的水平方向 R 排开，互不遮挡，整行居中。"""
        if not names:
            return
        spans = []
        for n in names:
            p = self.cam.p(self.bodies[n].shape.outline_pts())[:, 0]
            spans.append((float(p.min()), float(p.max())))
        gap = 0.35 * max(r - lo for lo, r in spans)
        pos, x = [], 0.0
        for i, (lo, _r) in enumerate(spans):
            if i:
                x += spans[i - 1][1] - lo + gap
            pos.append(x)
        mid = (pos[0] + spans[0][0] + pos[-1] + spans[-1][1]) / 2
        for n, x in zip(names, pos):
            self.bodies[n].at = (x - mid) * self.cam.R

    def _plane_corners(self, z: float) -> np.ndarray:
        """截平面：高度 z 处的一块水平平行四边形，一组边沿 R（画面向右），另一组沿 y（向里，画面上是斜的，看得出是平面），
        盖住 plane.bodies 的底面再多出一点。"""
        R, Y = self.cam.R, np.array([0.0, 1.0, 0.0])
        pts = np.vstack([self.bodies[n].shape.outline_pts() + self.bodies[n].at for n in self.plane_bodies])
        s = pts[:, 0] / R[0]  # p = s·R + f·Y
        f = pts[:, 1] - s * R[1]
        widths = [self.bodies[n].shape.footprint() for n in self.plane_bodies]
        m = 0.25 * max(f[2] - f[0] for f in widths)
        s0, s1, f0, f1 = s.min() - m, s.max() + m, f.min() - m, f.max() + m
        up = np.array([0.0, 0.0, z])
        return np.array([s0 * R + f0 * Y, s1 * R + f0 * Y, s1 * R + f1 * Y, s0 * R + f1 * Y]) + up

    def _layout_flats(self, around: tuple[float, float, float, float], label_h: float) -> None:
        """平铺截面的位置：每个立体底面范围那么大的一格，排成一行，放在立体的右边或下面。"""
        self.slot_size = [self.bodies[n].shape.footprint() for n in self.plane_bodies]
        widths = [s[2] - s[0] for s in self.slot_size]
        size = max(max(widths), max(s[3] - s[1] for s in self.slot_size))
        gap = 0.35 * size
        x0, y0, x1, y1 = around
        if self.compare == "right":
            x = x1 + 0.6 * size
            cy = (y0 + y1) / 2
        else:
            x = (x0 + x1) / 2 - (sum(widths) + gap * (len(widths) - 1)) / 2
            cy = y0 - 0.5 * size - max(s[3] - s[1] for s in self.slot_size) / 2
        self.slots = []
        for s in self.slot_size:
            self.slots.append(np.array([x - s[0], cy - (s[1] + s[3]) / 2]))
            x += (s[2] - s[0]) + gap

    # ---------- 节点 ----------

    def _label(self, text: str, role: str | None = None) -> Node:
        t = self.theme
        node = rich(text, t, size=self.label_size, color=t.ink(role) if role else t.FG).node
        node.shift(-node.center())
        node.font_floor = self.label_floor
        g = Group(node)
        g.text = text  # 测试和排查时读回写的是什么
        return g

    def _paint(self, role: str) -> tuple[str, str]:
        """面的填充色、棱的描边色（HL 在浅底上描边换成 HL_INK）。"""
        return self.theme.color(role), self.theme.ink(role)

    def _body_node(self, b: _Body) -> Group:
        cam, k = self.cam, self.k
        fill, ink = self._paint(b.role)
        g = Group(name=b.name)
        g.matrix = T.translate(*cam.p(b.at))
        faces = Group(name="faces")
        visible, hidden = VPath(), VPath()
        s = b.shape
        if s.kind == "poly":
            front = []
            for f in s.faces:
                v = s.verts
                n = np.cross(v[f[1]] - v[f[0]], v[f[2]] - v[f[0]])
                n /= np.linalg.norm(n)
                front.append(float(np.dot(n, cam.V)) > 1e-6)
                if front[-1]:
                    shade = FILL * (1.4 - 0.8 * max(0.0, float(np.dot(n, LIGHT))))
                    faces.add(PathNode(path=polygon([tuple(p) for p in cam.p(v[f])]),
                                       style=Style(fill=fill, fill_opacity=shade)))
            edges: dict[tuple[int, int], bool] = {}
            for f, on in zip(s.faces, front):
                for i in range(len(f)):
                    e = tuple(sorted((f[i], f[(i + 1) % len(f)])))
                    edges[e] = edges.get(e, False) or on
            hidden_segs = []
            for (i, j), on in edges.items():
                p, q = cam.p(s.verts[i]), cam.p(s.verts[j])
                if on:
                    visible.move_to(*map(float, p)).line_to(*map(float, q))
                else:
                    hidden_segs.append((p, q))
            for p, q in hidden_segs:
                hidden.extend(dashed(np.array([p, q]), DASH / k, GAP / k))
        else:
            self._round(s, faces, visible, hidden, fill)
        g.add(faces)
        lines = [PathNode(path=visible, style=Style(stroke=ink, stroke_width=EDGE)),
                 PathNode(path=hidden, style=Style(stroke=ink, stroke_width=HIDDEN, stroke_opacity=HIDDEN_OPACITY))]
        g.add(*lines)
        b.faces, b.lines = list(faces.children), lines
        label = self.definition["bodies"].get(b.name, {}).get("label")
        if label:
            lab = self._label(label, b.role)
            x0, y0, x1, _ = self.body_box[b.name]
            lab.matrix = T.translate((x0 + x1) / 2 - cam.p(b.at)[0], y0 - cam.p(b.at)[1] - 0.15 * self.label_h / k
                                     - lab.height / 2 / k) @ T.scale(1 / k)
            g.add(lab)
        b.node = g
        return g

    def _round(self, s: Shape, faces: Group, visible: VPath, hidden: VPath, fill: str) -> None:
        """圆柱、圆锥（局部画面坐标）：底面椭圆半轴 r 和 r·sin(仰角)，前半圈在下面。"""
        r, h, e, k = s.r, s.h, self.cam.e, self.k
        b, H = r * math.sin(e), h * math.cos(e)

        def arc(p0: float, p1: float, z: float) -> np.ndarray:
            n = max(8, int(abs(p1 - p0) / (2 * math.pi) * N_ROUND))
            phi = np.linspace(p0, p1, n + 1)
            return np.stack([r * np.cos(phi), b * np.sin(phi) + z * math.cos(e)], axis=1)

        pi = math.pi
        if s.kind == "cylinder":
            side = np.vstack([arc(pi, 2 * pi, h), arc(2 * pi, pi, 0.0)])
            faces.add(PathNode(path=polyline(side, closed=True), style=Style(fill=fill, fill_opacity=FILL * 1.15)))
            faces.add(PathNode(path=polyline(arc(0, 2 * pi, h), closed=True), style=Style(fill=fill, fill_opacity=FILL * 0.7)))
            polyline(arc(0, 2 * pi, h), closed=True, path=visible)
            polyline(arc(pi, 2 * pi, 0.0), path=visible)
            for x in (-r, r):
                visible.move_to(x, 0.0).line_to(x, H)
            hidden.extend(dashed(arc(0, pi, 0.0), DASH / k, GAP / k))
            return
        if H <= b * 1.0001:  # 很扁的圆锥：顶点落在底面椭圆里，只看见底面
            faces.add(PathNode(path=polyline(arc(0, 2 * pi, 0.0), closed=True), style=Style(fill=fill, fill_opacity=FILL)))
            polyline(arc(0, 2 * pi, 0.0), closed=True, path=visible)
            return
        p1 = math.asin(b / H)
        p2 = pi - p1
        front = arc(p2, 2 * pi + p1, 0.0)
        faces.add(PathNode(path=polyline(np.vstack([[[0.0, H]], front]), closed=True),
                           style=Style(fill=fill, fill_opacity=FILL * 1.15)))
        polyline(front, path=visible)
        for p in (front[0], front[-1]):
            visible.move_to(0.0, H).line_to(float(p[0]), float(p[1]))
        hidden.extend(dashed(arc(p1, p2, 0.0), DASH / k, GAP / k))

    def _build_plane(self, fig: Group) -> None:
        t = self.theme
        self.plane_node = PathNode(style=Style(fill=t.DIM, fill_opacity=0.16, stroke=t.DIM, stroke_width=2, opacity=0.0),
                                   name="plane")
        fig.add(self.plane_node)
        self.sections: list[PathNode] = []
        self.flats: list[PathNode] = []
        self.flat_labels: list[Node | None] = []
        labels = self.definition["plane"].get("labels") or [None] * len(self.plane_bodies)
        for i, name in enumerate(self.plane_bodies):
            fill, ink = self._paint(self.bodies[name].role)
            style = Style(fill=fill, fill_opacity=SECTION_FILL, stroke=ink, stroke_width=EDGE, opacity=0.0)
            sec = PathNode(style=style, name=f"section{i}")
            self.sections.append(sec)
            fig.add(sec)
            if not self.compare:
                continue
            flat = PathNode(style=style, name=f"flat{i}")
            self.flats.append(flat)
            fig.add(flat)
            lab = None
            if labels[i]:
                lab = self._label(labels[i], self.bodies[name].role)
                slot, fy0 = self.slots[i], self.slot_size[i][1]
                lab.matrix = T.translate(slot[0], slot[1] + fy0 - 0.25 * self.label_h / self.k
                                         - lab.height / 2 / self.k) @ T.scale(1 / self.k)
                lab.set_style(opacity=0.0)
                fig.add(lab)
            self.flat_labels.append(lab)
        self._place(self.z, flats=False)

    # ---------- 截面 ----------

    def _section_pts(self, i: int, z: float) -> np.ndarray | None:
        b = self.bodies[self.plane_bodies[i]]
        local = b.shape.section(z - b.at[2])
        return None if local is None else local + b.at

    def _projected(self, pts: np.ndarray | None) -> np.ndarray | None:
        return None if pts is None else self.cam.p(pts)

    def _flat(self, i: int, pts: np.ndarray | None) -> np.ndarray | None:
        if pts is None:
            return None
        b = self.bodies[self.plane_bodies[i]]
        return pts[:, :2] - b.at[:2] + self.slots[i]

    def _place(self, z: float, flats: bool) -> None:
        self.plane_node.path = polygon([tuple(p) for p in self.cam.p(self._plane_corners(z))])
        for i, sec in enumerate(self.sections):
            pts = self._section_pts(i, z)
            sec.path = loop_path(self._projected(pts))
            if flats:
                self.flats[i].path = loop_path(self._flat(i, pts))

    # ---------- 动作 ----------

    def action(self, act: str, args: dict) -> list[Animation]:
        self.acted = True  # 做过动作之后后端就不再重建它（标注字号）
        if act in ("split", "apart", "join", "unsplit"):
            return self._split_action(act, self.splits[args["split"]])
        if act == "sweep":
            return self._sweep(float(args["z"]))
        if act == "compare":
            return self._compare()
        raise KeyError(f"组件 {self.name} 没有动作 {act}")

    def _split_action(self, act: str, sp: dict) -> list[Animation]:
        cube = self.bodies[sp["body"]].node
        pieces = [self.bodies[n].node for n in sp["pieces"]]
        if act in ("split", "unsplit"):
            on = act == "split"
            sp["state"] = "joined" if on else "whole"
            if on:
                self._swap_items([cube], pieces)
            else:
                self._swap_items(pieces, [cube])
            return [FadeTo(cube, 0.0 if on else 1.0, 0.6)] + [FadeTo(p, 1.0 if on else 0.0, 0.6) for p in pieces]
        apart = act == "apart"
        sp["state"] = "apart" if apart else "joined"
        out: list[Animation] = []
        for node, n in zip(pieces, sp["dirs"]):
            off = self.cam.p(n * sp["gap"])
            a, b = (np.zeros(2), off) if apart else (off, np.zeros(2))
            out.append(Travel(node, straight(a, b), 1.0))
        return out

    def _swap_items(self, old: list[Node], new: list[Node]) -> None:
        """items 是"现在看得见的立体"（同一个列表对象，原地更新）：内置的 focus 只调暗它们。"""
        ids = {id(n) for n in old}
        keep = [n for n in self.items if id(n) not in ids]
        self.items[:] = keep + [n for n in new if all(n is not m for m in keep)]

    def _sweep(self, z1: float) -> list[Animation]:
        z0, flats = self.z, self.compared
        self.z = z1
        if not self.plane_shown:
            self.plane_shown = True
            show = _Func(lambda alpha: None, 0.01, on_begin=lambda: self._place(z1, flats))
            return [show, FadeTo(self.plane_node, 1.0, 0.6)] + [FadeTo(s, 1.0, 0.6) for s in self.sections]
        # 截平面连续扫过、截面跟着变：中途采样（compare 的平铺同理）
        return [_Func(lambda alpha: self._place(z0 + (z1 - z0) * alpha, flats), 1.6).sampled()]

    def _compare(self) -> list[Animation]:
        self.compared = True
        z = self.z
        pairs = []
        for i in range(len(self.sections)):
            pts = self._section_pts(i, z)
            pairs.append((self._projected(pts), self._flat(i, pts)))

        def morph(alpha: float) -> None:
            for flat, (a, b) in zip(self.flats, pairs):
                flat.path = loop_path(None if a is None else a + (b - a) * alpha)

        def begin() -> None:
            morph(0.0)
            for flat in self.flats:
                flat.set_style(opacity=1.0)

        out: list[Animation] = [_Func(morph, 1.2, on_begin=begin).sampled()]
        out += [FadeTo(lab, 1.0, 0.5) for lab in self.flat_labels if lab is not None]
        return out

    # ---------- set {role / opacity} ----------

    def restyle(self, sel: Selector, args: dict) -> list[Animation] | None:
        d = 0.3
        if sel.span in self.bodies:
            b = self.bodies[sel.span]
            out: list[Animation] = []
            if "role" in args:
                fill, ink = self._paint(args["role"])
                b.role = args["role"]
                out += [Restyle(f, d, fill=fill) for f in b.faces] + [Restyle(ln, d, stroke=ink) for ln in b.lines]
            if "opacity" in args:
                out.append(Restyle(b.node, d, opacity=float(args["opacity"])))
            return out
        if sel.part in ("section", "flat", "plane"):
            nodes = {"section": self.sections, "flat": self.flats, "plane": [self.plane_node]}[sel.part]
            pick = nodes[sel.index[0]:sel.index[1]] if sel.index else nodes
            out = []
            for node in pick:
                if "role" in args:
                    out.append(Restyle(node, d, fill=self.theme.color(args["role"]), stroke=self.theme.ink(args["role"])))
                if "opacity" in args:
                    out.append(Restyle(node, d, opacity=float(args["opacity"])))
            return out
        return None


def _union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
