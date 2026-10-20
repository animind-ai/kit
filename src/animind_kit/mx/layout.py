"""墨线版布局：按 scene-spec 的布局字段摆放顶层对象，渲染后做出框 / 重叠检查。"""

from __future__ import annotations

import numpy as np
from moxian.core import DOWN, LEFT, RIGHT, UP, BBox, Node

from ..regions import GAPS, PIN_MAX_HEIGHT, PIN_TOP_MARGIN, PIN_WIDTH, Region, check_boxes, regions
from . import solver

DIRECTIONS = {"below": DOWN, "above": UP, "left_of": LEFT, "right_of": RIGHT}


def fit(node: Node, region: Region, max_width: float | None = None, max_height: float | None = None) -> Node:
    mw = region.width * (max_width or 0.96)
    mh = region.height * (max_height or 0.96)
    if node.width > mw:
        node.scale_to_fit_width(mw)
    if node.height > mh:
        node.scale_to_fit_height(mh)
    return node


def pin_top(node: Node) -> Node:
    node.scale_to_fit_width(PIN_WIDTH)
    if node.height > PIN_MAX_HEIGHT:
        node.scale_to_fit_height(PIN_MAX_HEIGHT)
    node.to_edge(UP, margin=PIN_TOP_MARGIN)
    return node.shift([-node.center()[0], 0.0])


def place(node: Node, definition: dict, refs: dict[str, BBox], region: Region | None = None,
          stack_below: float = 0.0) -> Node:
    """refs：字段名 → 被引用对象在世界坐标里的包围盒。"""
    if definition.get("pin") == "top":
        return pin_top(node)
    if "at" in definition:
        return node.move_to(definition["at"])
    region = region or regions()["stage"]
    size = definition.get("size") or {}
    relative = any(k in definition for k in DIRECTIONS) or "over" in definition or "within" in definition
    fit(node, region, size.get("max_width"), size.get("max_height"))
    if not relative:
        node.move_to(region.center)
        if stack_below > 0:
            total = node.height + stack_below
            if total > region.height:
                node.scale_to_fit_height(max(0.3, region.height - stack_below))
                total = node.height + stack_below
            node.shift([0.0, region.center[1] + total / 2 - node.height / 2 - node.center()[1]])
    if "within" in definition:
        inner = refs["within"]
        if node.width > inner.width * 0.9:
            node.scale_to_fit_width(inner.width * 0.9)
        node.move_to(inner.center)
    if "over" in definition:
        ref = refs["over"]
        node.move_to(ref.center)
        if ref.height < node.height * 0.5:  # 盖在一条横线（填空的下划线）上：坐在线上，不要被线穿过
            node.shift([0.0, ref.y1 + 0.03 - node.bbox().y0])
    for key, direction in DIRECTIONS.items():
        if key in definition:
            anchor = refs[key]
            gap = GAPS[definition.get("gap", "md")]
            if key in ("left_of", "right_of"):
                # 横向放不下：先在剩余宽度里缩（最多缩到 60%），还不行就改放到锚点下方
                room = (region.x1 - anchor.x1 - gap) if key == "right_of" else (anchor.x0 - gap - region.x0)
                if node.width > room:
                    if room >= node.width * MIN_SHRINK:
                        node.scale(room / node.width)
                    else:
                        node.next_to(anchor.point(DOWN), DOWN, gap)
                        node.shift([anchor.x0 - node.bbox().x0, 0.0])
                        continue
            node.next_to(anchor.point(direction), direction, gap)
            if key in ("below", "above"):
                node.shift([anchor.center[0] - node.center()[0], 0.0])
    align = definition.get("align") or {}
    if "x" in align:
        node.shift([refs["align.x"].center[0] - node.center()[0], 0.0])
    if "y" in align:
        node.shift([0.0, refs["align.y"].center[1] - node.center()[1]])
    return clamp(node, region)


MIN_SHRINK = 0.6


def clamp(node: Node, region: Region) -> Node:
    """超出区域的推回区域内；比区域还宽 / 高就先缩到能放下。"""
    b = node.bbox()
    if b is None:
        return node
    if b.width > region.width:
        node.scale(region.width / b.width)
    if b.height > region.height:
        node.scale(region.height / b.height)
    b = node.bbox()
    dx = max(0.0, region.x0 - b.x0) - max(0.0, b.x1 - region.x1)
    dy = max(0.0, region.y0 - b.y0) - max(0.0, b.y1 - region.y1)
    if dx or dy:
        node.shift([dx, dy])
    return node


def check(visible: dict[str, Node], ignore_pairs: set[frozenset[str]], pinned: set[str] = frozenset(),
          template: str = "default") -> list[str]:
    boxes = {}
    for name, node in visible.items():
        b = node.bbox()
        if b is None or (b.width == 0 and b.height == 0):
            continue
        boxes[name] = (b.x0, b.x1, b.y0, b.y1)
    return check_boxes(boxes, ignore_pairs, pinned, template)


def solver_items(visible: dict[str, Node], definitions: dict[str, dict], fixed: set[str] = frozenset()) -> list[solver.Item]:
    """可见对象 → 求解器输入。顺序沿用 visible 的插入顺序（即出现顺序）。"""
    from scene_spec import selectors

    items = []
    for name, node in visible.items():
        b = node.bbox()
        if b is None or (b.width == 0 and b.height == 0):
            continue
        d = definitions.get(name, {})
        anchor = side = None
        for key in ("below", "above", "left_of", "right_of", "over", "within"):
            if key in d:
                sel = selectors.parse(d[key])
                anchor, side = (sel.obj if sel else None), (key if key in DIRECTIONS else None)
                break
        items.append(solver.Item(
            id=name, box=solver.Box(b.x0, b.y0, b.x1, b.y1), region=name, anchor=anchor, side=side,
            gap=GAPS[d.get("gap", "md")] if side else solver.DEFAULT_GAP,
            fixed=name in fixed or "pin" in d or "at" in d,
        ))
    return items


def resolve(visible: dict[str, Node], definitions: dict[str, dict], region_of: dict[str, Region],
            ignore_pairs: set[frozenset[str]] = frozenset(), fixed: set[str] = frozenset(),
            min_scale: float = MIN_SHRINK) -> list[str]:
    """对同时可见的对象跑布局求解器，把挪动 / 缩放直接作用到节点上，返回解不开的冲突。

    visible：对象名 → 树里的节点（世界坐标 = 节点自身坐标，即 root 的直接子节点）。
    definitions：对象名 → scene-spec 里的定义（读 below / right_of / over / gap / pin / at）。
    region_of：对象名 → 它所在的区域；没给的对象不做区域约束。
    fixed：不许动的对象（已经在画面上的、钉住的）。
    """
    items = solver_items(visible, definitions, fixed)
    region_map = {it.id: region_of[it.id] for it in items if it.id in region_of}
    for it in items:
        if it.id not in region_map:
            it.region = None
    result = solver.solve(items, region_map, ignore_pairs, min_scale)
    for adj in result.adjustments:
        m = np.array([[adj.scale, 0.0, adj.dx], [0.0, adj.scale, adj.dy], [0.0, 0.0, 1.0]])
        visible[adj.id].apply(m)
    return result.problems
