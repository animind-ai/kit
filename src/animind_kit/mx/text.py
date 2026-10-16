"""带片段标注的混排文字 → 墨线节点。

'令 [$b_n=A_{n+1}-A_n$]{#b_def .B}，则 …' 会被拆成若干段：中文走字体轮廓（moxian.text.Text），
$...$ 和含反斜杠的片段走 LaTeX（moxian.text.Tex）。每个带 #id 的片段是一个 Group，可以单独指向。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from moxian.core import DOWN, RIGHT, Group, Node
from moxian.text import Tex, Text, math

from scene_spec.textmarks import ROLES, Span, parse_object_text

from ..theme import Theme

_UNESCAPE = re.compile(r"\\([\[\]{}])")
_MATH = re.compile(r"\$([^$]*)\$")


@dataclass
class Rich:
    node: Group
    spans: dict[str, Node] = field(default_factory=dict)


def segments(text: str) -> list[tuple[str, Span | None]]:
    """把原文切成 (文本, 片段或 None) 的列表，顺序和原文一致。"""
    parsed = parse_object_text(text)
    if parsed.problems:
        raise ValueError("；".join(parsed.problems))
    parts: list[tuple[str, Span | None]] = []
    cursor = 0
    for span in parsed.spans:
        needle = "[" + span.text + "]{"
        start = text.find(needle, cursor)
        if start < 0:
            raise ValueError(f"找不到片段 [{span.text}]")
        if start > cursor:
            parts.append((text[cursor:start], None))
        end = text.index("}", start + len(needle)) + 1
        parts.append((span.text, span))
        cursor = end
    if cursor < len(text):
        parts.append((text[cursor:], None))
    return parts


def runs(body: str, size: float, color: str, weight: str = "normal") -> list[Node]:
    """一段文本里的中文和公式交替：返回节点列表。"""
    out: list[Node] = []
    pos = 0
    for m in _MATH.finditer(body):
        if m.start() > pos:
            out += _plain(body[pos : m.start()], size, color, weight)
        if m.group(1).strip():
            out.append(math(m.group(1), size, color))
        pos = m.end()
    if pos < len(body):
        out += _plain(body[pos:], size, color, weight)
    return out


def _plain(text: str, size: float, color: str, weight: str = "normal") -> list[Node]:
    text = _UNESCAPE.sub(r"\1", text)
    if not text:
        return []
    if "\\" in text:  # 含 LaTeX 命令（例如 \underline）：整段交给 TeX 的文本模式
        return [Tex(text, size, color)]
    return [Text(text, size, color, weight=weight)]


def inline(parts: list[Node], pad: float = 0.03) -> Group:
    """把若干节点排成一行。

    文字按字体的前进宽度排（空格才不会丢），公式按包围盒排并两侧留 pad；
    文字共用基线（Text 的原点就是基线左端），公式按 x 高度中线对齐到文字。
    """
    row = Group(*parts)
    cursor = 0.0
    for node in parts:
        if isinstance(node, Text):
            node.shift([cursor - float(node.matrix[0, 2]), 0.0])
            cursor += node.advance
        else:
            b = node.bbox()
            node.shift([cursor + pad - b.x0, 0.0])
            cursor += b.width + 2 * pad
    base = next((p for p in parts if isinstance(p, Text)), None)
    for node in parts:
        if isinstance(node, Text) or node is base:
            continue
        if base is None:
            node.align_to(parts[0], DOWN)
        else:
            target_y = base.x_height / 2 + 0.02  # 基线在 y=0
            node.shift([0.0, target_y - node.center()[1]])
    return row


def rich(text: str, theme: Theme, size: float = 30, color: str | None = None) -> Rich:
    nodes: list[Node] = []
    spans: dict[str, Node] = {}
    base_color = color or theme.FG
    for body, span in segments(text):
        role = next((c for c in span.classes if c in ROLES), None) if span else None
        piece_color = theme.ink(role) if role else base_color
        weight = "bold" if span and "bold" in span.classes else "normal"
        piece_nodes = runs(body, size, piece_color, weight)
        if span and span.id:
            spans[span.id] = Group(*piece_nodes) if len(piece_nodes) != 1 else piece_nodes[0]
        nodes += piece_nodes
    row = inline(nodes)
    # 片段 Group 只是引用容器，不进树；它的子节点已经在 row 里
    return Rich(row, spans)
