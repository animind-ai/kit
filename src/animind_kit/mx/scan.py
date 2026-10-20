"""墨线对象树 → lint.Thing：某一刻画面上每个看得见的字形和图形，坐标换到画面上（含镜头）。

文字认法：路径叶子的祖先里有 moxian 的 Text / Tex 节点就是字形，那个节点就是它所在的"文字段"，
字号 = 节点的 size（1080p 像素）× 世界缩放 × 镜头放大。祖先节点上的 font_floor（字号下限类别）带到字形上。残影（ghost）拷出来的是普通路径，
整块当成一段没有字号的文字。其余叶子是图形。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from moxian.clip import ClipNode, place
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


def scan(root: Node, labels: dict[int, Label], camera: Camera | None = None, min_opacity: float = 0.15,
         time: float | None = None) -> list[Thing]:
    """time：成片时刻（beat 内）。片段还没开始（早于 start）时不画，不参与检查；None 表示不看开始时刻。"""
    cam = None if camera is None or camera.is_identity() else camera
    things: list[Thing] = []
    fixed_flags: list[bool] = []
    runs: dict[int, int] = {}

    def visit(node: Node, parent: np.ndarray, parent_style: Style | None, label: Label | None,
              run: tuple[Node, np.ndarray, tuple] | None, fixed: bool, anchor: np.ndarray | None,
              floor: str | None = None, trail: tuple = ()) -> None:
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
        if id(node) in labels:  # 文字段的位置从挂名字的节点算起
            label, trail = labels[id(node)], ()
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
            run = (node, screen, trail)
        if isinstance(node, ClipNode) and label is not None and node.opacity * style.opacity >= min_opacity:
            # 片段：实际画出来的矩形（fit 之后的目标区域）当成填充图形（act="plate"）。
            # 文字在它上面没问题；文字在它下面（z 更低）会被它盖住，报 covered
            rect = _shown_rect(node, time)
            if rect is not None:
                things.append(_plate(rect, label, screen, node.opacity * style.opacity, not fixed))
                fixed_flags.append(fixed)
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
                    slot = None
                    if run is not None and not ghost:
                        rnode, rm, slot = run
                        em = float(rnode.size * np.sqrt(abs(np.linalg.det(rm[:2, :2]))))
                        text = getattr(rnode, "source", None) or getattr(rnode, "text", "") or ""
                        text = text.replace("\\displaystyle ", "")
                    things.append(Thing("text", label.owner, label.obj, tuple(b), 0, runs.setdefault(key, len(runs)),
                                        text, em, label.act, follows=not fixed, floor=floor, slot=slot))
                else:
                    curves, closed = flatten(path, screen)
                    things.append(Thing("shape", label.owner, label.obj, tuple(b), 0, act=label.act,
                                        curves=curves, closed=closed, stroked=stroked, fill=fill, follows=not fixed))
                fixed_flags.append(fixed)
        for k, child in sorted(enumerate(node.children), key=lambda kc: kc[1].z):
            visit(child, m, style, label, run, fixed, anchor, floor, trail + (k,))

    visit(root, T.identity(), None, None, None, False, None)
    # 绘制顺序：和 SkiaRenderer 一致，镜头生效时不跟镜头走的一层最后画
    seq = list(range(len(things)))
    if cam is not None:
        seq = [i for i in seq if not fixed_flags[i]] + [i for i in seq if fixed_flags[i]]
    for order, i in enumerate(seq):
        things[i].order = order
    return things


def _shown_rect(node: ClipNode, time: float | None) -> tuple[float, float, float, float] | None:
    """片段在时刻 time 实际画出来的矩形（本地坐标）。还没开始是 None。

    尺寸取自 time 这一刻真正画的那一帧（frame_index_at 已经算了 trim 和循环），和 renderer 的 paint_raster 一样：
    帧序列的各帧尺寸可以不同。fit 决定画面占槽位的哪一块：contain 留白，cover 和 stretch 占满。
    """
    index = None
    if time is not None:
        index = node.frame_index_at(time)
        if index is None:
            return None
    width, height = _source_size(node, index)
    return place(node.plate, width, height, node.fit).dest


def _source_size(node: ClipNode, index: int | None) -> tuple[float, float]:
    """第 index 帧的宽高（像素）。视频的容器宽高不随帧变；帧序列取那一帧（index 为 None 时取第一帧）。"""
    source = node.source()
    if hasattr(source, "width"):
        return source.width, source.height
    image = source.frame(0 if index is None else index)
    return image.width(), image.height()


def _plate(rect: tuple[float, float, float, float], label: Label, screen: np.ndarray, fill: float,
           follows: bool) -> Thing:
    """片段的矩形（本地坐标 x0, y0, x1, y1）换到画面上，当成一块填充。只为了出界和遮挡检查，不画它的像素。"""
    x0, y0, x1, y1 = rect
    corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)
    pts = T.apply(screen, corners)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    return Thing("shape", label.owner, label.obj, (float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])), 0,
                 act="plate", curves=[pts], closed=[True], stroked=False, fill=fill, follows=follows)
