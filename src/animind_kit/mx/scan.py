"""墨线对象树 → lint.Thing：某一刻画面上每个看得见的字形和图形，坐标换到画面上（含镜头）。

文字认法：路径叶子的祖先里有 moxian 的 Text / Tex 节点就是字形，那个节点就是它所在的"文字段"，
字号 = 节点的 size（1080p 像素）× 世界缩放 × 镜头放大。祖先节点上的 font_floor（字号下限类别）带到字形上。残影（ghost）拷出来的是普通路径，
整块当成一段没有字号的文字。其余叶子是图形。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from moxian.core import Camera, Node, Style
from moxian.core import transform as T
from moxian.text import Tex, Text

from ..lint import Thing

STEPS = 8  # 每段三次贝塞尔折成几段


@dataclass(frozen=True)
class Label:
    owner: str  # 报告里的名字
    obj: str | None  # 所属 scene-spec 对象
    act: str | None = None  # 装饰类型；ghost 表示残影


def flatten(path, m: np.ndarray) -> tuple[list[np.ndarray], list[bool]]:
    ts = np.linspace(0.0, 1.0, STEPS + 1)[1:, None]
    curves, closed = [], []
    for sp in path.subpaths:
        if not sp.segments:
            continue
        pts = [sp.start.reshape(1, 2)]
        p0 = sp.start
        for seg in sp.segments:
            c1, c2, p3 = seg
            pts.append((1 - ts) ** 3 * p0 + 3 * (1 - ts) ** 2 * ts * c1 + 3 * (1 - ts) * ts ** 2 * c2 + ts ** 3 * p3)
            p0 = p3
        curves.append(T.apply(m, np.vstack(pts)))
        closed.append(bool(sp.closed))
    return curves, closed


def _hex(c) -> str:
    """颜色统一成小写 #rrggbb（风格提示按它查强调色）；不是字符串的（渐变等）给空串。"""
    if not isinstance(c, str):
        return ""
    c = c.strip().lower()
    return c[:7] if c.startswith("#") and len(c) in (7, 9) else c


def scan(root: Node, labels: dict[int, Label], camera: Camera | None = None, min_opacity: float = 0.15) -> list[Thing]:
    cam = None if camera is None or camera.is_identity() else camera
    things: list[Thing] = []
    fixed_flags: list[bool] = []
    runs: dict[int, int] = {}

    def visit(node: Node, parent: np.ndarray, parent_style: Style | None, label: Label | None,
              run: tuple[Node, np.ndarray] | None, fixed: bool, anchor: np.ndarray | None,
              floor: str | None = None) -> None:
        m = parent @ node.matrix
        floor = getattr(node, "font_floor", None) or floor
        if node.style is None:
            style = parent_style or Style()
        elif parent_style is None:
            style = node.style
        else:
            style = node.style.with_(opacity=node.style.opacity * parent_style.opacity)
        if style.opacity < min_opacity:
            return
        label = labels.get(id(node), label)
        fixed = fixed or node.fixed
        if anchor is None and node.keep_size:
            anchor = m[:2, 2].copy()
        if cam is None or fixed:
            screen = m
        elif anchor is not None:
            screen = cam.matrix @ T.scale(1 / cam.zoom, about=(float(anchor[0]), float(anchor[1]))) @ m
        else:
            screen = cam.matrix @ m
        if isinstance(node, (Text, Tex)) and run is None:
            run = (node, screen)
        path = getattr(node, "path", None)
        if path is not None and not path.is_empty() and style.visible() and label is not None:
            fill = style.fill_opacity * style.opacity if style.fill is not None else 0.0
            stroked = (style.stroke is not None and style.stroke_width > 0
                       and style.stroke_opacity * style.opacity >= min_opacity)
            b = path.bbox(screen)
            if b is not None and (fill >= min_opacity or stroked):
                ghost = label.act == "ghost"
                if run is not None or ghost:
                    key = id(run[0]) if run is not None else id(label)
                    em = None
                    text = label.owner
                    if run is not None and not ghost:
                        rnode, rm = run
                        em = float(rnode.size * np.sqrt(abs(np.linalg.det(rm[:2, :2]))))
                        text = getattr(rnode, "source", None) or getattr(rnode, "text", "") or ""
                        text = text.replace("\\displaystyle ", "")
                    things.append(Thing("text", label.owner, label.obj, tuple(b), 0, runs.setdefault(key, len(runs)),
                                        text, em, label.act, follows=not fixed, floor=floor,
                                        color=_hex(style.fill if style.fill is not None else style.stroke),
                                        alpha=style.opacity * (style.fill_opacity if style.fill is not None else 1.0)))
                else:
                    curves, closed = flatten(path, screen)
                    if fill >= min_opacity:
                        color, alpha = _hex(style.fill), fill
                    else:
                        color, alpha = _hex(style.stroke), style.stroke_opacity * style.opacity
                    things.append(Thing("shape", label.owner, label.obj, tuple(b), 0, act=label.act,
                                        curves=curves, closed=closed, stroked=stroked, fill=fill, follows=not fixed,
                                        color=color, alpha=alpha))
                fixed_flags.append(fixed)
        for child in sorted(node.children, key=lambda n: n.z):
            visit(child, m, style, label, run, fixed, anchor, floor)

    visit(root, T.identity(), None, None, None, False, None)
    # 绘制顺序：和 SkiaRenderer 一致，镜头生效时不跟镜头走的一层最后画
    seq = list(range(len(things)))
    if cam is not None:
        seq = [i for i in seq if not fixed_flags[i]] + [i for i in seq if fixed_flags[i]]
    for order, i in enumerate(seq):
        things[i].order = order
    return things
