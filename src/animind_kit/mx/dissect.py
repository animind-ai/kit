"""剖分组件 dissect（墨线实现）：图形剪开、刚体移动、拼成另一个图形。

几何（切块、吸附、扇形交错排列、拼合轮廓）全部来自 scene_spec.dissect，检查器和渲染器用同一份算法。
这里只负责：把块画出来、按动作推演每块的位姿（图形坐标里的 3×3 仿射矩阵）、把位姿变化做成动画。

节点结构：

    node（组件根，布局求解器只动它）
      └ fig（图形坐标 → 画面：缩放 k、把 view 的中心移到原点）
          ├ frame   看不见的 view 矩形，给布局占位（块挪到哪都不出这块地方）
          ├ 各块    Group，matrix = 位姿；里面是形状和标签（标签每帧反变换回来，始终正立、字号不变）
          ├ 切口    虚线，cut 时短暂出现
          ├ 参考线  虚线 + 标签（show obj#名字）
          └ 轮廓    merge 时画出的外轮廓 + 标签

暴露的片段（obj#名字）：每一块、每条参考线、每个合并轮廓，所以内置的 show / hide / focus / mark 都能直接用。
动作在编译时按顺序推演位姿，动画只在播放时把节点推到编译时算好的位置；块之间在移动途中重叠是预期的。
"""

from __future__ import annotations

import math

import numpy as np
from moxian.anim import Animation, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import Path as VPath
from moxian.path import circle, polygon
from scene_spec import dissect as G

from .text import rich
from .widgets import Built, Widget

FIT_W, FIT_H = 9.0, 5.0  # 图形默认撑到的大小（画面单位）；放不下时布局再缩
LABEL = 32
STROKE = 3.0
DASH, GAP = 0.16, 0.11  # 虚线（画面单位）
LABEL_GAP = 0.1  # 边长标签离边的距离（画面单位）
DOT = 0.1


def mat(a: G.Aff) -> np.ndarray:
    return np.array(a.rows(), dtype=float)


def aff(m: np.ndarray) -> G.Aff:
    return G.Aff(m[0, 0], m[0, 1], m[0, 2], m[1, 0], m[1, 1], m[1, 2])


def apply(m: np.ndarray, p) -> np.ndarray:
    return (m @ np.array([p[0], p[1], 1.0]))[:2]


def arc_path(path: VPath, c, r: float, a0: float, a1: float) -> VPath:
    """从当前点（应在 a0 处）沿圆弧画到 a1，每段不超过 90°，三次贝塞尔近似。"""
    n = max(1, math.ceil(abs(a1 - a0) / 90 - 1e-9))
    step = (a1 - a0) / n
    for i in range(n):
        t0, t1 = math.radians(a0 + i * step), math.radians(a0 + (i + 1) * step)
        k = 4 / 3 * math.tan((t1 - t0) / 4) * r
        p0 = (c[0] + r * math.cos(t0), c[1] + r * math.sin(t0))
        p1 = (c[0] + r * math.cos(t1), c[1] + r * math.sin(t1))
        path.cubic_to(p0[0] - k * math.sin(t0), p0[1] + k * math.cos(t0),
                      p1[0] + k * math.sin(t1), p1[1] - k * math.cos(t1), *p1)
    return path


def piece_path(piece: G.Piece) -> VPath:
    if piece.kind == "poly":
        return polygon(piece.points)
    c = piece.points[0]
    if piece.full:
        return circle(piece.r).transformed(T.translate(*c))
    p = VPath().move_to(*c)
    p.line_to(*G.polar(c, piece.r, piece.a0))
    arc_path(p, c, piece.r, piece.a0, piece.a1)
    return p.close()


