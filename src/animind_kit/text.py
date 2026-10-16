"""文字与公式：中文用开源的 Noto Sans CJK SC（思源黑体），公式走 xelatex + ctex。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from manim import MathTex, Tex, TexTemplate, Text, VGroup

from scene_spec.textmarks import ROLES, STYLE_CLASSES, Span, parse_object_text

from .theme import Theme

FONT = "Noto Sans CJK SC"

CJK_TEMPLATE = TexTemplate(
    tex_compiler="xelatex",
    output_format=".xdv",
    preamble=r"\usepackage{amsmath}\usepackage{amssymb}"
    r"\usepackage[fontset=none]{ctex}\setCJKmainfont{Noto Sans CJK SC}"
    r'\xeCJKDeclareCharClass{CJK}{"2460 -> "24FF}'
    r"\setlength{\textwidth}{40cm}\setlength{\linewidth}{40cm}\setlength{\hsize}{40cm}",
)


def plain(text: str, size: float = 36, color: str | None = None) -> Text:
    return Text(text, font=FONT, font_size=size, color=color)


def mixed(*parts: str, size: float = 30, color: str | None = None) -> Tex:
    """中文和公式混排，每个参数一段，可单独上色、单独指向。"""
    return Tex(*parts, tex_template=CJK_TEMPLATE, font_size=size, color=color)


def math(tex: str, size: float = 44, color: str | None = None) -> MathTex:
    return MathTex(tex, font_size=size, color=color)


_UNESCAPE = re.compile(r"\\([\[\]{}])")


def _tex_escape_plain(segment: str) -> str:
    """片段外的普通文字：去掉 \\[ \\] 转义；$...$ 原样交给 LaTeX。"""
    return _UNESCAPE.sub(r"\1", segment)


@dataclass
class Rich:
    """一段带片段标注的文字渲染结果。"""

    mob: Tex
    spans: dict[str, VGroup] = field(default_factory=dict)


def rich(text: str, theme: Theme, size: float = 30, color: str | None = None) -> Rich:
    """把 '令 [$b_n$]{#b .B}，则…' 渲染成 Tex，片段单独成段、按角色上色。"""
    parsed = parse_object_text(text)
    if parsed.problems:
        raise ValueError("；".join(parsed.problems))
    # 重新按片段切分原文：parse_object_text 只给片段内容，这里要把片段外的文字也切出来
    parts: list[tuple[str, Span | None]] = []
    cursor = 0
    for span in parsed.spans:
        start = _find_span(text, span, cursor)
        if start > cursor:
            parts.append((text[cursor:start], None))
        end = text.index("}", text.index("]{", start)) + 1
        parts.append((span.text, span))
        cursor = end
    if cursor < len(text):
        parts.append((text[cursor:], None))

    tex_parts = []
    for body, span in parts:
        piece = _tex_escape_plain(body)
        if span and "bold" in span.classes:
            piece = r"\textbf{" + piece + "}"
        if span and "italic" in span.classes:
            piece = r"\textit{" + piece + "}"
        tex_parts.append(piece)
    mob = mixed(*tex_parts, size=size, color=color or theme.FG)
    out = Rich(mob)
    for i, (_, span) in enumerate(parts):
        if span is None:
            continue
        role = next((c for c in span.classes if c in ROLES), None)
        if role:
            mob[i].set_color(theme.ink(role))
        if span.id:
            out.spans[span.id] = mob[i]
    return out


def _find_span(text: str, span: Span, start: int) -> int:
    needle = "[" + span.text + "]{"
    pos = text.find(needle, start)
    if pos < 0:
        raise ValueError(f"找不到片段 [{span.text}]")
    return pos
