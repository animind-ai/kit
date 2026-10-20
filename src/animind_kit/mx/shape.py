"""type: shape：基础图形（圆、方、长方形、多边形、线、箭头、点），可带一个标注。

长度、坐标是画面单位（画面高 8），只表示形状；位置和整体大小由布局决定。参数的含义见 scene-spec 设计稿 §6.4.1。
闭合图形描边，写了 role 时再垫一层淡色填充；点是实心的。标注放在图形里面（放不下就放到下面）、
线和箭头的上方、点的右上，不压在线上。
"""

from __future__ import annotations

import numpy as np
from moxian.core import DOWN, UP, Group, Node, PathNode, Style
from moxian.path import arrow, circle, line, polygon, rect
from scene_spec.semantic import shape_length

from ..theme import Theme
from .text import rich

STROKE = 4  # 描边粗细（1080p 像素）
HEAD = 0.3  # 箭头尖的长度
HEAD_W = 0.13  # 箭头尖的半宽
LABEL_GAP = 0.12
INSIDE = 0.7  # 标注宽、高不超过图形的这个比例才放在里面
DEFAULTS = {"circle": 1.0, "dot": 0.08, "square": 2.0, "rectangle": (3.0, 2.0), "line": 2.0, "arrow": 2.0}


def _ends(d: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    if "points" in d:
        (x0, y0), (x1, y1) = d["points"]
        return (float(x0), float(y0)), (float(x1), float(y1))
    length = shape_length(d.get("side", DEFAULTS[d["shape"]])) or DEFAULTS[d["shape"]]
    return (0.0, 0.0), (length, 0.0)


def build_shape(d: dict, theme: Theme, label_size: float, name: str = "") -> Group:
    kind = d["shape"]
    role = d.get("role")
    color = theme.ink(role)
    outline = Style(stroke=color, stroke_width=STROKE)
    closed = Style(fill=color, fill_opacity=theme.fill_opacity(), stroke=color, stroke_width=STROKE) if role else outline
    if kind in ("circle", "dot"):
        r = float(d.get("radius", DEFAULTS[kind]))
        body: list[Node] = [PathNode(path=circle(r), style=Style(fill=color) if kind == "dot" else closed)]
    elif kind == "square":
        s = shape_length(d.get("side", DEFAULTS[kind])) or DEFAULTS[kind]
        body = [PathNode(path=rect(s, s), style=closed)]
    elif kind == "rectangle":
        w, h = d.get("side", DEFAULTS[kind])
        body = [PathNode(path=rect(float(w), float(h)), style=closed)]
    elif kind == "polygon":
        body = [PathNode(path=polygon([(float(x), float(y)) for x, y in d["points"]]), style=closed)]
    elif kind == "line":
        (x0, y0), (x1, y1) = _ends(d)
        body = [PathNode(path=line(x0, y0, x1, y1), style=outline)]
    elif kind == "arrow":
        (x0, y0), (x1, y1) = _ends(d)
        shaft, head = arrow(x0, y0, x1, y1, HEAD, HEAD_W)
        body = [PathNode(path=shaft, style=outline), PathNode(path=head, style=Style(fill=color, stroke=color, stroke_width=1))]
    else:
        raise NotImplementedError(f"kit（墨线）还不支持 shape: {kind}")
    node = Group(*body, name=name)
    if d.get("label"):
        node.add(_label(d, kind, Group(*body).bbox(), rich(d["label"], theme, size=label_size, color=color).node))
    return node


def _label(d: dict, kind: str, box, label: Node) -> Node:
    """标注摆到图形旁边或里面（图形还在自己的坐标里，整体之后再由布局移动、缩放）。"""
    if kind in ("circle", "square", "rectangle", "polygon"):
        if label.width <= box.width * INSIDE and label.height <= box.height * INSIDE:
            if kind == "polygon":  # 多边形放在顶点的平均位置（三角形的重心），比包围盒中心更靠里
                return label.move_to(np.mean(np.asarray(d["points"], dtype=float), axis=0))
            return label.move_to(box.center)
        return label.next_to(box.point(DOWN), DOWN, LABEL_GAP)
    if kind == "dot":
        corner = box.point(np.array([1.0, 1.0]))
        return label.shift(corner + LABEL_GAP - label.bbox().point(np.array([-1.0, -1.0])))
    return label.next_to(box.point(UP), UP, LABEL_GAP)  # 线、箭头：在包围盒上方，不会压到线