def dashed(segments, dash: float, gap: float) -> VPath:
    path = VPath()
    for p, q in segments:
        length = G.dist(p, q)
        if length < 1e-9:
            continue
        ux, uy = (q[0] - p[0]) / length, (q[1] - p[1]) / length
        n = max(1, round((length + gap) / (dash + gap)))
        d = (length - (n - 1) * gap) / n  # 两端都是实线段
        s = 0.0
        for _ in range(n):
            path.move_to(p[0] + ux * s, p[1] + uy * s)
            e = min(length, s + d)
            path.line_to(p[0] + ux * e, p[1] + uy * e)
            s += d + gap
    return path


class _Label:
    """块上的一个标签：anchor 是块原坐标里的点；edge 不为空时是边长标签，要往块外面挪开。"""

    def __init__(self, node: Node, anchor, edge=None):
        self.node, self.anchor, self.edge = node, anchor, edge
        b = node.bbox()
        self.size = (b.width, b.height)


class _Pose(Animation):
    """把一块的位姿按 fn(alpha) 推进；每帧顺带摆正它的标签。"""

    def __init__(self, widget: Dissect, name: str, fn, duration: float = 1.0, flip: bool = False):
        super().__init__(widget.nodes[name], duration, easing.smooth)
        self.widget, self.name, self.fn, self.flip = widget, name, fn, flip

    def interpolate(self, alpha: float) -> None:
        m = self.fn(alpha)
        self.node.matrix = m
        self.widget.sync_labels(self.name, fade=self.flip)


class _Staged(Animation):
    """若干 (开始, 结束, 动画) 按整段时长的比例播放；各自用自己的缓动。"""

    def __init__(self, windows: list[tuple[float, float, Animation]], duration: float = 1.0):
        super().__init__(Node(), duration, easing.linear)
        self.windows = windows
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


class _Call(Animation):
    """在 alpha 越过某一点时做一件事（例如把被切的块换成切出来的几块）。"""

    def __init__(self, fn, duration: float = 1.0):
        super().__init__(Node(), duration, easing.linear)
        self.fn, self.done = fn, False

    def interpolate(self, alpha: float) -> None:
        if not self.done:
            self.fn()
            self.done = True


class _Opacity(Animation):
    def __init__(self, node: Node, start: float, end: float, duration: float = 0.4):
        super().__init__(node, duration, easing.smooth)
        self.start, self.end = start, end

    def interpolate(self, alpha: float) -> None:
        self.node.set_style(opacity=self.start + (self.end - self.start) * alpha)


