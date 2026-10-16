"""主题：把 palette 的语义角色翻成 Manim 用的颜色。

色值只来自 animind-ai/palette（data/palette.json 是它的副本，MIT）。
场景文件里只写角色（FG/A/B/C/HL/NO/DIM），这里负责：
- 角色 → 颜色；
- 派生色：卡片底色、mark 垫色、浅底上结论文字用的深色（HL_INK）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

ROLES = ("FG", "A", "B", "C", "HL", "NO", "DIM")


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0, min(1, c)) * 255):02X}" for c in rgb)


def blend(fg: str, bg: str, amount: float) -> str:
    """amount=1 完全是 fg，0 完全是 bg。"""
    a, b = _hex_to_rgb(fg), _hex_to_rgb(bg)
    return _rgb_to_hex(tuple(x * amount + y * (1 - amount) for x, y in zip(a, b)))  # type: ignore[arg-type]


def luminance(value: str) -> float:
    r, g, b = _hex_to_rgb(value)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@dataclass(frozen=True)
class Theme:
    name: str
    BG: str
    FG: str
    A: str
    B: str
    C: str
    HL: str
    NO: str
    DIM: str
    HL_INK: str  # 结论文字 / 描边用（浅底上 HL 太淡时另给）
    CARD: str  # 卡片、胶囊的底
    MARK: str  # mark 动作垫在元素下面的淡色
    is_light: bool

    def color(self, role: str | None, default: str | None = None) -> str:
        if role is None:
            return default or self.FG
        return getattr(self, role)

    def ink(self, role: str | None) -> str:
        """文字用色：HL 在浅底上换成 HL_INK。"""
        if role == "HL":
            return self.HL_INK
        return self.color(role)

    def fill_opacity(self) -> float:
        return 0.30 if self.is_light else 0.38


def _load_palettes() -> dict:
    return json.loads(resources.files("animind_kit").joinpath("data", "palette.json").read_text(encoding="utf-8"))


def theme(name: str = "paper") -> Theme:
    palettes = _load_palettes()
    if name not in palettes:
        raise KeyError(f"palette 里没有主题 {name!r}（有 {', '.join(palettes)}）")
    p = palettes[name]
    bg, fg = p["BG"], p["FG"]
    light = luminance(bg) > 0.5
    hl_ink = p.get("C_HL_INK", p["C_HL"] if not light else blend(p["C_HL"], fg, 0.75))
    return Theme(
        name=name,
        BG=bg,
        FG=fg,
        A=p["C_A"],
        B=p["C_B"],
        C=p["C_C"],
        HL=p["C_HL"],
        NO=p["C_NO"],
        DIM=p["C_DIM"],
        HL_INK=hl_ink,
        CARD=blend(fg, bg, 0.06),
        MARK=blend(p.get("C_HL_MARK", p["C_HL"]), bg, 0.45 if light else 0.35),
        is_light=light,
    )


def themes() -> list[str]:
    return list(_load_palettes())
