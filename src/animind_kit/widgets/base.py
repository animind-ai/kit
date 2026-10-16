"""组件基类。

每个组件负责：
- 由定义 + 主题 + data 建出自己的 Mobject；
- 暴露子元素：spans（obj#id）、parts（obj.part[i]）、items（obj[i]）；
- 自己的动作（返回动画列表）；
- 跨 beat 的内部状态（例如差分表已经推出了几个数）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from manim import Animation, Mobject, VGroup

from ..theme import Theme


@dataclass
class Built:
    mob: Mobject
    spans: dict[str, Mobject] = field(default_factory=dict)
    parts: dict[str, list[Mobject]] = field(default_factory=dict)
    items: list[Mobject] = field(default_factory=list)


class Widget:
    name = "widget"

    def __init__(self, obj_id: str, definition: dict, theme: Theme, data: dict):
        self.obj_id = obj_id
        self.definition = definition
        self.theme = theme
        self.data = data
        self.built = self.build()

    def build(self) -> Built:
        raise NotImplementedError

    @property
    def mob(self) -> Mobject:
        return self.built.mob

    # ---- 跨 beat 状态 ----
    def state(self) -> dict[str, Any]:
        return {}

    def restore(self, state: dict[str, Any]) -> None:
        pass

    # ---- 组件动作：返回 (动画列表, 新增到画面的 mobject 列表) ----
    def action(self, act: str, args: dict) -> tuple[list[Animation], list[Mobject]]:
        raise KeyError(f"组件 {self.name} 没有动作 {act}")

    # ---- 强调：组件可以覆盖，给出更贴合的表现 ----
    def focus(self, index: int | None) -> list[Animation] | None:
        return None


def group(*mobs: Mobject) -> VGroup:
    return VGroup(*mobs)