class Dissect(Widget):
    name = "dissect"

    def build(self) -> Built:
        d = self.definition
        plan = G.plan_of(d)
        if plan.problems:
            code, path, message = plan.problems[0]
            raise ValueError(f"dissect 对象 {self.obj_id} 的 {path}: {message}（{code}）")
        self.plan = plan
        t = self.theme
        self.base_role = d.get("role", "A")

        # view：给图形留出的范围
        if "view" in d:
            (x0, y0), (x1, y1) = d["view"]
        else:
            pts = [p for pc in plan.pieces.values() if pc.born is None for p in pc.outline()]
            pts += [tuple(g[k]) for g in plan.guides.values() for k in ("from", "to")]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            pad = 0.08 * max(x1 - x0, y1 - y0)
            x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
        self.view = (x0, y0, x1, y1)
        self.k = min(FIT_W / max(1e-6, x1 - x0), FIT_H / max(1e-6, y1 - y0))
        k = self.k
        fig = Group(name="fig")
        fig.matrix = T.scale(k) @ T.translate(-(x0 + x1) / 2, -(y0 + y1) / 2)
        self.fig = fig
        frame = PathNode(path=polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]), style=Style(opacity=0.0))
        fig.add(frame)

        self.nodes: dict[str, Group] = {}
        self.labels: dict[str, list[_Label]] = {}
        self.label_on: dict[str, bool] = {}
        self.pose: dict[str, np.ndarray] = {}
        self.alive: set[str] = set(plan.initial)
        for name, piece in plan.pieces.items():
            node = self._piece_node(name, piece)
            visible = piece.born is None and not piece.hidden
            node.set_style(opacity=1.0 if visible else 0.0)
            fig.add(node)
            self.nodes[name] = node
            self.pose[name] = np.eye(3)
            self.sync_labels(name)

        self.cut_nodes: dict[str, Group] = {}
        for cname, cut in plan.cuts.items():
            g = Group(PathNode(path=dashed(cut.lines, DASH / k, GAP / k),
                               style=Style(stroke=t.FG, stroke_width=2.5)), name=f"cut:{cname}")
            g.set_style(opacity=0.0)
            fig.add(g)
            self.cut_nodes[cname] = g

        self.guide_nodes: dict[str, Group] = {}
        for gname, spec in plan.guides.items():
            fig.add(self._guide_node(gname, spec))

        self.merge_nodes: dict[str, Group] = {}
        for mname, spec in plan.merges.items():
            g = Group(PathNode(style=Style(stroke=t.ink(spec.get("role", "HL")), stroke_width=5)), name=mname)
            if spec.get("label"):
                g.add(self._text(spec["label"], spec.get("role", "HL")))
            g.set_style(opacity=0.0)
            fig.add(g)
            self.merge_nodes[mname] = g

        node = Group(fig, name=self.obj_id)
        spans = {**self.nodes, **self.guide_nodes, **self.merge_nodes}
        # items 是"现在存在的块"（同一个列表对象，动作里原地更新）：内置的 focus 调暗兄弟块、show obj#块 先藏其余块
        # 都读它，不能让已经被切掉的块跟着亮回来
        self.items = [self.nodes[n] for n in plan.initial]
        return Built(node, spans=spans, items=self.items)

    def _refresh_items(self) -> None:
        self.items[:] = [self.nodes[n] for n in self.plan.pieces if n in self.alive]

    # ---------- 节点 ----------

    def _role(self, name: str) -> str:
        style = self.plan.styles.get(name, {})
        if "role" in style:
            return style["role"]
        piece = self.plan.pieces[name]
        src = None
        if piece.born in self.plan.cuts:
            src = self.plan.cuts[piece.born].piece
        elif piece.born in self.plan.copies:
            src = self.plan.copies[piece.born]
        return self._role(src) if src else self.base_role

    def _text(self, text: str, role: str | None = None) -> Node:
        t = self.theme
        node = rich(text, t, size=LABEL, color=t.ink(role) if role else t.FG).node
        node.shift(-node.center())
        return Group(node)

    def _piece_node(self, name: str, piece: G.Piece) -> Group:
        t, k = self.theme, self.k
        color = t.color(self._role(name))
        group = Group(name=name)
        if piece.kind == "dots":
            for p in piece.points:
                dot = PathNode(path=circle(DOT / k).transformed(T.translate(*p)), style=Style(fill=color))
                group.add(dot)
        else:
            width = STROKE if piece.kind == "poly" else 2.0
            group.add(PathNode(path=piece_path(piece),
                               style=Style(fill=color, fill_opacity=t.fill_opacity(), stroke=color, stroke_width=width)))
        style = self.plan.styles.get(name, {})
        labels: list[_Label] = []
        if style.get("label"):
            labels.append(_Label(self._text(style["label"]), piece.center()))
        for e in style.get("edges", []):
            p, q = (tuple(x) for x in e["at"])
            labels.append(_Label(self._text(e["text"]), ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), (p, q)))
        for lab in labels:
            group.add(lab.node)
        self.labels[name] = labels
        self.label_on[name] = True
        return group

    def _guide_node(self, gname: str, spec: dict) -> Group:
        t, k = self.theme, self.k
        p, q = tuple(spec["from"]), tuple(spec["to"])
        role = spec.get("role")
        color = t.ink(role) if role else t.FG
        if spec.get("solid"):
            line = PathNode(path=VPath().move_to(*p).line_to(*q), style=Style(stroke=color, stroke_width=3))
        else:
            line = PathNode(path=dashed([(p, q)], DASH / k, GAP / k), style=Style(stroke=color, stroke_width=3))
        g = Group(line, name=gname)
        if spec.get("label"):
            lab = self._text(spec["label"], role)
            b = lab.bbox()
            # 标签放在参考线中点旁边，按线的法向挪开半个标签再多一点
            mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            length = G.dist(p, q)
            nx, ny = (q[1] - p[1]) / length, -(q[0] - p[0]) / length
            want = {"right": (1, 0), "left": (-1, 0), "above": (0, 1), "below": (0, -1)}.get(spec.get("side", "right"))
            if nx * want[0] + ny * want[1] < 0 or (abs(nx * want[0] + ny * want[1]) < 1e-9 and nx + ny < 0):
                nx, ny = -nx, -ny  # 默认放在右边；正好和要求的方向垂直时取右 / 上
            ext = (abs(nx) * b.width + abs(ny) * b.height) / 2 + LABEL_GAP
            lab.matrix = T.translate(mx + nx * ext / k, my + ny * ext / k) @ T.scale(1 / k)
            g.add(lab)
        g.set_style(opacity=0.0)
        self.guide_nodes[gname] = g
        return g

    def sync_labels(self, name: str, fade: bool = False) -> None:
        """把块的标签摆到位：位置跟着块走，方向始终正立、大小不变（反变换掉块的旋转、翻面和图形缩放）。"""
        m = self.nodes[name].matrix
        det = float(np.linalg.det(m[:2, :2]))
        if abs(det) < 0.2:
            for lab in self.labels[name]:
                lab.node.set_style(opacity=0.0)
            return
        inv = np.linalg.inv(m)
        center = apply(m, self.plan.pieces[name].center())
        k = self.k
        for lab in self.labels[name]:
            pos = apply(m, lab.anchor)
            if lab.edge is not None:
                p, q = apply(m, lab.edge[0]), apply(m, lab.edge[1])
                n = np.array([q[1] - p[1], p[0] - q[0]])
                n /= np.linalg.norm(n)
                if np.dot(n, pos - center) < 0:
                    n = -n
                w, h = lab.size
                ext = (abs(n[0]) * w + abs(n[1]) * h) / 2 + LABEL_GAP
                pos = pos + n * ext / k
            lab.node.matrix = inv @ T.translate(*pos) @ T.scale(1 / k)
            on = 1.0 if self.label_on[name] else 0.0
            lab.node.set_style(opacity=on * (min(1.0, (abs(det) - 0.2) / 0.6) if fade else 1.0))

    # ---------- 动作 ----------

    def action(self, act: str, args: dict) -> list[Animation]:
        handler = getattr(self, f"_act_{act}", None)
        if handler is None:
            raise KeyError(f"组件 {self.name} 没有动作 {act}")
        return handler(args)

    def _center(self, name: str) -> np.ndarray:
        return apply(self.pose[name], self.plan.pieces[name].center())

    def _rigid(self, name: str, end: np.ndarray, flip: bool = False) -> _Pose:
        """从当前位姿到 end：重心走直线，同时绕重心转过去（不翻面时）。"""
        start = self.pose[name]
        self.pose[name] = end
        c0 = self._center_of(start, name)
        c1 = self._center_of(end, name)
        rel = end @ np.linalg.inv(start)
        theta = math.degrees(math.atan2(rel[1, 0], rel[0, 0]))

        def fn(alpha: float) -> np.ndarray:
            c = c0 + (c1 - c0) * alpha
            return T.translate(*c) @ T.rotate(math.radians(theta * alpha)) @ T.translate(*(-c0)) @ start

        return _Pose(self, name, fn)

    def _center_of(self, pose: np.ndarray, name: str) -> np.ndarray:
        return apply(pose, self.plan.pieces[name].center())

    def _act_cut(self, args: dict) -> list[Animation]:
        cut = self.plan.cuts[args["cut"]]
        parent = cut.piece
        pose = self.pose[parent].copy()
        spread = float(args.get("spread", 0.0))
        dash = self.cut_nodes[cut.name]
        dash.matrix = pose
        children = list(cut.into)
        self.alive.discard(parent)
        self.alive |= set(children)
        self._refresh_items()
        pc = self._center_of(pose, parent)
        anims: list[tuple[float, float, Animation]] = [(0.0, 0.3, _Opacity(dash, 0.0, 1.0))]

        def swap() -> None:
            self.nodes[parent].set_style(opacity=0.0)
            for c in children:
                self.nodes[c].matrix = pose.copy()
                self.nodes[c].set_style(opacity=1.0)
                self.sync_labels(c)

        anims.append((0.3, 0.31, _Call(swap)))
        for c in children:
            self.pose[c] = pose.copy()
            if spread > 0:
                v = self._center_of(pose, c) - pc
                v = v / (np.linalg.norm(v) or 1.0) * spread
                anims.append((0.35, 0.75, self._shift_anim(c, v)))
        anims.append((0.7, 1.0, _Opacity(dash, 1.0, 0.0)))
        return [_Staged(anims)]

    def _shift_anim(self, name: str, v) -> _Pose:
        start = self.pose[name]
        end = T.translate(*v) @ start
        self.pose[name] = end
        return _Pose(self, name, lambda a: T.translate(*(np.asarray(v) * a)) @ start)

    def _act_shift(self, args: dict) -> list[Animation]:
        name = args["piece"]
        if "by" in args:
            v = np.array(args["by"], dtype=float)
        else:
            v = np.array(args["to"], dtype=float) - self._center(name)
        return [self._shift_anim(name, v)]

    def _act_rotate(self, args: dict) -> list[Animation]:
        name = args["piece"]
        about = args.get("about", "center")
        start = self.pose[name]
        if about == "center":
            p = self._center(name)
        elif isinstance(about, dict):
            p = apply(start, about["vertex"])
        else:
            p = np.array(about, dtype=float)
        angle = float(args["angle"])
        self.pose[name] = T.rotate(math.radians(angle), tuple(p)) @ start
        return [_Pose(self, name, lambda a: T.rotate(math.radians(angle * a), tuple(p)) @ start)]

    def _act_flip(self, args: dict) -> list[Animation]:
        name = args["piece"]
        start = self.pose[name]
        axis = args["axis"]
        if isinstance(axis, dict):
            p, q = apply(start, axis["edge"][0]), apply(start, axis["edge"][1])
        else:
            p, q = np.array(axis[0], dtype=float), np.array(axis[1], dtype=float)
        u = (q - p) / np.linalg.norm(q - p)
        Q = np.array([[u[0], -u[1], 0.0], [u[1], u[0], 0.0], [0.0, 0.0, 1.0]])

        def fn(alpha: float) -> np.ndarray:
            # 绕轴"翻过去"：垂直于轴的方向从 1 压到 -1（中途看起来是侧着的一条线）
            s = math.cos(math.pi * alpha)
            return T.translate(*p) @ Q @ np.diag([1.0, s, 1.0]) @ Q.T @ T.translate(*(-p)) @ start

        self.pose[name] = fn(1.0)
        return [_Pose(self, name, fn, flip=True)]

    def _act_snap(self, args: dict) -> list[Animation]:
        name = args["piece"]
        start = self.pose[name]
        to = args["to"]
        if "edge" in args:
            p0, p1 = (apply(start, x) for x in args["edge"])
            if isinstance(to, str) and to in self.plan.guides:
                g = self.plan.guides[to]
                q0, q1 = np.array(g["from"], float), np.array(g["to"], float)
            else:
                q0, q1 = (apply(self.pose[to], x) for x in args["to_edge"])
            move = mat(G.edge_map(tuple(p0), tuple(p1), tuple(q0), tuple(q1)))
        else:
            p = apply(start, args["vertex"])
            q = apply(self.pose[to], args["to_vertex"]) if isinstance(to, str) else np.array(to, float)
            move = T.translate(*(q - p))
        return [self._rigid(name, move @ start)]

    def _act_copy(self, args: dict) -> list[Animation]:
        name = args["piece"]
        src = self.plan.copies[name]
        pose = self.pose[src].copy()
        self.pose[name] = pose
        self.alive.add(name)
        self._refresh_items()
        node = self.nodes[name]

        def place() -> None:
            node.matrix = pose.copy()
            self.sync_labels(name)

        windows: list[tuple[float, float, Animation]] = [(0.0, 0.01, _Call(place)), (0.0, 0.4, _Opacity(node, 0.0, 1.0))]
        if "by" in args:
            windows.append((0.3, 1.0, self._shift_anim(name, np.array(args["by"], dtype=float))))
        return [_Staged(windows)]

    def _act_merge(self, args: dict) -> list[Animation]:
        mname = args["name"]
        spec = self.plan.merges[mname]
        polys, weights, centers = [], [], []
        for p in spec["pieces"]:
            m = self.pose[p]
            piece = self.plan.pieces[p]
            poly = [tuple(apply(m, v)) for v in piece.outline()]
            polys.append(poly if G.signed_area(poly) > 0 else poly[::-1])
            weights.append(piece.area())
            centers.append(self._center(p))
        segs = G.union_outline(polys)
        path = VPath()
        for p, q in segs:
            path.move_to(*p).line_to(*q)
        g = self.merge_nodes[mname]
        g.children[0].path = path
        if len(g.children) > 1:
            c = sum(w * c for w, c in zip(weights, centers)) / max(1e-9, sum(weights))
            g.children[1].matrix = T.translate(*c) @ T.scale(1 / self.k)
        return [_Opacity(g, 0.0, 1.0)]

    def _act_unmerge(self, args: dict) -> list[Animation]:
        return [_Opacity(self.merge_nodes[args["name"]], 1.0, 0.0)]

    def _act_arrange(self, args: dict) -> list[Animation]:
        cut = self.plan.cuts[args["cut"]]
        sectors = [self.plan.pieces[n] for n in cut.into]
        poses = G.alternate_poses(sectors, tuple(args["at"]) if "at" in args else None)
        n = len(sectors)
        width = 0.5 if n > 1 else 1.0
        windows = []
        for i, (name, pose) in enumerate(zip(cut.into, poses)):
            start = (1 - width) * i / max(1, n - 1)
            windows.append((start, start + width, self._rigid(name, mat(pose))))
        return [_Staged(windows)]

    def _act_labels(self, args: dict) -> list[Animation]:
        name = args["piece"]
        on = bool(args["show"])
        self.label_on[name] = on
        return [_Opacity(lab.node, 0.0 if on else 1.0, 1.0 if on else 0.0) for lab in self.labels[name]]

    # ---------- 结束状态检查 ----------

    def end_problems(self) -> list[str]:
        """这一刻看得见的标签（块的面积 / 边长标签、参考线和轮廓的标签）两两不能压在一起。"""
        labels = {id(lab.node) for labs in self.labels.values() for lab in labs}
        labels |= {id(c) for g in [*self.guide_nodes.values(), *self.merge_nodes.values()] for c in g.children[1:]}
        boxes: list[tuple[str, object]] = []

        def visit(node: Node, parent: np.ndarray, opacity: float, owner: str) -> None:
            opacity *= node.style.opacity if node.style else 1.0
            if opacity < 0.5:
                return
            if node.name in self.built.spans:
                owner = node.name
            if id(node) in labels:
                boxes.append((owner, node.bbox(parent)))
                return
            m = parent @ node.matrix
            for child in node.children:
                visit(child, m, opacity, owner)

        visit(self.node, np.eye(3), 1.0, self.obj_id)
        problems = []
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                (na, a), (nb, b) = boxes[i], boxes[j]
                if a is None or b is None:
                    continue
                ox = min(a.x1, b.x1) - max(a.x0, b.x0)
                oy = min(a.y1, b.y1) - max(a.y0, b.y0)
                if ox > 0.02 and oy > 0.02:
                    problems.append(f"{self.obj_id} 的标签压在一起：{na} 和 {nb}（{ox:.2f}×{oy:.2f}）")
        return problems
