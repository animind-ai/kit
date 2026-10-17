"""读数：一个随参数变化的数（readout 对象、斜率三角形的边长标注）。

数值用字体轮廓排（每帧都可能是新数字，LaTeX 太慢），分数用 LaTeX 的 \\frac（只有恰好是分数的那几帧才用到，
按内容缓存）。负号用 U+2212，和公式里的减号一样长。
"""

from __future__ import annotations

import math
from fractions import Fraction

from moxian.anim import Dynamic, Param
from moxian.core import Group, Node
from scene_spec.expr import evaluate, free_names, parse

from ..theme import Theme
from .text import mmath, mtext, rich

MAX_DEN = 12  # format: fraction 时分母的上限
_CACHE: dict[tuple, Node] = {}


def format_value(v: float, digits: int = 2, style: str = "decimal") -> tuple[str, str]:
    """→ ("text", "0.50") 或 ("tex", r"\\frac{1}{2}")；没有定义时 ("text", "—")。"""
    if not math.isfinite(v):
        return "text", "—"
    if style == "fraction":
        f = Fraction(v).limit_denominator(MAX_DEN)
        if abs(float(f) - v) < 1e-9:
            if f.denominator == 1:
                return "text", _minus(str(f.numerator))
            sign = "-" if f < 0 else ""
            return "tex", rf"{sign}\frac{{{abs(f.numerator)}}}{{{f.denominator}}}"
        digits = 2
    text = f"{v:.{digits}f}"
    if float(text) == 0:
        text = f"{0:.{digits}f}"  # 不显示 −0.00
    return "text", _minus(text)


def trim(v: float, digits: int = 2) -> str:
    """去掉末尾的 0：2.50 → 2.5，2.00 → 2（标注边长用）。"""
    kind, text = format_value(v, digits)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _minus(text: str) -> str:
    return text.replace("-", "−")


def number(v: float, size: float, color: str, digits: int = 2, style: str = "decimal", trimmed: bool = False) -> Node:
    """一个数的节点（左下角在原点附近，基线在 y=0）。按 (内容, 字号, 颜色) 缓存，返回的节点套一层 Group，可以放心挪。"""
    kind, text = ("text", trim(v, digits)) if trimmed else format_value(v, digits, style)
    key = (kind, text, size, color)
    node = _CACHE.get(key)
    if node is None:
        node = mmath(text, size, color) if kind == "tex" else mtext(text, size, color)
        b = node.bbox()
        if b is not None:
            node.shift([-b.x0, 0.0])
        if len(_CACHE) < 4000:
            _CACHE[key] = node
    return Group(node)


class Readout:
    """readout 对象：label（可混排公式）+ 值。值随参数重算，左端固定，数字变宽时往右长。"""

    GAP = 0.18

    def __init__(self, definition: dict, theme: Theme, params: dict[str, Param], size: float):
        self.definition = definition
        value = definition["value"]
        self.tree = parse(str(value), set(params), allow_x=False) if isinstance(value, str) else ("num", float(value))
        self.params = params
        deps = [params[n] for n in sorted(free_names(self.tree))]
        role = definition.get("role")
        self.color = theme.ink(role)
        self.size = size
        self.digits = int(definition.get("digits", 2))
        self.style = definition.get("format", "decimal")
        self.label: Node | None = None
        if definition.get("label"):
            self.label = rich(definition["label"], theme, size=size, color=theme.FG).node
            b = self.label.bbox()
            self.label.shift([-b.x0, -b.center[1]])
        self.node = Dynamic(self._build, *deps, name="readout")

    def value(self) -> float:
        env = {k: p.value for k, p in self.params.items()}
        return evaluate(self.tree, math.nan, env)

    def _build(self) -> list[Node]:
        num = number(self.value(), self.size * 1.05, self.color, self.digits, self.style)
        b = num.bbox()
        x = 0.0 if self.label is None else self.label.bbox().x1 + self.GAP
        if b is not None:
            num.shift([x - b.x0, -b.center[1]])
        return [n for n in (self.label, num) if n is not None]
