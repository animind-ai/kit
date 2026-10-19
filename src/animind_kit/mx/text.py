"""带片段标注的混排文字 → 墨线节点。

'令 [$b_n=A_{n+1}-A_n$]{#b_def .B}，则 …' 会被拆成若干段：中文走字体轮廓（moxian.text.Text），
$...$ 和含反斜杠的片段走 LaTeX（moxian.text.Tex）。每个带 #id 的片段是一个 Group，可以单独指向。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from moxian.core import PX, Group, Node
from moxian.text import Tex, Text, math
from moxian.text.font import DEFAULT_FONT, FULLWIDTH_PUNCT, is_han, spacing_adjustments, typeface
from scene_spec.textmarks import ROLES, Span, parse_object_text

from ..theme import Theme

# kit 的字号沿用 Manim 的直觉（同一个数字在 Manim 里约大 1.4 倍）；交给墨线前统一换算
SCALE = 1.4


# 字体族：text 是正文（默认墨线的中文字体，中西文都能排），code 是等宽代码字体。
# 场景文件的 style.fonts 可以换掉（编程课用 Inter + Source Code Pro）；后端开始渲染前调 set_fonts。
FONTS = {"text": DEFAULT_FONT, "code": DEFAULT_FONT}
# 字号换算的额外倍数（乘在 SCALE 上）。SCALE 是按默认中文字体对 Manim 标的；讲座版式照编程课的 cs_style（Inter、
# Source Code Pro）在同一个字号下看起来大一圈，按 L06 的末帧量出来正文约 1.32 倍、代码约 1.45 倍。
FONT_SCALE = {"text": 1.0, "code": 1.0}


def set_fonts(fonts: dict | None, log=print, scale: dict | None = None) -> None:
    """按 style.fonts 设字体；系统里没有的字体退回默认字体并提示。不写时恢复默认。scale：见 FONT_SCALE。"""
    FONTS.update(text=DEFAULT_FONT, code=DEFAULT_FONT)
    FONT_SCALE.update({"text": 1.0, "code": 1.0, **(scale or {})})
    for key, family in (fonts or {}).items():
        try:
            typeface(family)
        except RuntimeError:
            log(f"FONT 系统里没有字体 {family!r}，{key} 用 {DEFAULT_FONT}")
            continue
        FONTS[key] = family


def mtext(text: str, size: float, color: str, weight: str = "normal", mono: bool = False) -> Text:
    key = "code" if mono else "text"
    return Text(text, size * SCALE * FONT_SCALE[key], color, family=FONTS[key], weight=weight)


def mmath(tex: str, size: float, color: str) -> Node:
    return math(tex, size * SCALE, color)


def mtex(tex: str, size: float, color: str) -> Node:
    return Tex(tex, size * SCALE, color)


_UNESCAPE = re.compile(r"\\([\[\]{}])")
_MATH = re.compile(r"\$([^$]*)\$")
# 文本模式的间距命令和空白；宽度以 em 计（Computer Modern 10pt：词间空 3.33pt，\quad 10pt）
_SPACING = re.compile(r"\\(?:qquad|quad)(?![A-Za-z])\s*|\\[ ,:>;!]|~|\s+")
_SPACING_EM = {"\\qquad": 2.0, "\\quad": 1.0, "\\,": 3 / 18, "\\:": 4 / 18, "\\>": 4 / 18, "\\;": 5 / 18, "\\!": -3 / 18}
_WORD_SPACE_EM = 1 / 3


class Space:
    """一段水平空白：只在 inline 里推进光标，不进节点树。

    soft 表示它只来自普通空白：挨着汉字或全角标点时按中文排版的习惯并入默认间距（同 xeCJK），
    挨着公式或西文时才是一个词间空。显式命令（\\quad、\\ 、~ 等）总是算数。
    """

    def __init__(self, width: float, soft: bool = False):
        self.width = width
        self.soft = soft

    def __repr__(self) -> str:
        return f"Space({self.width:.3f}{', soft' if self.soft else ''})"


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


def runs(body: str, size: float, color: str, weight: str = "normal", mono: bool = False) -> list[Node | Space]:
    """一段文本里的中文和公式交替：返回节点列表（片段间的空白和间距命令是 Space）。"""
    out: list[Node | Space] = []
    pos = 0
    for m in _MATH.finditer(body):
        if m.start() > pos:
            out += _plain(body[pos : m.start()], size, color, weight, mono)
        if m.group(1).strip():
            out.append(mmath(m.group(1), size, color))
        pos = m.end()
    if pos < len(body):
        out += _plain(body[pos:], size, color, weight, mono)
    return out


def _space(tokens: list[str], size: float) -> Space:
    em = 0.0
    for tok in tokens:
        name = tok.rstrip() or " "
        em += _SPACING_EM.get(name, _WORD_SPACE_EM)
    soft = all(not tok.strip() for tok in tokens)
    return Space(em * size * SCALE * PX, soft)  # 和 Tex 一样：10pt 字对应 size 像素


def _plain(text: str, size: float, color: str, weight: str = "normal", mono: bool = False) -> list[Node | Space]:
    """普通文本 → 节点。首尾的空白、间距命令变成 Space；段内的间距命令把文字切开。"""
    text = _UNESCAPE.sub(r"\1", text)
    pieces: list[str | list[str]] = []  # 文字，或者一串相邻的间距记号
    pos = 0
    for m in _SPACING.finditer(text):
        if m.start() > pos:
            pieces.append(text[pos : m.start()])
        if pieces and isinstance(pieces[-1], list):
            pieces[-1].append(m.group())
        else:
            pieces.append([m.group()])
        pos = m.end()
    if pos < len(text):
        pieces.append(text[pos:])
    # 段内（两边都是文字）的普通空白留在文字里，按字体前进宽度排；其余的合进相邻文字或成为 Space
    merged: list[str | list[str]] = []
    for i, piece in enumerate(pieces):
        inner = 0 < i < len(pieces) - 1
        if isinstance(piece, list) and inner and all(not t.strip() for t in piece):
            piece = "".join(piece)
        if isinstance(piece, str) and merged and isinstance(merged[-1], str):
            merged[-1] += piece
        else:
            merged.append(piece)
    if any("\\" in p for p in merged if isinstance(p, str)):
        # 含别的 LaTeX 命令（例如 \underline{a\,b}）：只剥掉首尾的间距，中间整段交给 TeX 的文本模式
        head = merged.pop(0) if isinstance(merged[0], list) else None
        tail = merged.pop() if merged and isinstance(merged[-1], list) else None
        core = "".join(p if isinstance(p, str) else "".join(p) for p in merged)
        out: list[Node | Space] = [mtex(core, size, color)]
        return ([_space(head, size)] if head else []) + out + ([_space(tail, size)] if tail else [])
    return [_space(p, size) if isinstance(p, list) else mtext(p, size, color, weight, mono) for p in merged]


def _cjk_edge(node: Node | Space | None, side: int) -> bool:
    """node 的第 side 个字符（0 是开头，-1 是结尾）是不是汉字或全角标点。"""
    if not isinstance(node, Text) or not node.text:
        return False
    ch = node.text[side]
    return is_han(ch) or ch in FULLWIDTH_PUNCT


def _resolve_soft(parts: list[Node | Space]) -> list[Node | Space]:
    """处理只来自普通空白的 Space。

    两边都是文字（片段边界上的空格）：和段内一样，按字体里空格的前进宽度排；
    否则挨着汉字或全角标点时去掉，用默认的中西文间距；挨着公式或西文时保留，是一个词间空。
    """
    out: list[Node | Space] = []
    for i, p in enumerate(parts):
        if isinstance(p, Space) and p.soft:
            left = next((q for q in reversed(parts[:i]) if not isinstance(q, Space)), None)
            right = next((q for q in parts[i + 1 :] if not isinstance(q, Space)), None)
            if isinstance(left, Text) and isinstance(right, Text):
                p = Space(Text(" ", left.em / PX, family=left.family).advance)
            elif _cjk_edge(left, -1) or _cjk_edge(right, 0):
                continue
        out.append(p)
    return out


def _gap_between(left: Node | Space | None, right: Node | Space) -> float:
    """混排时两个相邻片段之间的距离（画面单位）。

    汉字和行内公式之间留四分之一个字宽（中西文间距）；紧挨着全角标点不留（标点自带半个字宽的空白）；
    公式和公式之间留一点点。
    """
    if left is None:
        return 0.0
    if isinstance(left, Space) or isinstance(right, Space):
        return 0.0  # 显式空白：宽度就是 Space 自己
    if isinstance(left, Text) and isinstance(right, Text):
        # 两段文字相接（例如片段边界）：用和段内相同的标点挤压、中西文间距规则
        a, b = left.text[-1:], right.text[:1]
        return spacing_adjustments(a + b)[1] * left.em if a and b else 0.0
    if isinstance(left, Text):
        ch, em = left.text[-1:] or " ", left.em
    elif isinstance(right, Text):
        ch, em = right.text[:1] or " ", right.em
    else:
        return 0.06
    if is_han(ch):
        return 0.25 * em  # 汉字和公式之间留四分之一个字宽
    if ch in FULLWIDTH_PUNCT or ch.isspace():
        return 0.0  # 全角标点自带空白
    return 0.03  # 连字符等西文标点：只留一点点


def inline(items: list[Node | Space]) -> Group:
    """把若干节点排成一行。

    文字按字体的前进宽度排（空格才不会丢），公式按包围盒排；片段之间的距离见 _gap_between，
    Space 只推进光标。文字共用基线（Text 的原点就是基线左端），公式按 x 高度中线对齐到文字（没有文字时按中线对齐）。
    """
    items = _resolve_soft(items)
    parts = [p for p in items if not isinstance(p, Space) and p.bbox() is not None]
    row = Group(*parts)
    cursor = 0.0
    prev: Node | Space | None = None
    for node in items:
        if not isinstance(node, Space) and node.bbox() is None:
            continue  # 空公式之类：不占位置
        cursor += _gap_between(prev, node)
        if isinstance(node, Space):
            cursor += node.width
        elif isinstance(node, Text):
            node.shift([cursor - float(node.matrix[0, 2]), 0.0])
            cursor += node.advance
        else:
            b = node.bbox()
            node.shift([cursor - b.x0, 0.0])
            cursor += b.width
        prev = node
    base = next((p for p in parts if isinstance(p, Text)), None)
    for node in parts:
        if isinstance(node, Text) or node is base:
            continue
        if base is None:  # 全是公式：中线对齐到第一个（和有文字时按 x 高度中线对齐是一回事）
            node.shift([0.0, parts[0].center()[1] - node.center()[1]])
        else:
            target_y = base.x_height / 2 + 0.02  # 基线在 y=0
            node.shift([0.0, target_y - node.center()[1]])
    return row


def rich(text: str, theme: Theme, size: float = 30, color: str | None = None) -> Rich:
    nodes: list[Node | Space] = []
    spans: dict[str, Node] = {}
    base_color = color or theme.FG
    for body, span in segments(text):
        role = next((c for c in span.classes if c in ROLES), None) if span else None
        piece_color = theme.ink(role) if role else base_color
        weight = "bold" if span and "bold" in span.classes else "normal"
        pieces = runs(body, size, piece_color, weight, mono=bool(span and "code" in span.classes))
        piece_nodes = [p for p in pieces if not isinstance(p, Space)]
        if span and span.id:
            spans[span.id] = Group(*piece_nodes) if len(piece_nodes) != 1 else piece_nodes[0]
        nodes += pieces
    row = inline(nodes)
    # 片段 Group 只是引用容器，不进树；它的子节点已经在 row 里
    return Rich(row, spans)
