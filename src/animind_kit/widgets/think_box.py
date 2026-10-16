""""想一想"框：淡色底、细描边，若干行文字。第一行若是 .bold 片段，用结论色。"""

from __future__ import annotations

from manim import DOWN, LEFT, RoundedRectangle, VGroup

from ..text import rich
from .base import Built, Widget


class ThinkBox(Widget):
    name = "think_box"

    def build(self) -> Built:
        t = self.theme
        texts = VGroup()
        spans: dict = {}
        for i, line in enumerate(self.definition["lines"]):
            r = rich(line, t, size=27, color=t.HL_INK if i == 0 and line.startswith("[") else t.FG)
            texts.add(r.mob)
            spans.update(r.spans)
        texts.arrange(DOWN, buff=0.16, aligned_edge=LEFT)
        if texts.width > 11.8:
            texts.scale_to_fit_width(11.8)
        box = RoundedRectangle(
            width=texts.width + 0.6, height=texts.height + 0.4, corner_radius=0.14,
            fill_color=t.MARK, fill_opacity=0.6, stroke_color=t.HL_INK, stroke_width=2,
        ).move_to(texts)
        return Built(VGroup(box, texts), spans=spans, items=list(texts))
