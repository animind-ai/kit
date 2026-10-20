"""坐标平面（墨线实现）：向量的首尾相接、复数乘 i 转 90°、矩阵把整个平面变形。

plane：x、y 是坐标范围（整数 [起, 止]，要含 0），画淡的单位网格（grid，默认 true）、坐标轴和整数刻度（numbers，默认 true）。
complex: true 时纵轴刻度写成 i、2i、−i（复平面），轴名默认「实轴」「虚轴」；axis_labels 可改两个轴名（字符串，可混排公式）。
mesh: true 时另有一层彩色网格（mesh_role，默认 B）跟着 warp 变形，每一帧把变形后的网格线裁在坐标范围里，
看起来是整个平面在变（动作 warp）；原来的淡网格留在下面当参照。

画在平面上的东西都有名字（name），之后的动作按名字找。坐标都是数学坐标：
- vector {name, v: [x, y], at: [x, y]（尾巴，默认原点）, role（默认 A）, label, side: l / r（名字在箭头的左 / 右边，默认 l）, along（名字挨着箭杆的哪一处，0 尾 … 1 头，默认 0.5）, dashed}：
  从尾巴长出一支箭头，v 是分量（终点 = at + v）；
- slide {name, at, copy}：箭头平移到尾巴在 at（分量不变）；写了 copy（新名字）时原来那支不动，滑过去的是一支虚线的副本；
- point {name, at, label, role（默认 HL）, dir（名字在点的哪一边：u d l r ul ur dl dr，默认 ur）, fixed}：一个点；
  fixed: true 时它钉在画面上，整个平面 rotate / scale / warp / reset 时不跟着走（标出"原来在这里"、比较两种结果用）；
- shape {name, points: [[x, y], …], role（默认 C）, label, fill（默认 true；false 只描粗边）}：填色多边形（单位正方形、小旗子），跟着变换走；
  label 写在多边形的重心上；
- rotate {name（一个名字或名字列表；不写 = 整个平面）, angle（度，逆时针为正）, label, trace（默认 true）}：
  绕原点转；写了 name 时画出第一个东西的终点扫过的圆弧，弧旁写 label（例如 \\times i）；
- scale {name（同上）, by}：以原点为中心放大 by 倍；
- warp {matrix: [[a, b], [c, d]]}：整个平面（彩色网格和所有箭头、点、多边形）按矩阵变形，和之前的变换复合
  （先做的在右边：warp A 再 warp B，结果是 BA）；纯旋转按角度转过去，其他矩阵按 (1−t)I + tM 渐变；
- reset：整个平面变回原样（之前所有 warp 的逆）；
- relabel {name, label}：换名字（旧的淡出、新的淡入，位置跟着箭头）；
- remove {name}（一个或列表；不写 = 全部箭头、点、多边形和弧）：淡出。

暴露：axes（坐标轴和刻度，一项）、grid（淡网格）、mesh（彩色网格）、items（画上去的全部东西，一项）。

文字（刻度、名字）都垫一块底色：网格线从字底下过，不会穿过字（inspect 不报 cross）。
刻度和名字标了 font_floor = "label"，后端排版后按实际缩放把 label_size 抬到字号下限（同数轴）。
颜色、透明度、几何都在动画播放时才改：一个 beat 的动作先全部建好再播放，建动作时只推演状态。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from moxian.anim import Animation, Draw, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import Path, circle, polygon, rounded_rect

from .dissect import dashed
from .dots import FadeTo, reserve
from .text import mmath, rich
from .widgets import Built, Widget

UNIT = 1.0  # 一个单位的长度（画面单位，排版前）
HEAD = 0.3  # 箭头尖的长度
HEAD_W = 0.13  # 箭头尖的半宽
VEC_W = 6  # 箭杆粗细（1080p 像素）
DOT = 0.1  # 点的半径
PAD = 0.06  # 文字底色比字大出的边
LABEL_GAP = 0.32  # 箭头名字离箭杆中点的距离
LABEL_DIRS = {
    "u": (0.0, 1.0), "d": (0.0, -1.0), "l": (-1.0, 0.0), "r": (1.0, 0.0),
    "ul": (-0.71, 0.71), "ur": (0.71, 0.71), "dl": (-0.71, -0.71), "dr": (0.71, -0.71),
}


def rotation(deg: float) -> np.ndarray:
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])


def rotation_angle(m: np.ndarray) -> float | None:
    """m 是纯旋转（正交、行列式 1、不是恒等）时返回转角（弧度），否则 None。"""
    if np.allclose(m @ m.T, np.eye(2), atol=1e-9) and np.linalg.det(m) > 0 and not np.allclose(m, np.eye(2)):
        return math.atan2(m[1, 0], m[0, 0])
    return None


def tween_matrix(m: np.ndarray) -> Callable[[float], np.ndarray]:
    """恒等 → m 的过程：旋转按角度转，其他按 (1−t)I + tM。"""
    angle = rotation_angle(m)
    if angle is not None:
        return lambda t: rotation(math.degrees(angle * t))
    return lambda t: (1 - t) * np.eye(2) + t * m


def clip_line(p: np.ndarray, d: np.ndarray, box: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray] | None:
    """直线 p + s·d 裁在矩形 box 里的那一段（Liang–Barsky）；不相交时 None。"""
    lo, hi = -math.inf, math.inf
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


@dataclass
class Item:
    kind: str  # vector / point / shape / arc
    name: str
    pts: np.ndarray  # 数学坐标：vector [尾, 头]；point [点]；shape 顶点
    node: Group
    label: Node | None = None
    side: float = 1.0  # vector：名字在左边 +1、右边 −1
    dir: tuple[float, float] = (0.71, 0.71)  # point：名字的方向
    dashed: bool = False
    extra: dict = field(default_factory=dict)


class _Geo(Animation):
    """按 alpha 重画一组东西：pts_of(item, alpha) 给出这一帧的数学坐标。"""

    def __init__(self, plane: Plane, moves: list[tuple[Item, Callable[[float], np.ndarray]]],
                 mesh: Callable[[float], np.ndarray] | None = None, duration: float = 1.0):
        super().__init__(Node(), duration, easing.smooth)
        self.plane, self.mesh = plane, mesh
        # 名字在建动作时定下：之后 relabel 换了名字，这一段动画挪的还是当时的那个
        self.moves = [(item, f, item.label) for item, f in moves]

    def interpolate(self, alpha: float) -> None:
        for item, f, label in self.moves:
            self.plane.paint(item, f(alpha), label)
        if self.mesh is not None:
            self.plane.paint_mesh(self.mesh(alpha))


class Plane(Widget):
    name = "plane"
    LABEL = 34  # 刻度和名字的默认字号（kit 字号）；opts["label_size"] 覆盖（后端按字号下限抬）
    label_floor = "label"

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.acted = False
        self.label_size = float(self.opts.get("label_size", self.LABEL))
        (x0, x1), (y0, y1) = d["x"], d["y"]
        self.box = (float(x0), float(y0), float(x1), float(y1))
        self.complex = bool(d.get("complex", False))
        node = Group(name=self.obj_id)
        U = UNIT
        numbers = d.get("numbers", True)
        margin = (mmath("8", self.label_size, t.FG).height + 0.35) if numbers else 0.35
        self.grid = Group(name="grid")
        if d.get("grid", True):
            g = Path()
            for x in range(int(x0), int(x1) + 1):
                g.move_to(x * U, y0 * U).line_to(x * U, y1 * U)
            for y in range(int(y0), int(y1) + 1):
                g.move_to(x0 * U, y * U).line_to(x1 * U, y * U)
            self.grid.add(PathNode(path=g, style=Style(stroke=t.DIM, stroke_width=2, stroke_opacity=0.35)))
        self.mesh = PathNode(path=Path(), style=Style(stroke=t.color(d.get("mesh_role"), t.B), stroke_width=3,
                                                       stroke_opacity=0.75), name="mesh")
        self.mesh_on = bool(d.get("mesh", False))
        self.M = np.eye(2)  # 到目前为止的变换（建动作时推演）
        if self.mesh_on:
            self.paint_mesh(self.M)
        self.axes = Group(name="axes")
        ax = Path().move_to(x0 * U - 0.2, 0.0).line_to(x1 * U + 0.2, 0.0)
        ax.move_to(0.0, y0 * U - 0.2).line_to(0.0, y1 * U + 0.2)
        self.axes.add(PathNode(path=ax, style=Style(stroke=t.FG, stroke_width=3)))
        heads = Path()
        for tip, (ux, uy) in (((x1 * U + 0.2, 0.0), (1.0, 0.0)), ((0.0, y1 * U + 0.2), (0.0, 1.0))):
            heads.move_to(tip[0] + ux * 0.18, tip[1] + uy * 0.18)
            heads.line_to(tip[0] - uy * 0.09, tip[1] + ux * 0.09).line_to(tip[0] + uy * 0.09, tip[1] - ux * 0.09).close()
        self.axes.add(PathNode(path=heads, style=Style(fill=t.FG, stroke=t.FG, stroke_width=1)))
        if numbers:
            for x in range(int(x0), int(x1) + 1):
                if x != 0:
                    self.axes.add(self.padded(str(x), t.DIM, (x * U, -0.12), (0.0, -1.0)))
            for y in range(int(y0), int(y1) + 1):
                if y != 0:
                    tex = (("" if abs(y) == 1 else str(abs(y))) + "i") if self.complex else str(abs(y))
                    tex = ("-" if y < 0 else "") + tex
                    self.axes.add(self.padded(tex, t.DIM, (-0.12, y * U), (-1.0, 0.0)))
            self.axes.add(self.padded("0", t.DIM, (-0.12, -0.12), (-0.71, -0.71)))
        # 轴名放在网格外面（横轴箭头右边、纵轴箭头上面），转动的箭头和弧不会扫过它们
        box_l, box_r = x0 * U - margin, x1 * U + margin
        box_b, box_t = y0 * U - margin, y1 * U + margin
        names = d.get("axis_labels") or (["实轴", "虚轴"] if self.complex else None)
        if names:
            xl = self.padded_rich(names[0], t.DIM, (x1 * U + 0.45, 0.0), (1.0, 0.0))
            yl = self.padded_rich(names[1], t.DIM, (0.0, y1 * U + 0.45), (0.0, 1.0))
            self.axes.add(xl, yl)
            box_r = max(box_r, xl.bbox().x1 + 0.1)
            box_t = max(box_t, yl.bbox().y1 + 0.1)
        node.add(reserve(box_r - box_l, box_t - box_b, (box_l + box_r) / 2, (box_b + box_t) / 2))
        self.layer = Group(name="items")
        self.labels = Group(name="labels")  # 名字画在所有箭头上面
        node.add(self.grid, self.mesh, self.axes, self.layer, self.labels)
        self.items: dict[str, Item] = {}
        return Built(node, parts={"axes": [self.axes], "grid": [self.grid], "mesh": [self.mesh],
                                  "items": [self.layer]})

    # ---------- 文字 ----------

    def padded(self, tex: str, color: str, at, toward) -> Group:
        """一段公式垫上底色，放在 at 点朝 toward 方向的一侧（toward 为 (0,0) 时以 at 为中心）。"""
        return self._pad(mmath(tex, self.label_size, color), at, toward)

    def padded_rich(self, text: str, color: str, at, toward) -> Group:
        return self._pad(rich(text, self.theme, size=self.label_size, color=color).node, at, toward)

    def _pad(self, body: Node, at, toward) -> Group:
        body.font_floor = self.label_floor
        bg = PathNode(path=rounded_rect(body.width + 2 * PAD, body.height + 2 * PAD, 0.06),
                      style=Style(fill=self.theme.BG))
        bg.move_to(body.center())
        g = Group(bg, body)
        place(g, np.asarray(at, dtype=float), np.asarray(toward, dtype=float))
        return g

    def label_node(self, tex: str, color: str) -> Group:
        body = mmath(tex, self.label_size, color)
        body.font_floor = self.label_floor
        bg = PathNode(path=rounded_rect(body.width + 2 * PAD, body.height + 2 * PAD, 0.06),
                      style=Style(fill=self.theme.BG, fill_opacity=0.92))
        bg.move_to(body.center())
        g = Group(bg, body)
        g.matrix = T.translate(*(-g.center()))  # 中心在局部原点，摆放时只改平移
        return Group(g)

    # ---------- 几何 ----------

    def w(self, p) -> np.ndarray:
        return np.asarray(p, dtype=float) * UNIT

    def paint(self, item: Item, pts: np.ndarray, label: Node | None = None) -> None:
        """按数学坐标 pts 重画 item（箭头、点、多边形），名字 label 跟着挪。"""
        P = self.w(pts)
        if item.kind == "vector":
            shaft, head = item.node.children
            a, b = P[0], P[1]
            v = b - a
            n = float(np.linalg.norm(v))
            if n < 1e-6:
                shaft.path, head.path = Path(), Path()
            else:
                u = v / n
                h = min(HEAD, n * 0.6)
                hw = HEAD_W * h / HEAD
                base = b - u * h
                if item.dashed:
                    shaft.path = dashed([(tuple(a), tuple(b - u * h * 0.6))], 0.16, 0.1)
                else:
                    shaft.path = Path().move_to(*a).line_to(*(b - u * h * 0.6))
                nrm = np.array([-u[1], u[0]])
                head.path = polygon([tuple(b), tuple(base + nrm * hw), tuple(base - nrm * hw)])
            if label is not None:
                self.place_label(item, pts, label)
        elif item.kind == "point":
            item.node.children[0].matrix = T.translate(*P[0])
            if label is not None:
                self.place_label(item, pts, label)
        elif item.kind == "shape":
            item.node.children[0].path = polygon([tuple(p) for p in P])
            if label is not None:
                self.place_label(item, pts, label)

    def place_label(self, item: Item, pts: np.ndarray, lab: Node) -> None:
        P = self.w(pts)
        if item.kind == "vector":
            a, b = P[0], P[1]
            v = b - a
            n = float(np.linalg.norm(v))
            u = v / n if n > 1e-6 else np.array([1.0, 0.0])
            nrm = np.array([-u[1], u[0]]) * item.side
            half = np.array([lab.width, lab.height]) / 2
            reach = LABEL_GAP + float(np.dot(np.abs(nrm), half))
            lab.matrix = T.translate(*(a + (b - a) * item.extra.get("along", 0.5) + nrm * reach))
        elif item.kind == "shape":
            lab.matrix = T.translate(*P.mean(axis=0))
        elif item.kind == "point":
            d = np.array(item.dir)
            half = np.array([lab.width, lab.height]) / 2
            reach = DOT + 0.08 + float(np.dot(np.abs(d), half)) / max(1e-6, float(np.linalg.norm(d)))
            lab.matrix = T.translate(*(P[0] + d / np.linalg.norm(d) * reach))

    def paint_mesh(self, m: np.ndarray) -> None:
        """变换 m 下的整数网格线，裁在坐标范围里。"""
        box = tuple(v * UNIT for v in self.box)
        path = Path()
        if abs(np.linalg.det(m)) < 1e-6:
            self.mesh.path = path
            return
        inv = np.linalg.inv(m)
        corners = np.array([[self.box[0], self.box[1]], [self.box[2], self.box[1]], [self.box[2], self.box[3]],
                            [self.box[0], self.box[3]]])
        pre = corners @ inv.T  # 坐标范围的四个角在变换前的位置：只有这些整数线会穿过画面
        for axis in (0, 1):
            lo, hi = math.floor(pre[:, axis].min()), math.ceil(pre[:, axis].max())
            for k in range(max(lo, -60), min(hi, 60) + 1):
                p = m[:, axis] * k * UNIT
                d = m[:, 1 - axis]
                seg = clip_line(p, d, box)
                if seg is not None:
                    path.move_to(*seg[0]).line_to(*seg[1])
        self.mesh.path = path

    # ---------- 动作 ----------

    def names(self, value) -> list[Item]:
        if value is None:
            return [i for i in self.items.values() if i.kind != "arc" and not i.extra.get("fixed")]
        names = [value] if isinstance(value, str) else list(value)
        return [self.items[n] for n in names]

    def new_vector(self, name: str, tail, v, role: str | None, label: str | None, side: str, is_dashed: bool,
                   along: float = 0.5) -> Item:
        t = self.theme
        color = t.ink(role or "A")  # HL 在浅底上太淡：箭头和名字一样用 HL_INK
        shaft = PathNode(path=Path(), style=Style(stroke=color, stroke_width=VEC_W if not is_dashed else 4))
        head = PathNode(path=Path(), style=Style(fill=color, stroke=color, stroke_width=1))
        node = Group(shaft, head, name=name)
        node.set_style(opacity=0.0)
        self.layer.add(node)
        lab = None
        if label:
            lab = self.label_node(label, t.ink(role or "A"))
            lab.set_style(opacity=0.0)
            self.labels.add(lab)
        tail = np.asarray(tail, dtype=float)
        item = Item("vector", name, np.array([tail, tail + np.asarray(v, dtype=float)]), node, lab,
                    side=1.0 if side == "l" else -1.0, dashed=is_dashed, extra={"role": role, "along": along})
        self.items[name] = item
        return item

    def appear(self, item: Item, grow: Callable[[float], np.ndarray], duration: float = 0.8) -> list[Animation]:
        anims: list[Animation] = [FadeTo(item.node, 1.0, 0.15), _Geo(self, [(item, grow)], duration=duration)]
        if item.label is not None:
            anims.append(_Late(FadeTo(item.label, 1.0, 0.3), duration))
        return anims

    def action(self, act: str, args: dict) -> list[Animation]:
        self.acted = True
        t = self.theme
        if act == "vector":
            item = self.new_vector(args["name"], args.get("at", (0, 0)), args["v"], args.get("role"), args.get("label"),
                                   args.get("side", "l"), bool(args.get("dashed", False)), float(args.get("along", 0.5)))
            a, b = item.pts.copy()
            return self.appear(item, lambda s: np.array([a, a + (b - a) * max(s, 1e-3)]))
        if act == "slide":
            item = self.items[args["name"]]
            a, b = item.pts.copy()
            at = np.asarray(args["at"], dtype=float)
            if args.get("copy"):
                ghost = self.new_vector(args["copy"], a, b - a, args.get("role", item.extra.get("role")),
                                        args.get("label"), "r" if item.side < 0 else "l", True)
                ghost.pts = np.array([at, at + b - a])
                anims: list[Animation] = [FadeTo(ghost.node, 1.0, 0.2),
                                          _Geo(self, [(ghost, lambda s: np.array([a, b]) + (at - a) * s)])]
                if ghost.label is not None:
                    anims.append(_Late(FadeTo(ghost.label, 1.0, 0.3), 1.0))
                return anims
            item.pts = np.array([at, at + b - a])
            return [_Geo(self, [(item, lambda s: np.array([a, b]) + (at - a) * s)])]
        if act == "point":
            role = args.get("role")
            color = t.color(role, t.HL_INK)
            dot = PathNode(path=circle(DOT), style=Style(fill=color))
            node = Group(Group(dot), name=args["name"])
            node.set_style(opacity=0.0)
            self.layer.add(node)
            lab = None
            if args.get("label"):
                lab = self.label_node(args["label"], t.ink(role or "HL"))
                lab.set_style(opacity=0.0)
                self.labels.add(lab)
            item = Item("point", args["name"], np.array([args["at"]], dtype=float), node, lab,
                        dir=LABEL_DIRS[args.get("dir", "ur")], extra={"role": role or "HL", "fixed": bool(args.get("fixed", False))})
            self.items[item.name] = item
            self.paint(item, item.pts, lab)  # 新节点还看不见，建动作时就能摆好
            out: list[Animation] = [FadeTo(node, 1.0, 0.4)]
            if lab is not None:
                out.append(FadeTo(lab, 1.0, 0.4))
            return out
        if act == "shape":
            color = t.color(args.get("role"), t.C)
            filled = args.get("fill", True)
            body = PathNode(path=Path(), style=Style(fill=color if filled else None, fill_opacity=t.fill_opacity(),
                                                     stroke=color, stroke_width=3 if filled else 6))
            node = Group(body, name=args["name"])
            node.set_style(opacity=0.0)
            # 多边形垫在箭头下面；多边形之间后画的在上面
            shapes = sum(1 for i in self.items.values() if i.kind == "shape" and i.node in self.layer.children)
            self.layer.children.insert(shapes, node)
            lab = None
            if args.get("label"):
                lab = self.label_node(args["label"], t.ink(args.get("role") or "C"))
                lab.set_style(opacity=0.0)
                self.labels.add(lab)
            item = Item("shape", args["name"], np.array(args["points"], dtype=float), node, lab)
            self.items[item.name] = item
            self.paint(item, item.pts, lab)  # 新节点还看不见，建动作时就能摆好
            return [FadeTo(node, 1.0, 0.5)] + ([FadeTo(lab, 1.0, 0.5)] if lab is not None else [])
        if act in ("rotate", "scale"):
            m = rotation(float(args["angle"])) if act == "rotate" else float(args["by"]) * np.eye(2)
            whole = args.get("name") is None
            anims = self.transform(m, self.names(args.get("name")), whole)
            if act == "rotate" and not whole and args.get("trace", True):
                anims += self.trace(self.names(args["name"])[0], float(args["angle"]), args.get("label"))
            return anims
        if act == "warp":
            return self.transform(np.array(args["matrix"], dtype=float), self.names(None), True)
        if act == "reset":
            return self.transform(np.linalg.inv(self.M), self.names(None), True)
        if act == "relabel":
            item = self.items[args["name"]]
            old = item.label
            new = self.label_node(args["label"], t.ink(item.extra.get("role") or "A"))
            new.set_style(opacity=0.0)
            self.labels.add(new)
            item.label = new
            self.place_label(item, item.pts, new)  # 新节点还看不见，建动作时就能摆好
            out: list[Animation] = [FadeTo(new, 1.0, 0.4)]
            if old is not None:
                out.insert(0, FadeTo(old, 0.0, 0.3))
            return out
        if act == "remove":
            value = args.get("name")
            items = list(self.items.values()) if value is None else self.names(value)
            out = []
            for item in items:
                out.append(FadeTo(item.node, 0.0, 0.4))
                if item.label is not None:
                    out.append(FadeTo(item.label, 0.0, 0.4))
                del self.items[item.name]
            return out
        raise KeyError(f"组件 {self.name} 没有动作 {act}")

    def transform(self, m: np.ndarray, items: list[Item], whole: bool) -> list[Animation]:
        f = tween_matrix(m)
        moves = []
        for item in items:
            p0 = item.pts.copy()
            moves.append((item, lambda s, p0=p0: p0 @ f(s).T))
            item.pts = p0 @ m.T
        mesh = None
        if whole and self.mesh_on:
            m0 = self.M.copy()
            mesh = lambda s: f(s) @ m0  # noqa: E731
        if whole:
            self.M = m @ self.M
        # 线性变换（rotate / scale / warp / reset）是连续的：中途采样。slide、vector 长出来不标（刚体搬动）
        out: list[Animation] = [_Geo(self, moves, mesh, duration=1.6).sampled()]
        if whole:  # 整个平面变形时，之前画的圆弧不再对得上：淡出
            for arc in [i for i in self.items.values() if i.kind == "arc"]:
                out += [FadeTo(arc.node, 0.0, 0.3)] + ([FadeTo(arc.label, 0.0, 0.3)] if arc.label is not None else [])
                del self.items[arc.name]
        return out

    def trace(self, item: Item, angle: float, label: str | None) -> list[Animation]:
        """转的时候画出 item 终点扫过的圆弧（在 transform 之前推演的位置上：item.pts 已经是转完的）。"""
        t = self.theme
        end = item.pts[-1]
        start = end @ rotation(-angle).T
        r = float(np.linalg.norm(start)) * UNIT
        a0 = math.atan2(start[1], start[0])
        sweep = math.radians(angle)
        steps = max(8, int(abs(angle) / 4))
        path = Path()
        for k in range(steps + 1):
            a = a0 + sweep * k / steps
            (path.move_to if k == 0 else path.line_to)(r * math.cos(a), r * math.sin(a))
        arc = PathNode(path=path, style=Style(stroke=t.DIM, stroke_width=3, opacity=0.0))
        node = Group(arc, name=f"arc{len(self.items)}")
        self.layer.add(node)
        lab = None
        out: list[Animation] = [_Show(arc, 1.6)]
        if label:
            lab = self.label_node(label, t.DIM)
            mid = a0 + sweep / 2
            half = np.array([lab.width, lab.height]) / 2
            u = np.array([math.cos(mid), math.sin(mid)])
            reach = r + 0.15 + float(np.dot(np.abs(u), half))
            lab.matrix = T.translate(*(u * reach))
            lab.set_style(opacity=0.0)
            self.labels.add(lab)
            out.append(_Late(FadeTo(lab, 1.0, 0.4), 1.2))
        self.items[node.name] = Item("arc", node.name, np.zeros((1, 2)), node, lab)
        return out


def place(node: Node, at: np.ndarray, toward: np.ndarray) -> None:
    """node 的包围盒贴在 at 点朝 toward 的一侧（toward 每个分量取 −1、0、1 的方向）。"""
    b = node.bbox()
    half = np.array([b.width, b.height]) / 2
    node.shift(at + toward * half - b.center)


class _Show(Draw):
    """圆弧边画边显出来：透明度在播放时才改。"""

    def begin(self) -> None:
        self.node.set_style(opacity=1.0)
        super().begin()


class _Late(Animation):
    """把一个短动画放到一段时间的末尾播（名字在箭头长好以后再出现）。"""

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
