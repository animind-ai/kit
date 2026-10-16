"""步骤胶囊：一排圆角标签，focus 某一个时反色。"""

from __future__ import annotations

from manim import RIGHT, Animation, RoundedRectangle, VGroup

from ..text import plain
from .base import Built, Widget


class Chips(Widget):
    name = "chips"

    def build(self) -> Built:
        t = self.theme
        self.ink = t.color(self.definition.get("role"), t.A)
        chips = VGroup()
        for text in self.definition["items"]:
            label = plain(text, 22, self.ink)
            bg = RoundedRectangle(
                width=label.width + 0.4, height=0.5, corner_radius=0.25,
                fill_color=t.CARD, fill_opacity=1, stroke_width=0,
            )
            chips.add(VGroup(bg, label))
        chips.arrange(RIGHT, buff=0.3)
        self.current: int | None = None
        return Built(chips, items=list(chips))

    def state(self) -> dict:
        return {"current": self.current}

    def restore(self, state: dict) -> None:
        self.current = state.get("current")
        for i, chip in enumerate(self.built.items):
            self._paint(chip, i == self.current)

    def _paint(self, chip, on: bool) -> None:
        chip[0].set_fill(self.ink if on else self.theme.CARD)
        chip[1].set_color(self.theme.BG if on else self.ink)

    def focus(self, index: int | None) -> list[Animation]:
        self.current = index
        anims = []
        for i, chip in enumerate(self.built.items):
            on = i == index
            anims.append(chip[0].animate.set_fill(self.ink if on else self.theme.CARD))
            anims.append(chip[1].animate.set_color(self.theme.BG if on else self.ink))
        return anims
