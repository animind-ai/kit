"""三维坐标空间 space（墨线实现）：线性代数的列空间、零空间、投影、行列式是体积。

参数和检查见 scene_spec/space.py。坐标 x 向右、y 向里、z 向上（同 solid）；投影是正交轴测，相机和 orbit 也同 solid
（Camera、orbit_target、planned_views）：排版按全部 orbit 一路经过的视角留位置，转动中途不出框。

画法：
- z = 0 上的淡单位网格（floor）；mesh: true 时另一层彩色网格（mesh_role，默认 B）跟着 warp 变形，每一帧裁在坐标范围里；
- 三根坐标轴从起点画到止点再长出一点，正方向带箭头和轴名（axis_labels，默认 x、y、z）；
- 画上去的东西每一帧按离观者的远近重排绘制顺序（远的先画，近的盖在上面）：
  vector 箭头（箭杆是投影后的线段，箭头尖在画面上画，正对观者时变短）；point；
  line 裁在坐标范围里的线段（span）；plane 平面和坐标范围的交（凸多边形，半透明）；
  box 平行六面体（朝向观者的面涂淡色，两侧的面都背对观者的棱画虚线）；
  project 的直角记号（变形一开始就淡出：变形之后不再是直角）。
- 名字都垫底色、标 font_floor = "label"（后端按字号下限抬 label_size）。

动作的几何在播放时才改（建动作时只推演 item.geo；画面上现在的几何在 item.shown）：
- warp {matrix}：全部东西和彩色网格按 (1−t)I + tM 渐变，和之前的变换复合；矩阵可以奇异：压没了的直线、
  压成一条线的平面画成线段，压成一点的不画；reset：之前全部 warp 的逆；
- orbit：相机连续转，每一帧重算全部投影和远近顺序；relabel {name, label}：换名字；remove {name}：淡出。

暴露的部件：axes、floor、mesh、items（画上去的全部东西，一项）。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from moxian.anim import Animation, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import Path, circle, polygon, rounded_rect
from scene_spec.solid import VIEW

from .dissect import dashed
from .dots import FadeTo, reserve
from .plane import _Late
from .solid import Camera, _Func, orbit_target, planned_views
from .text import mmath
from .widgets import Built, Widget

FIT_W, FIT_H = 9.0, 5.6  # 坐标范围的投影撑到的大小（画面单位，排版前）
AXIS_OVER = 0.45  # 坐标轴在止点外多长出的一段（数学单位）
HEAD, HEAD_W = 0.26, 0.11  # 箭头尖（画面单位）
VEC_W, LINE_W, EDGE_W = 6, 4, 3  # 1080p 像素
PLANE_FILL, BOX_FILL = 0.2, 0.16
HIDDEN_OPACITY = 0.5
PAD = 0.1
MARK = 0.28  # 直角记号的边长（数学单位）
DOT = 0.09
EPS = 1e-6
DEFAULT_ROLE = {"vector": "A", "point": "HL", "line": "C", "plane": "B", "box": "C", "mark": "DIM"}


# ---------- 几何 ----------

def clip_line(p: np.ndarray, d: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """直线 p + s·d 裁在长方体 [lo, hi] 里的那一段（Liang–Barsky）；不相交时 None。"""
    s0, s1 = -math.inf, math.inf
    for k in range(3):
        if abs(d[k]) < 1e-12:
            if not lo[k] - 1e-9 <= p[k] <= hi[k] + 1e-9:
                return None
            continue
        a, b = (lo[k] - p[k]) / d[k], (hi[k] - p[k]) / d[k]
        s0, s1 = max(s0, min(a, b)), min(s1, max(a, b))
    if s1 - s0 < 1e-9:
        return None
    return p + s0 * d, p + s1 * d


def box_corners(lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    return np.array([[(lo, hi)[(i >> k) & 1][k] for k in range(3)] for i in range(8)], dtype=float)


BOX_EDGES = [(i, i | (1 << k)) for i in range(8) for k in range(3) if not i & (1 << k)]  # 12 条棱：顶点序号差一位


def clip_plane(p: np.ndarray, u: np.ndarray, w: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray | None:
    """平面 p + s·u + t·w 和长方体 [lo, hi] 的交（凸多边形的顶点，按转向排好）；不相交或平面退化时 None。"""
    n = np.cross(u, w)
    if np.linalg.norm(n) < EPS:
        return None
    n = n / np.linalg.norm(n)
    c = box_corners(lo, hi)
    f = (c - p) @ n
    pts = [c[i] for i in range(8) if abs(f[i]) < 1e-9]
    for i, j in BOX_EDGES:
        if f[i] * f[j] < 0 and abs(f[i]) > 1e-9 and abs(f[j]) > 1e-9:
            pts.append(c[i] + (c[j] - c[i]) * f[i] / (f[i] - f[j]))
    if len(pts) < 3:
        return None
    pts = np.array(pts)
    mid = pts.mean(axis=0)
    e1 = u / np.linalg.norm(u)
    e2 = np.cross(n, e1)
    ang = np.arctan2((pts - mid) @ e2, (pts - mid) @ e1)
    pts = pts[np.argsort(ang)]
    keep = [pts[0]] + [q for a, q in zip(pts[:-1], pts[1:]) if np.linalg.norm(q - a) > 1e-9]
    return np.array(keep) if len(keep) >= 3 else None


def tween(m: np.ndarray) -> Callable[[float], np.ndarray]:
    return lambda t: (1 - t) * np.eye(3) + t * m


def moved(geo: dict[str, np.ndarray], m: np.ndarray) -> dict[str, np.ndarray]:
    """几何的每一项（点或方向）都按线性变换 m 走：变换过原点，点和方向一样乘。"""
    return {k: m @ v for k, v in geo.items()}


def project_onto(v: np.ndarray, onto: Item) -> np.ndarray:
    if onto.kind == "line":
        d = onto.geo["d"]
        return float(v @ d) / float(d @ d) * d
    n = np.cross(onto.geo["u"], onto.geo["w"])
    return v - float(v @ n) / float(n @ n) * n


def plane_span(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """同 scene_spec.space.plane_span。"""
    k = int(np.argmin(np.abs(normal)))
    u = np.cross(normal, np.eye(3)[k])
    return u, np.cross(normal, u)


@dataclass
class Item:
    kind: str  # vector / point / line / plane / box / mark
    name: str
    geo: dict[str, np.ndarray]  # 推演到的几何（建动作时改）
    node: Group
    role: str
    label: Node | None = None
    dashed: bool = False
    shown: dict[str, np.ndarray] = field(default_factory=dict)  # 画面上现在的几何（播放时改）
    grow: float = 1.0  # 直线从经过的点向两边长出来
    hidden: bool = False  # 名字是不是因为压扁了藏起来了
    depth: float = 0.0


class Space(Widget):
    name = "space"
    LABEL = 34
    label_floor = "label"

    @staticmethod
    def plan_opts(doc: dict, obj_id: str) -> dict:
        views = planned_views(doc, obj_id)
        return {"views": views} if views else {}

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.acted = False
        self.label_size = float(self.opts.get("label_size", self.LABEL))
        self.lo = np.array([d["x"][0], d["y"][0], d["z"][0]], dtype=float)
        self.hi = np.array([d["x"][1], d["y"][1], d["z"][1]], dtype=float)
        view = d.get("view") or {}
        self.view = (float(view.get("azimuth", VIEW[0])), float(view.get("elevation", VIEW[1])))
        self.cam = Camera(*self.view)
        cams = [self.cam] + [Camera(*v) for v in self.opts.get("views", ())]
        # 缩放和中心按经过的每个视角一起定：坐标范围的 8 个角和轴尖
        tips = np.array([self.hi[i] + AXIS_OVER + 0.35 for i in range(3)])
        pts = np.vstack([box_corners(self.lo, self.hi), np.diag(tips)])
        boxes = [c.p(pts) for c in cams]
        allp = np.vstack(boxes)
        (x0, y0), (x1, y1) = allp.min(axis=0), allp.max(axis=0)
        self.k = min(FIT_W / (x1 - x0), FIT_H / (y1 - y0))
        self.c0 = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        lab_h = mmath("x", self.label_size, t.FG).height
        node = Group(name=self.obj_id)
        node.add(reserve(self.k * (x1 - x0) + 2.4 * lab_h, self.k * (y1 - y0) + 2.4 * lab_h))

        self.floor = PathNode(path=Path(), style=Style(stroke=t.DIM, stroke_width=2, stroke_opacity=0.35), name="floor")
        self.floor_on = d.get("floor", True)
        self.mesh = PathNode(path=Path(), style=Style(stroke=t.color(d.get("mesh_role"), t.B), stroke_width=3,
                                                       stroke_opacity=0.7), name="mesh")
        self.mesh_on = bool(d.get("mesh", False))
        self.M = np.eye(3)  # 推演到的整体变换
        self.mesh_M = np.eye(3)  # 画面上彩色网格现在的变换
        ax_style = Style(stroke=t.FG, stroke_width=3)
        self.axis_lines = PathNode(path=Path(), style=ax_style)
        self.axis_heads = PathNode(path=Path(), style=Style(fill=t.FG, stroke=t.FG, stroke_width=1))
        names = d.get("axis_labels") or ["x", "y", "z"]
        self.axis_labels = [self.label_node(n, t.DIM) for n in names]
        self.axes = Group(self.axis_lines, self.axis_heads, name="axes")
        self.layer = Group(name="items")
        self.labels = Group(*self.axis_labels, name="labels")  # 名字（含轴名）画在所有东西上面：底色盖住从下面过的线
        node.add(self.floor, self.mesh, self.axes, self.layer, self.labels)
        self.items: dict[str, Item] = {}
        self.repaint()
        return Built(node, parts={"axes": [self.axes], "floor": [self.floor], "mesh": [self.mesh], "items": [self.layer]})

    # ---------- 投影和文字 ----------

    def s(self, pts) -> np.ndarray:
        """数学坐标（n×3 或 3）→ 组件里的画面坐标。"""
        return self.k * (self.cam.p(pts) - self.c0)

    def label_node(self, tex: str, color: str) -> Group:
        body = mmath(tex, self.label_size, color)
        body.font_floor = self.label_floor
        bg = PathNode(path=rounded_rect(body.width + 2 * PAD, body.height + 2 * PAD, 0.06),
                      style=Style(fill=self.theme.BG))
        bg.move_to(body.center())
        g = Group(bg, body)
        g.matrix = T.translate(*(-g.center()))
        return Group(g)

    def put_label(self, lab: Node | None, at: np.ndarray, toward: np.ndarray | None = None, gap: float = 0.18) -> None:
        """名字的中心放在画面点 at 朝 toward 方向让开自己的半个身子再加 gap 的地方。"""
        if lab is None:
            return
        if toward is None or np.linalg.norm(toward) < EPS:
            lab.matrix = T.translate(*at)
            return
        u = toward / np.linalg.norm(toward)
        half = np.array([lab.width, lab.height]) / 2
        lab.matrix = T.translate(*(at + u * (gap + float(np.abs(u) @ half))))

    def put_label_free(self, lab: Node | None, choices: list[tuple[np.ndarray, np.ndarray, float]]) -> None:
        """按顺序试几个位置，用第一个不压在轴名和别的名字上的；都压到就用第一个。"""
        if lab is None:
            return
        others = self.axis_labels + [i.label for i in self.items.values() if i.label is not None and i.label is not lab]
        axes = [b for a in others if (b := a.bbox()) is not None]
        for at, toward, gap in choices:
            self.put_label(lab, at, toward, gap)
            box = lab.bbox()
            if box is None or not any(min(box.x1, b.x1) > max(box.x0, b.x0) and min(box.y1, b.y1) > max(box.y0, b.y0)
                                      for b in axes):
                return
        self.put_label(lab, *choices[0])

    def show_label(self, item: Item, on: bool) -> None:
        """直线压没了、平面压扁了时名字跟着藏起来（播放时改透明度），恢复时再出来。"""
        if item.label is None or item.hidden == (not on):
            return
        item.hidden = not on
        item.label.set_style(opacity=1.0 if on else 0.0)

    # ---------- 画 ----------

    def repaint(self) -> None:
        """按现在的相机重画坐标轴、网格和全部东西，并按远近重排绘制顺序。"""
        self.paint_axes()
        self.paint_floor()
        self.paint_mesh(self.mesh_M)
        for item in self.items.values():
            self.paint(item)
        self.sort()

    def sort(self) -> None:
        nodes = {id(i.node): i for i in self.items.values()}
        self.layer.children.sort(key=lambda n: nodes[id(n)].depth if id(n) in nodes else -math.inf)

    def paint_axes(self) -> None:
        lines, heads = Path(), Path()
        for i in range(3):
            e = np.eye(3)[i]
            a, b = self.s(e * self.lo[i]), self.s(e * (self.hi[i] + AXIS_OVER))
            lines.move_to(*a).line_to(*b)
            arrow_head(heads, a, b, HEAD * 0.75)
            self.put_label(self.axis_labels[i], b, b - self.s(np.zeros(3)), 0.12)
        self.axis_lines.path, self.axis_heads.path = lines, heads

    def grid_lines(self, m: np.ndarray, k_range: tuple[int, int, int, int]) -> Path:
        """z = 0 上的整数网格线经过 m 变换后裁在坐标范围里。"""
        path = Path()
        e = np.eye(3)
        for axis, (k0, k1) in ((0, k_range[:2]), (1, k_range[2:])):
            d = m @ e[1 - axis]
            if np.linalg.norm(d) < EPS:
                continue
            for k in range(k0, k1 + 1):
                seg = clip_line(m @ (e[axis] * k), d, self.lo, self.hi)
                if seg is not None:
                    path.move_to(*self.s(seg[0])).line_to(*self.s(seg[1]))
        return path

    def paint_floor(self) -> None:
        if self.floor_on:
            self.floor.path = self.grid_lines(np.eye(3), (int(self.lo[0]), int(self.hi[0]), int(self.lo[1]), int(self.hi[1])))

    def paint_mesh(self, m: np.ndarray) -> None:
        if self.mesh_on:
            r = 12
            self.mesh.path = self.grid_lines(m, (-r, r, -r, r))

    def paint(self, item: Item) -> None:
        g, V = item.shown, self.cam.V
        k = item.kind
        if k == "vector":
            a, b = g["a"], g["a"] + g["v"]
            A, B = self.s(a), self.s(b)
            shaft, head = item.node.children
            v2 = B - A
            n = float(np.linalg.norm(v2))
            if n < 1e-4:
                shaft.path, head.path = Path(), Path()
            else:
                u = v2 / n
                h = min(HEAD, n * 0.6)
                end = B - u * h * 0.6
                shaft.path = (dashed([(tuple(A), tuple(end))], 0.14, 0.09) if item.dashed
                              else Path().move_to(*A).line_to(*end))
                head.path = arrow_head(Path(), A, B, h)
            if np.linalg.norm(a) < EPS or n < 1e-4:  # 从原点出发：名字在箭头尖外面
                tip = v2 if n > 1e-4 else np.array([1.0, 1.0])
                side = np.array([-tip[1], tip[0]])
                self.put_label_free(item.label, [(B, tip, 0.18), (B, tip + side, 0.18), (B, tip - side, 0.18)])
            else:  # 尾巴不在原点（误差向量）：名字在箭杆中间、背着原点的一侧，不和别的箭头尖上的名字挤在一起
                u = v2 / n
                nrm = np.array([-u[1], u[0]])
                mid = (A + B) / 2
                if float(nrm @ (mid - self.s(np.zeros(3)))) < 0:
                    nrm = -nrm
                self.put_label_free(item.label, [(mid, nrm, 0.12), (mid, -nrm, 0.12)])
            item.depth = float((a + b) / 2 @ V)
        elif k == "point":
            P = self.s(g["p"])
            item.node.children[0].matrix = T.translate(*P)
            self.put_label(item.label, P, np.array([0.71, 0.71]), DOT + 0.06)
            item.depth = float(g["p"] @ V)
        elif k == "line":
            seg = clip_line(g["p"], g["d"], self.lo, self.hi) if np.linalg.norm(g["d"]) > EPS else None
            body = item.node.children[0]
            if seg is None:
                body.path = Path()
                self.show_label(item, False)
                return
            self.show_label(item, True)
            mid = np.clip(g["p"], self.lo, self.hi)
            p0, p1 = (mid + (q - mid) * item.grow for q in seg)
            P0, P1 = self.s(p0), self.s(p1)
            body.path = dashed([(tuple(P0), tuple(P1))], 0.16, 0.1) if item.dashed else Path().move_to(*P0).line_to(*P1)
            far = P1 if float(g["d"] @ np.array([1.0, 1.0, 1.0])) >= 0 else P0
            self.put_label(item.label, far, far - (P0 + P1) / 2, 0.1)
            item.depth = float((p0 + p1) / 2 @ V)
        elif k == "plane":
            fill, edge = item.node.children
            poly = clip_plane(g["p"], g["u"], g["w"], self.lo, self.hi)
            if poly is None:
                fill.path = Path()
                d = g["u"] if np.linalg.norm(g["u"]) >= np.linalg.norm(g["w"]) else g["w"]
                seg = clip_line(g["p"], d, self.lo, self.hi) if np.linalg.norm(d) > EPS else None
                edge.path = Path() if seg is None else Path().move_to(*self.s(seg[0])).line_to(*self.s(seg[1]))
                self.show_label(item, False)
                item.depth = float(g["p"] @ V)
                return
            self.show_label(item, True)
            P = self.s(poly)
            fill.path = polygon([tuple(q) for q in P])
            edge.path = polygon([tuple(q) for q in P])
            c = P.mean(axis=0)
            corners = sorted(P, key=lambda q: -float(q @ np.array([1.0, 0.35])))  # 先试右上的角
            self.put_label_free(item.label, [(c + (q - c) * 0.72, np.zeros(2), 0.0) for q in corners])
            item.depth = float(poly.mean(axis=0) @ V)
        elif k == "box":
            self.paint_box(item)
        elif k == "mark":
            p, e, f = g["p"], g["e"], g["f"]
            P = self.s(np.array([p + e, p + e + f, p + f]))
            item.node.children[0].path = Path().move_to(*P[0]).line_to(*P[1]).line_to(*P[2])
            item.depth = float((p + (e + f) / 2) @ V)

    def paint_box(self, item: Item) -> None:
        g = item.shown
        o, edges = g["o"], np.array([g["u"], g["v"], g["w"]])
        verts = np.array([o + sum(((i >> k) & 1) * edges[k] for k in range(3)) for i in range(8)])
        center = verts.mean(axis=0)
        V = self.cam.V
        faces = []  # (顶点序号, 是否朝向观者)
        flat = abs(float(np.linalg.det(edges))) < EPS
        for k in range(3):
            for bit in (0, 1):
                idx = [i for i in range(8) if ((i >> k) & 1) == bit]
                idx = [idx[0], idx[1], idx[3], idx[2]]  # 四个顶点按一圈的顺序
                q = verts[idx]
                n = np.cross(q[1] - q[0], q[3] - q[0])
                if float(n @ (q.mean(axis=0) - center)) < 0:
                    n = -n
                faces.append((idx, bit, k, not flat and float(n @ V) > 1e-9))
        fill, solid, hidden = item.node.children
        P = self.s(verts)
        path = Path()
        for idx, _bit, _k, front in faces:
            if front:
                path.move_to(*P[idx[0]])
                for i in idx[1:]:
                    path.line_to(*P[i])
                path.close()
        fill.path = path
        front_of = {(k, bit): front for _idx, bit, k, front in faces}
        sp, hp = Path(), []
        for i, j in BOX_EDGES:
            k = int(math.log2(i ^ j))
            others = [m for m in range(3) if m != k]
            seen = flat or any(front_of[(m, (i >> m) & 1)] for m in others)
            if seen:
                sp.move_to(*P[i]).line_to(*P[j])
            else:
                hp.append((tuple(P[i]), tuple(P[j])))
        solid.path = sp
        hidden.path = dashed(hp, 0.12, 0.08) if hp else Path()
        ymid = (P[:, 1].max() + P[:, 1].min()) / 2
        self.put_label_free(item.label, [(np.array([P[:, 0].max(), ymid]), np.array([1.0, 0.0]), 0.12),
                                         (np.array([P[:, 0].min(), ymid]), np.array([-1.0, 0.0]), 0.12),
                                         (np.array([(P[:, 0].max() + P[:, 0].min()) / 2, P[:, 1].min()]), np.array([0.0, -1.0]), 0.12)])
        item.depth = float(center @ V)

    # ---------- 建东西 ----------

    def new_item(self, kind: str, name: str, geo: dict, args: dict) -> Item:
        t = self.theme
        role = args.get("role") or DEFAULT_ROLE[kind]
        ink, fill = t.ink(role), t.color(role)
        if kind == "vector":
            w = VEC_W if not args.get("dashed") else 4
            parts = [PathNode(path=Path(), style=Style(stroke=ink, stroke_width=w)),
                     PathNode(path=Path(), style=Style(fill=ink, stroke=ink, stroke_width=1))]
        elif kind == "point":
            parts = [Group(PathNode(path=circle(DOT), style=Style(fill=ink)))]
        elif kind == "line":
            parts = [PathNode(path=Path(), style=Style(stroke=ink, stroke_width=LINE_W))]
        elif kind == "plane":
            parts = [PathNode(path=Path(), style=Style(fill=fill, fill_opacity=PLANE_FILL)),
                     PathNode(path=Path(), style=Style(stroke=ink, stroke_width=2, stroke_opacity=0.8))]
        elif kind == "box":
            parts = [PathNode(path=Path(), style=Style(fill=fill, fill_opacity=BOX_FILL)),
                     PathNode(path=Path(), style=Style(stroke=ink, stroke_width=EDGE_W)),
                     PathNode(path=Path(), style=Style(stroke=ink, stroke_width=2, stroke_opacity=HIDDEN_OPACITY))]
        else:  # mark
            parts = [PathNode(path=Path(), style=Style(stroke=t.FG, stroke_width=2.5))]
        node = Group(*parts, name=name)
        node.set_style(opacity=0.0)
        self.layer.add(node)
        lab = None
        if args.get("label"):
            lab = self.label_node(args["label"], ink)
            lab.set_style(opacity=0.0)
            self.labels.add(lab)
        geo = {k: np.asarray(v, dtype=float) for k, v in geo.items()}
        item = Item(kind, name, geo, node, role, lab, bool(args.get("dashed", False)),
                    shown={k: v.copy() for k, v in geo.items()})
        self.items[name] = item
        self.paint(item)  # 还看不见，先摆好（label 的大小、位置）
        return item

    def appear(self, item: Item, duration: float = 0.6, grow: Callable[[float], None] | None = None) -> list[Animation]:
        out: list[Animation] = [FadeTo(item.node, 1.0, 0.15 if grow else duration)]
        if grow is not None:
            def step(alpha: float) -> None:
                grow(alpha)
                self.paint(item)
                self.sort()

            out.append(_Func(step, duration))
        if item.label is not None:
            out.append(_Late(FadeTo(item.label, 1.0, 0.3), duration))
        return out

    def grow_vector(self, item: Item, a: np.ndarray, v: np.ndarray) -> Callable[[float], None]:
        def fn(s: float) -> None:
            item.shown = {"a": a, "v": v * max(s, 1e-3)}

        return fn

    # ---------- 动作 ----------

    def action(self, act: str, args: dict) -> list[Animation]:
        self.acted = True
        name = args.get("name")
        if act == "vector":
            a, v = np.asarray(args.get("at", (0, 0, 0)), dtype=float), np.asarray(args["v"], dtype=float)
            item = self.new_item("vector", name, {"a": a, "v": v}, args)
            return self.appear(item, 0.8, self.grow_vector(item, a, v))
        if act == "point":
            return self.appear(self.new_item("point", name, {"p": args["at"]}, args), 0.4)
        if act == "line":
            item = self.new_item("line", name, {"p": args.get("through", (0, 0, 0)), "d": args["dir"]}, args)

            def grow(s: float, item=item) -> None:
                item.grow = max(s, 1e-3)

            return self.appear(item, 0.9, grow)
        if act == "plane":
            if "normal" in args:
                u, w = plane_span(np.asarray(args["normal"], dtype=float))
            else:
                u, w = (np.asarray(s, dtype=float) for s in args["span"])
            item = self.new_item("plane", name, {"p": args.get("through", (0, 0, 0)), "u": u, "w": w}, args)
            return self.appear(item, 0.7)
        if act == "box":
            e = [np.asarray(x, dtype=float) for x in args["edges"]]
            item = self.new_item("box", name, {"o": args.get("at", (0, 0, 0)), "u": e[0], "v": e[1], "w": e[2]}, args)
            return self.appear(item, 0.7)
        if act == "project":
            return self.project(args)
        if act == "warp":
            return self.transform(np.array(args["matrix"], dtype=float))
        if act == "reset":
            return self.transform(np.linalg.inv(self.M))
        if act == "orbit":
            return self.orbit(args)
        if act == "relabel":
            item = self.items[name]
            old, new = item.label, self.label_node(args["label"], self.theme.ink(item.role))
            new.set_style(opacity=0.0)
            self.labels.add(new)
            item.label = new
            self.paint(item)  # 新名字还看不见，先摆好
            return ([FadeTo(old, 0.0, 0.3)] if old is not None else []) + [FadeTo(new, 1.0, 0.4)]
        if act == "remove":
            value = args.get("name")
            names = list(self.items) if value is None else ([value] if isinstance(value, str) else list(value))
            out: list[Animation] = []
            for n in names:
                item = self.items.pop(n)
                out.append(FadeTo(item.node, 0.0, 0.4))
                if item.label is not None:
                    out.append(FadeTo(item.label, 0.0, 0.4))
                mark = self.items.pop(f"{n}_perp", None)  # 投影的直角记号跟着投影走
                if mark is not None:
                    out.append(FadeTo(mark.node, 0.0, 0.4))
            return out
        raise KeyError(f"组件 {self.name} 没有动作 {act}")

    def project(self, args: dict) -> list[Animation]:
        src, onto = self.items[args["name"]], self.items[args["onto"]]
        v = src.geo["v"]
        p = project_onto(v, onto)
        e = v - p
        anims: list[Animation] = []
        err = None
        if args.get("error"):
            err_args = {"role": args.get("error_role", "HL"), "label": args.get("error_label"), "dashed": True}
            err = self.new_item("vector", args["error"], {"a": p, "v": e}, err_args)
            # 误差从原来那支的尖往下落到投影的尖：尾巴从 v 走到 p
            def drop(s: float, err=err) -> None:
                tail = v + (p - v) * max(s, 1e-3)
                err.shown = {"a": tail, "v": v - tail}

            anims += self.appear(err, 0.8, drop)
        proj = self.new_item("vector", args["as"], {"a": np.zeros(3), "v": p}, args)
        grow = self.grow_vector(proj, np.zeros(3), p)
        anims += [_Delay(a, 0.8) for a in self.appear(proj, 0.8, grow)]
        ne, npn = float(np.linalg.norm(e)), float(np.linalg.norm(p))
        if ne > 1e-6 and npn > 1e-6:
            size = min(MARK, 0.4 * ne, 0.4 * npn)
            mark = self.new_item("mark", f"{args['as']}_perp", {"p": p, "e": e / ne * size, "f": -p / npn * size}, {})
            anims += [_Delay(a, 1.6) for a in self.appear(mark, 0.3)]
        return anims

    def transform(self, m: np.ndarray) -> list[Animation]:
        f = tween(m)
        out: list[Animation] = []
        moves = []
        for item in list(self.items.values()):
            if item.kind == "mark":  # 变形之后不再是直角：淡出
                out.append(FadeTo(item.node, 0.0, 0.3))
                del self.items[item.name]
                continue
            g0 = dict(item.geo)
            moves.append((item, g0))
            item.geo = moved(g0, m)
        m0 = self.M.copy()
        self.M = m @ self.M

        def step(alpha: float) -> None:
            mt = f(alpha)
            for item, g0 in moves:
                item.shown = moved(g0, mt)
            self.mesh_M = mt @ m0
            self.repaint()

        out.insert(0, _Func(step, 1.8).sampled())
        return out

    def orbit(self, args: dict) -> list[Animation]:
        v0 = self.view
        v1 = orbit_target(v0, args)
        self.view = v1
        turn = max(abs(v1[0] - v0[0]), abs(v1[1] - v0[1]))

        def step(alpha: float) -> None:
            self.cam = Camera(v0[0] + (v1[0] - v0[0]) * alpha, v0[1] + (v1[1] - v0[1]) * alpha)
            self.repaint()

        n = max(4, math.ceil(turn / 30.0))
        return [_Func(step, min(4.0, 1.0 + turn / 90.0)).sampled([i / n for i in range(1, n)])]


def arrow_head(path: Path, a: np.ndarray, b: np.ndarray, h: float) -> Path:
    """在画面上从 a 指向 b 的箭头尖（尖在 b）。"""
    v = b - a
    n = float(np.linalg.norm(v))
    if n < 1e-6:
        return path
    u = v / n
    nrm = np.array([-u[1], u[0]]) * HEAD_W * h / HEAD
    base = b - u * h
    return path.move_to(*b).line_to(*(base + nrm)).line_to(*(base - nrm)).close()


class _Delay(Animation):
    """把一个动画往后推 delay 秒（整段长度 = delay + 原来的长度）。"""

    def __init__(self, anim: Animation, delay: float):
        super().__init__(Node(), delay + anim.duration, easing.linear)
        self.anim, self.start = anim, delay / (delay + anim.duration)
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
