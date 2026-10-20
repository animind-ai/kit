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
SIDE_WIDTH = 4.2  # 右侧栏宽度（原来 3.4；侧栏字号下限抬高后，一行十来个字要放得下）


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


# 分区模板（style.layout）：default 是题卡在上的例题版式；lecture 是讲座（编程课）的幻灯片版式，
# 顶上只留一条放页眉的窄带，side 是右半边（左边代码、右边调用栈或终端），见 scene-spec 的 docs/cs-lecture.md。
TEMPLATES = {
    "default": {"top": 2.6, "side": SIDE_WIDTH},
    "lecture": {"top": 1.4, "side": (FRAME_WIDTH - 2 * MARGIN - 0.4) / 2},
    # v2（培玉视觉规范 v2，2026-10-20）：没有右侧栏，side 的内容并进顶上一条窄带（与 top 同一块），
    # stage 占满整个宽度；底部留一条一行字幕带（画面不再整体缩小，见 subtitles.ONE_LINE_BAND）。
    "v2": {"top": 1.0, "side": 0.0},
}
# 每个模板在画面底部给烧录字幕留的高度（单位）。v2：一行字幕带 54px@720 = 0.6 单位，再空 0.1。
SUBTITLE_STRIP = {"v2": 0.6}


def regions(top_used: bool = True, side_used: bool = True, footer_used: bool = True,
            template: str = "default") -> dict[str, Region]:
    """四个区域。没人用 top / side / footer 时，stage 把那块空间也占上。"""
    w, h = FRAME_WIDTH, FRAME_HEIGHT
    left, right, top, bottom = -w / 2 + MARGIN, w / 2 - MARGIN, h / 2 - MARGIN, -h / 2 + MARGIN
    if template == "v2":
        bottom = -h / 2 + SUBTITLE_STRIP["v2"] + 0.1
        band = TEMPLATES["v2"]["top"]
        header = top_used or side_used  # side 就在顶栏里
        stage_top = top - band - 0.2 if header else top
        stage_bottom = bottom + 1.1 if footer_used else bottom
        return {
            "top": Region("top", left, right, top - band, top),
            "stage": Region("stage", left, right, stage_bottom, stage_top),
            "side": Region("side", left, right, top - band, top),
            "footer": Region("footer", left, right, bottom, bottom + 1.0),
        }
    band, side = TEMPLATES[template]["top"], TEMPLATES[template]["side"]
    gap = 0.2 if template == "default" else 0.4
    stage_top = top - band - 0.2 if top_used else top
    stage_bottom = bottom + 1.1 if footer_used else bottom
    stage_right = right - side - gap if side_used else right
    return {
        "top": Region("top", left, right, top - band, top),
        "stage": Region("stage", left, stage_right, stage_bottom, stage_top),
        "side": Region("side", right - side, right, stage_bottom, stage_top),
        "footer": Region("footer", left, right, bottom, bottom + 1.0),
    }


def check_boxes(boxes: dict[str, tuple[float, float, float, float]], ignore_pairs: set[frozenset[str]],
                pinned: set[str] = frozenset(), template: str = "default") -> list[str]:
    """boxes: 名字 → (x0, x1, y0, y1)。返回出框 / 重叠的描述。"""
    from itertools import combinations

    problems = []
    w, h = FRAME_WIDTH / 2 - MARGIN + 0.05, FRAME_HEIGHT / 2 - MARGIN + 0.05
    low = FRAME_HEIGHT / 2 - SUBTITLE_STRIP[template] - 0.05 if template in SUBTITLE_STRIP else h  # 不许压进字幕带
    for name, (x0, x1, y0, y1) in boxes.items():
        top = FRAME_HEIGHT / 2 - 0.1 if name in pinned else h
        if x0 < -w or x1 > w or y0 < -low or y1 > top:
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
