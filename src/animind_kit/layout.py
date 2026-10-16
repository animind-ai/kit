"""布局：区域、相对约束、钉住；渲染后做出框 / 重叠检查。

0.1 还不是求解器：按声明的约束逐个摆放，摆完检查，问题以警告报出。
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
from manim import DOWN, LEFT, RIGHT, UP, Mobject, config

from .regions import GAPS, MARGIN, PIN_MAX_HEIGHT, PIN_WIDTH, Region, regions  # noqa: E402,F401


def fit(mob: Mobject, region: Region, max_width: float | None = None, max_height: float | None = None) -> Mobject:
    mw = region.width * (max_width or 0.96)
    mh = region.height * (max_height or 0.96)
    if mob.width > mw:
        mob.scale_to_fit_width(mw)
    if mob.height > mh:
        mob.scale_to_fit_height(mh)
    return mob


def pin_top(mob: Mobject) -> Mobject:
    mob.scale_to_fit_width(PIN_WIDTH)
    if mob.height > PIN_MAX_HEIGHT:
        mob.scale_to_fit_height(PIN_MAX_HEIGHT)
    return mob.set_x(0).to_edge(UP, buff=0.16)


DIRECTIONS = {"below": DOWN, "above": UP, "left_of": LEFT, "right_of": RIGHT}


def place(mob: Mobject, definition: dict, refs: dict[str, Mobject], region_name: str = "stage",
          stack_below: float = 0.0, usage: tuple[bool, bool, bool] = (True, True, True)) -> Mobject:
    """按对象定义里的布局字段摆放。

    refs 是已解析的引用：字段名 → Mobject。region_name 是对象所在（或其锚点所在）的区域，
    相对摆放的对象也按这个区域的宽度收缩。stack_below 是声明在它下面的对象总高度（含间距），
    放进区域时整叠一起居中，免得下面的对象顶出区域。
    """
    if definition.get("pin") == "top":
        return pin_top(mob)
    if "at" in definition:
        x, y = definition["at"]
        return mob.move_to([x, y, 0])
    region = regions(*usage)[region_name]
    size = definition.get("size") or {}
    relative = any(k in definition for k in DIRECTIONS) or "over" in definition or "within" in definition
    if not relative:
        fit(mob, region, size.get("max_width"), size.get("max_height"))
        mob.move_to(np.array([region.center[0], region.center[1], 0.0]))
        if stack_below > 0:
            total = mob.height + stack_below
            if total > region.height:
                mob.scale_to_fit_height(max(0.3, region.height - stack_below))
                total = mob.height + stack_below
            mob.set_y(region.center[1] + total / 2 - mob.height / 2)
    else:
        fit(mob, region, size.get("max_width"), size.get("max_height"))
    if "within" in definition:
        inner = refs["within"]
        if mob.width > inner.width * 0.9:
            mob.scale_to_fit_width(inner.width * 0.9)
        mob.move_to(inner)
    if "over" in definition:
        ref = refs["over"]
        mob.move_to(ref)
        if ref.height < mob.height * 0.5:  # 盖在一条横线（填空的下划线）上：坐在线上
            mob.next_to(ref, UP, buff=0.03)
    for key, direction in DIRECTIONS.items():
        if key in definition:
            anchor = refs[key]
            mob.next_to(anchor, direction, buff=GAPS[definition.get("gap", "md")])
            if key in ("below", "above"):
                mob.set_x(anchor.get_x())
    align = definition.get("align") or {}
    if "x" in align:
        mob.set_x(refs["align.x"].get_x())
    if "y" in align:
        mob.set_y(refs["align.y"].get_y())
    return mob


def bbox(mob: Mobject) -> tuple[float, float, float, float]:
    c = mob.get_critical_point
    return (c(LEFT)[0], c(RIGHT)[0], c(DOWN)[1], c(UP)[1])


def check(visible: dict[str, Mobject], ignore_pairs: set[frozenset[str]], pinned: set[str] = frozenset()) -> list[str]:
    """返回出框 / 重叠的描述；空列表表示没问题。钉在顶上的对象允许贴近上边缘。"""
    problems = []
    w, h = config.frame_width / 2 - MARGIN + 0.05, config.frame_height / 2 - MARGIN + 0.05
    boxes = {}
    for name, mob in visible.items():
        if mob.width == 0 and mob.height == 0:
            continue
        x0, x1, y0, y1 = boxes[name] = bbox(mob)
        top = config.frame_height / 2 - 0.1 if name in pinned else h
        if x0 < -w or x1 > w or y0 < -h or y1 > top:
            problems.append(f"{name} 超出安全边距（x {x0:.2f}..{x1:.2f}，y {y0:.2f}..{y1:.2f}）")
    for a, b in combinations(boxes, 2):
        if frozenset((a, b)) in ignore_pairs:
            continue
        ax0, ax1, ay0, ay1 = boxes[a]
        bx0, bx1, by0, by1 = boxes[b]
        ox = min(ax1, bx1) - max(ax0, bx0)
        oy = min(ay1, by1) - max(ay0, by0)
        if ox > 0.05 and oy > 0.05:
            problems.append(f"{a} 与 {b} 重叠（{ox:.2f}×{oy:.2f}）")
    return problems
