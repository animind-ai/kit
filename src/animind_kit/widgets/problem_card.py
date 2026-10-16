"""题卡：卡片底 + 左侧色条 + 若干行混排文字。"""

from __future__ import annotations

from manim import DOWN, LEFT, RIGHT, Rectangle, RoundedRectangle, VGroup

from ..text import rich
from .base import Built, Widget


class ProblemCard(Widget):
    name = "problem_card"

    def build(self) -> Built:
        t = self.theme
        lines = VGroup()
        spans: dict = {}
        for line in self.definition["lines"]:
            r = rich(line, t, size=30)
            lines.add(r.mob)
            spans.update(r.spans)
        lines.arrange(DOWN, buff=0.2, aligned_edge=LEFT)
        bg = RoundedRectangle(
            width=lines.width + 0.6, height=lines.height + 0.42, corner_radius=0.14,
            fill_color=t.CARD, fill_opacity=1, stroke_width=0,
        ).move_to(lines)
        bar_color = t.color(self.definition.get("role"), t.HL_INK)
        bar = Rectangle(width=0.08, height=bg.height - 0.24, fill_color=bar_color, fill_opacity=1, stroke_width=0)
        bar.next_to(bg.get_left(), RIGHT, buff=0.1)
        card = VGroup(bg, bar, lines)
        return Built(card, spans=spans, items=list(lines))
