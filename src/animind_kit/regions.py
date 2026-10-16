"""画面分区（纯几何，不依赖渲染后端）。画面高 8 个单位、宽 8×16/9，原点在中心。"""

from __future__ import annotations

from dataclasses import dataclass

FRAME_HEIGHT = 8.0
FRAME_WIDTH = FRAME_HEIGHT * 16 / 9
MARGIN = 0.3
GAPS = {"xs": 0.1, "sm": 0.2, "md": 0.4, "lg": 0.7, "xl": 1.0}
PIN_WIDTH = 12.4
PIN_MAX_HEIGHT = 2.5
PIN_TOP_MARGIN = 0.16


@dataclass(frozen=True)
class Region:
    name: str
    x0: float
    x1: float
    y0: float
    y1: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def regions(top_used: bool = True, side_used: bool = True, footer_used: bool = True) -> dict[str, Region]:
    """四个区域。没人用 top / side / footer 时，stage 把那块空间也占上。"""
    w, h = FRAME_WIDTH, FRAME_HEIGHT
    left, right, top, bottom = -w / 2 + MARGIN, w / 2 - MARGIN, h / 2 - MARGIN, -h / 2 + MARGIN
    stage_top = top - 2.8 if top_used else top
    stage_bottom = bottom + 1.1 if footer_used else bottom
    stage_right = right - 3.6 if side_used else right
    return {
        "top": Region("top", left, right, top - 2.6, top),
        "stage": Region("stage", left, stage_right, stage_bottom, stage_top),
        "side": Region("side", right - 3.4, right, stage_bottom, stage_top),
        "footer": Region("footer", left, right, bottom, bottom + 1.0),
    }


def check_boxes(boxes: dict[str, tuple[float, float, float, float]], ignore_pairs: set[frozenset[str]],
                pinned: set[str] = frozenset()) -> list[str]:
    """boxes: 名字 → (x0, x1, y0, y1)。返回出框 / 重叠的描述。"""
    from itertools import combinations

    problems = []
    w, h = FRAME_WIDTH / 2 - MARGIN + 0.05, FRAME_HEIGHT / 2 - MARGIN + 0.05
    for name, (x0, x1, y0, y1) in boxes.items():
        top = FRAME_HEIGHT / 2 - 0.1 if name in pinned else h
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
