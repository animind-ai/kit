"""总结卡：每行一个色块标签 + 一句话。"""

from __future__ import annotations

from manim import DOWN, LEFT, RIGHT, RoundedRectangle, VGroup

from ..text import plain, rich
from .base import Built, Widget


class Summary(Widget):
    name = "summary"

    def build(self) -> Built:
        t = self.theme
        rows = VGroup()
        for row in self.definition["rows"]:
            color = t.color(row.get("role"), t.A)
            text = plain(row["tag"], 22, t.BG)
            badge = VGroup(
                RoundedRectangle(width=max(1.5, text.width + 0.4), height=0.5, corner_radius=0.25,
                                 fill_color=color, fill_opacity=1, stroke_width=0),
                text,
            )
            body = rich(row["text"], t, size=28).mob
            rows.add(VGroup(badge, body).arrange(RIGHT, buff=0.3))
        rows.arrange(DOWN, buff=0.36, aligned_edge=LEFT)
        return Built(rows, items=list(rows))
