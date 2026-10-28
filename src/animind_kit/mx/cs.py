"""编程课组件（墨线实现），设计见 scene-spec 的 docs/cs-lecture.md。

画面照着课程仓库的 cs_style.py（Manim 版）做：浅纸色底、青色结构、琥珀色「看这里」、红色只给错误；
正文和代码的字体来自 style.fonts（text.set_fonts）。颜色全部取自主题角色，深色终端用 FG 做底、BG 做字。

- heading：左上角的小字（kicker）+ 标题 + 一小段 HL 短线；动作 retitle 换标题，小字不动。
- code：语法高亮的代码面板（pygments 切词，词类 → 角色）。暴露 line[i]、spans 里声明的 #片段。
  动作 highlight / unhighlight（整行宽的底色条）、point / unpoint（左边的三角箭头，Python Tutor 式）、jump / unjump（左边的弯箭头）。
- terminal：深色面板上的输出，保留行首空格；每行一项，用内置的 show obj[i] 逐行出现。
- call_stack：底帧（global frame）上面压帧；动作 push / pop / note / unnote。暴露 frame[i]、value[i]、note[i]、base。
- bindings：名字 → 箭头 → 值框（环境图）；动作 rebind（箭头转到另一个对象）、update（对象里的值变了）。
- card、pill、bullets：卡片（彩色竖条 + 标题 + 正文）、胶囊标签、编号要点。
- flow：格子上的流程图（方框、菱形、胶囊），直线或折线箭头。
- image：一张位图（相对场景文件的路径）。
- queue：排队的人（队首在右）；动作 ask（问号气泡往前传）、number（编号从队首传回来）。

位置、透明度都在动画开始播放时才改：一个 beat 的动作先全部建好再播放，建动作时只往树里加藏着的节点。
"""

from __future__ import annotations

from pathlib import Path as FsPath

import numpy as np
from moxian.anim import Animation, Draw, Restyle, Shift, fade_in, fade_out
from moxian.core import DOWN, LEFT, PX, RIGHT, Group, ImageNode, Node, PathNode, Style, arrange
from moxian.core import transform as T
from moxian.path import Path, circle, line, polygon, rect, rounded_rect
from scene_spec.cs import code_lines

from ..regions import FRAME_WIDTH, MARGIN
from ..theme import blend
from .dots import FadeTo, Steps, Travel, reserve, straight
from .text import FONT_SCALE, SCALE, mtext, rich
from .widgets import Built, Widget

WHITE = "#FFFFFF"


def em(size: float, mono: bool = False) -> float:
    """字号 size（kit 字号）的一个字身高，画面单位。"""
    return size * SCALE * FONT_SCALE["code" if mono else "text"] * PX


def char_width(size: float) -> float:
    """等宽字体一个字符的前进宽度。"""
    return mtext("0" * 10, size, "#000000", mono=True).advance / 10


def arrow_path(p0, p1, tip: float = 0.16) -> Path:
    """一根直箭头：线 + 终点的 V 形箭头（只描边）。"""
    a, b = np.asarray(p0, dtype=float), np.asarray(p1, dtype=float)
    path = line(*a, *b)
    return path.extend(chevron(b, b - a, tip))


def chevron(point, direction, tip: float = 0.16) -> Path:
    d = np.asarray(direction, dtype=float)
    d = d / (np.linalg.norm(d) or 1.0)
    n = np.array([-d[1], d[0]])
    p = np.asarray(point, dtype=float)
    a, c = p - d * tip + n * tip * 0.6, p - d * tip - n * tip * 0.6
    return Path().move_to(*a).line_to(*p).line_to(*c)


def polyline(points) -> Path:
    path = Path().move_to(*points[0])
    for q in points[1:]:
        path.line_to(*q)
    return path


class CSWidget(Widget):
    """内置的 set {role, opacity} 指向一部分（一组子节点）时，逐个改它们的样式。"""

    def restyle(self, sel, args: dict) -> list[Animation] | None:
        if sel.part is None and sel.index is None and sel.span is None:
            return None
        if sel.span is not None:
            members = [self.built.spans[sel.span]]
        elif sel.part is not None:
            cells = self.built.parts[sel.part]
            members = cells[sel.index[0] : sel.index[1]] if sel.index else list(cells)
        else:
            members = self.built.items[sel.index[0] : sel.index[1]]
        out: list[Animation] = []
        for m in members:
            targets = m.children if isinstance(m, Group) and m.style is None and m.children else [m]
            for node in targets:
                changes = {}
                if "opacity" in args:
                    changes["opacity"] = float(args["opacity"])
                if "role" in args and node.style is not None and node.style.fill is not None:
                    changes["fill"] = self.theme.ink(args["role"])
                if changes:
                    out.append(Restyle(node, 0.3, **changes))
        return out

    def ink(self, role: str | None, default: str | None = None) -> str:
        return self.theme.ink(role) if role else (default or self.theme.FG)


# ---------- 页眉 ----------

class Heading(CSWidget):
    """左上角小字 + 标题 + 短线。组件宽度占满整个顶栏（看不见的占位），放进 top 区域时内容就靠左。"""

    name = "heading"
    TITLE, KICKER = 40, 20
    WIDTH = (FRAME_WIDTH - 2 * MARGIN) * 0.96  # 布局把对象缩到区域宽度的 96% 以内

    def build(self) -> Built:
        t, d = self.theme, self.definition
        color = self.ink(d.get("role"), t.A)
        self.color = color
        rows: list[Node] = []
        spans: dict[str, Node] = {}
        if "kicker" in d:
            kicker = mtext(d["kicker"], self.KICKER, t.DIM)
            rows.append(kicker)
            spans["kicker"] = kicker
        self.title = mtext(d["text"], self.TITLE, color, "bold")
        rows.append(self.title)
        spans["title"] = self.title
        arrange(rows, DOWN, gap=0.26, align=LEFT)
        y = self.title.bbox().y0 - 0.16
        x = self.title.bbox().x0
        self.rule = PathNode(path=line(x, y, x + 1.0, y), style=Style(stroke=t.HL, stroke_width=5))
        box = Group(*rows, self.rule).bbox()
        self.group = Group(reserve(self.WIDTH, 0.01, x + self.WIDTH / 2, box.center[1]), *rows, self.rule)
        return Built(self.group, spans=spans)

    def action(self, act: str, args: dict) -> list[Animation]:
        if act != "retitle":
            return super().action(act, args)
        if "role" in args:
            self.color = self.ink(args["role"])
        new = mtext(args["text"], self.TITLE, self.color, "bold")
        new.shift(self.title.bbox().point(LEFT) - new.bbox().point(LEFT))
        new.shift([0.0, (self.title.baseline_y() - new.baseline_y())])
        new.set_style(opacity=0.0)
        self.group.add(new)
        old, self.title = self.title, new
        self.built.spans["title"] = new
        return [fade_out(old, 0.4), fade_in(new, 0.5, shift=(0.0, -0.08))]


# ---------- 代码面板 ----------

def _token_style(ttype, t) -> tuple[str, str]:
    """pygments 的词类 → (颜色, 字重)。只用主题角色：关键字 A，字符串 B，数 C，注释 DIM。"""
    from pygments.token import Comment, Keyword, Name, Number, Operator, String

    if ttype in Keyword or ttype in Operator.Word:
        return t.A, "bold"
    if ttype in Name.Builtin:
        return t.A, "normal"
    if ttype in Name.Function or ttype in Name.Class:
        return t.FG, "bold"
    if ttype in String:
        return t.B, "normal"
    if ttype in Number:
        return t.C, "normal"
    if ttype in Comment:
        return t.DIM, "normal"
    return t.FG, "normal"


def tokenized(src: str, language: str = "python") -> list[list[tuple[object, str]]]:
    """按行切好的 (词类, 文字)。"""
    from pygments.lexers import get_lexer_by_name

    lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
    rows: list[list[tuple[object, str]]] = [[]]
    for ttype, value in lexer.get_tokens("\n".join(code_lines(src))):
        parts = value.split("\n")
        for k, part in enumerate(parts):
            if k:
                rows.append([])
            if part:
                rows[-1].append((ttype, part))
    return rows


class Code(CSWidget):
    name = "code"
    SIZE = 26
    PAD = 0.3
    PITCH = 1.55  # 行距（字身高的倍数）

    def build(self) -> Built:
        t, d = self.theme, self.definition
        size = self.SIZE
        self.cw, self.pitch = char_width(size), em(size, mono=True) * self.PITCH
        lines = code_lines(d["src"])
        self.glyphs: list[list[tuple[int, Node]]] = []  # 每行：(列, 字形节点)
        rows: list[Group] = []
        ascent = descent = 0.0
        for i, tokens in enumerate(tokenized(d["src"])[: len(lines)]):
            row = Group(name=f"line{i}")
            cols: list[tuple[int, Node]] = []
            col = 0
            for ttype, value in tokens:
                color, weight = _token_style(ttype, t)
                if value.strip():
                    tx = mtext(value, size, color, weight, mono=True)
                    ascent, descent = max(ascent, tx.ascent), max(descent, tx.descent)
                    tx.shift([col * self.cw, -i * self.pitch])
                    glyphs = iter(tx.children)
                    for j, ch in enumerate(value):
                        if not ch.isspace():
                            cols.append((col + j, next(glyphs)))
                    row.add(tx)
                col += len(value)
            rows.append(row)
            self.glyphs.append(cols)
        self.rows = rows
        self.mid = (ascent - descent) / 2  # 基线到一行中线
        width = max((len(s.rstrip()) for s in lines), default=1) * self.cw
        top, bottom = ascent + self.PAD, -(len(lines) - 1) * self.pitch - descent - self.PAD
        left = -self.PAD
        numbers = Group()
        if d.get("numbers"):
            for i in range(len(lines)):
                num = mtext(str(i + 1), size, t.DIM, mono=True)
                num.shift([-0.35 - num.advance, -i * self.pitch])
                numbers.add(num)
            left -= 0.35 + len(str(len(lines))) * self.cw
        self.x0, self.x1 = left, width + self.PAD
        acts = self.opts.get("acts", set())
        # 场景里会用到弯箭头 / 指针时，左边预先留出位置：动作之后组件的包围盒不变，布局不会把它挤出画面
        gutter = self.jump_room(len(lines)) if "jump" in acts else 0.4 if "point" in acts else 0.0
        bg = PathNode(path=rounded_rect(self.x1 - self.x0, top - bottom, 0.12),
                      style=Style(fill=blend(WHITE, t.BG, 0.8), stroke=t.CARD, stroke_width=2))
        bg.move_to([(self.x0 + self.x1) / 2, (top + bottom) / 2])
        self.bars = Group(name="bars")  # 底色条在字的下面
        self.over = Group(name="over")  # 指针、弯箭头
        self.panel = Group(bg, self.bars, numbers, *rows, self.over)
        if gutter:
            self.panel.add(reserve(gutter, 0.01, self.x0 - gutter / 2, (top + bottom) / 2))
        self.marks: dict[int, Node] = {}
        self.pointer: PathNode | None = None
        self.pointer_line: int | None = None
        self.jumps: list[Node] = []
        spans: dict[str, Node] = {}
        for sid, spec in (d.get("spans") or {}).items():
            spans[sid] = self.span(spec["line"], spec["text"])
        return Built(self.panel, spans=spans, parts={"line": rows}, items=rows)

    def span(self, i: int, text: str) -> Node:
        c0 = code_lines(self.definition["src"])[i].index(text)
        return Group(*[g for c, g in self.glyphs[i] if c0 <= c < c0 + len(text)])

    @staticmethod
    def bulge(span: int) -> float:
        return 0.9 + 0.08 * span

    def jump_room(self, n: int) -> float:
        """弯箭头最多伸出去多远（三次贝塞尔的最远点是控制点偏移的 3/4）再加一点空。"""
        return 0.12 + 0.75 * self.bulge(n) + 0.1

    def line_y(self, i: int) -> float:
        return -i * self.pitch + self.mid

    def action(self, act: str, args: dict) -> list[Animation]:
        t = self.theme
        if act == "highlight":
            lines = args["lines"] if isinstance(args["lines"], list) else [args["lines"]]
            color = t.color(args.get("role"), t.HL)
            out = []
            for i in lines:
                if i in self.marks:
                    out.append(fade_out(self.marks.pop(i), 0.3))
                bar = PathNode(path=rect(self.x1 - self.x0 - 0.16, self.pitch * 0.96),
                               style=Style(fill=color, fill_opacity=0.22, opacity=0.0))
                bar.move_to([(self.x0 + self.x1) / 2, self.line_y(i)])
                self.bars.add(bar)
                self.marks[i] = bar
                out.append(fade_in(bar, 0.4))
            return out
        if act == "unhighlight":
            lines = args.get("lines", list(self.marks))
            lines = lines if isinstance(lines, list) else [lines]
            return [fade_out(self.marks.pop(i), 0.3) for i in lines if i in self.marks]
        if act == "point":
            i, color = args["line"], t.color(args.get("role"), t.HL)
            if self.pointer is None:
                tri = polygon([(-0.13, 0.11), (0.13, 0.0), (-0.13, -0.11)])
                self.pointer = PathNode(path=tri, style=Style(fill=color, opacity=0.0))
                self.pointer.move_to([self.x0 - 0.22, self.line_y(i)])
                self.over.add(self.pointer)
                self.pointer_line = i
                return [fade_in(self.pointer, 0.3)]
            delta = self.line_y(i) - self.line_y(self.pointer_line)
            self.pointer_line = i
            return [FadeTo(self.pointer, 1.0, 0.2), Shift(self.pointer, [0.0, delta], 0.4)]
        if act == "unpoint":
            return [fade_out(self.pointer, 0.3)] if self.pointer is not None else []
        if act == "jump":
            y0, y1 = self.line_y(args["from"]), self.line_y(args["to"])
            x = self.x0 - 0.12
            bulge = self.bulge(abs(args["from"] - args["to"]))
            path = Path().move_to(x, y0).cubic_to(x - bulge, y0, x - bulge, y1, x, y1)
            path.extend(chevron([x, y1], [1.0, 0.0], 0.16))
            arc = PathNode(path=path, style=Style(stroke=t.color(args.get("role"), t.NO), stroke_width=4, opacity=0.0))
            self.over.add(arc)
            self.jumps.append(arc)
            return [_DrawIn(arc, 0.8)]
        if act == "unjump":
            jumps, self.jumps = self.jumps, []
            return [fade_out(j, 0.3) for j in jumps]
        return super().action(act, args)


class _DrawIn(Draw):
    def begin(self) -> None:
        self.node.set_style(opacity=1.0)
        super().begin()


# ---------- 终端 ----------

class Terminal(CSWidget):
    name = "terminal"
    SIZE = 22
    PITCH = 1.45

    def text_color(self, role: str | None) -> str:
        t = self.theme
        if role in (None, "FG"):
            return blend(t.BG, t.FG, 0.92)
        if role == "HL":
            return t.MARK
        if role == "DIM":
            return blend(t.DIM, t.BG, 0.7)
        return blend(t.color(role), WHITE, 0.55)

    def build(self) -> Built:
        t, d = self.theme, self.definition
        size = self.SIZE
        cw, pitch = char_width(size), em(size, mono=True) * self.PITCH
        rows: list[Node] = []
        ascent = descent = 0.0
        longest = 0
        for i, row in enumerate(d["lines"]):
            text, role = (row, None) if isinstance(row, str) else (row["text"], row.get("role"))
            indent = len(text) - len(text.lstrip(" "))
            longest = max(longest, len(text.rstrip()))
            tx = mtext(text.strip() or " ", size, self.text_color(role), mono=True)
            ascent, descent = max(ascent, tx.ascent), max(descent, tx.descent)
            tx.shift([indent * cw, -i * pitch])
            rows.append(tx)
        width = max(d.get("cols", 0), longest) * cw + 0.7
        top, bottom = ascent + 0.3, -(len(rows) - 1) * pitch - descent - 0.3
        bg = PathNode(path=rounded_rect(width, top - bottom, 0.12), style=Style(fill=blend(t.FG, t.BG, 0.97)))
        bg.move_to([width / 2 - 0.35, (top + bottom) / 2])
        return Built(Group(bg, *rows), items=rows)


# ---------- 调用栈 ----------

class CallStack(CSWidget):
    name = "call_stack"
    SIZE = 26
    PITCH = 0.95  # 相邻两帧中心的距离
    NOTE = 22  # 状态标签的字号

    def build(self) -> Built:
        t, d = self.theme, self.definition
        self.cw = char_width(self.SIZE)
        self.fw = d.get("cols", 17) * self.cw + 0.8
        self.fh = em(self.SIZE, mono=True) + 0.42
        self.depth = d["depth"]
        label = mtext(d.get("base", "global frame"), 24, t.DIM)
        base = self._box(label, t.DIM, 2.0)
        base.move_to([0.0, 0.0])
        self.base = base
        nodes: list[Node] = [base]
        if "label" in d:
            caption = mtext(d["label"], 24, t.DIM)
            caption.move_to([0.0, -self.fh / 2 - 0.18 - caption.height / 2])
            nodes.append(caption)
        top = self.slot(self.depth) + self.fh / 2
        right = self.fw / 2 + 0.2 + d.get("note_cols", 16) * em(self.NOTE) * 0.5  # 帧右边留给状态标签
        nodes.insert(0, reserve(right + self.fw / 2, top + self.fh / 2, (right - self.fw / 2) / 2, (top - self.fh / 2) / 2))
        self.group = Group(*nodes)
        self.frames: list[Node] = []
        self.values: list[Node] = []
        self.notes: list[Node | None] = []
        self.base_note: Node | None = None
        return Built(self.group, parts={"frame": self.frames, "value": self.values, "note": [], "base": [base]})

    def slot(self, level: int) -> float:
        """level 0 是底帧，1 是第一个压上去的帧。"""
        return level * self.PITCH

    def _box(self, content: Node, color: str, stroke: float = 2.5) -> Group:
        bg = PathNode(path=rounded_rect(self.fw, self.fh, 0.08),
                      style=Style(fill=color, fill_opacity=0.08, stroke=color, stroke_width=stroke))
        content.move_to([0.0, 0.0])
        return Group(bg, content)

    def action(self, act: str, args: dict) -> list[Animation]:
        t = self.theme
        if act == "push":
            color = t.color(args.get("role"), t.A)
            head = mtext(args["name"], self.SIZE, color, "bold", mono=True)
            parts: list[Node] = [head]
            value: Node | None = None
            for k, v in (args.get("vars") or {}).items():
                name = mtext(f"{k} = ", self.SIZE, t.FG, mono=True)
                val = mtext(str(v), self.SIZE, t.FG, mono=True)
                val.shift([name.advance, 0.0])
                parts.append(Group(name, val))
                value = value or val
            arrange(parts, RIGHT, gap=0.5)
            frame = self._box(Group(*parts), color)
            frame.move_to([0.0, self.slot(len(self.frames) + 1)])
            frame.set_style(opacity=0.0)
            self.group.add(frame)
            self.frames.append(frame)
            self.values.append(value or head)
            self.notes.append(None)
            return [fade_in(frame, 0.6, shift=(0.0, -0.3))]
        if act == "pop":
            frame = self.frames.pop()
            self.values.pop()
            note = self.notes.pop()
            out = [fade_out(frame, 0.5, shift=(0.0, 0.3))]
            if note is not None:
                out.append(fade_out(note, 0.4))
            self._sync_notes()
            return out
        if act == "note":
            where = args.get("frame", len(self.frames) - 1)
            host = self.base if where == "base" else self.frames[where]
            old = self.base_note if where == "base" else self.notes[where]
            color = t.color(args.get("role"), t.DIM)
            mono = bool(args.get("mono"))
            size = 24 if mono else self.NOTE
            tag = mtext(args["text"], size, color, "bold" if mono or args.get("role") else "normal", mono=mono)
            y = host.bbox().center[1]
            tag.shift([self.fw / 2 + 0.2 - tag.bbox().x0, y - tag.bbox().center[1]])
            tag.set_style(opacity=0.0)
            self.group.add(tag)
            if where == "base":
                self.base_note = tag
            else:
                self.notes[where] = tag
            self._sync_notes()
            out = [fade_in(tag, 0.35)]
            if old is not None:
                out.insert(0, fade_out(old, 0.3))
            return out
        if act == "unnote":
            if args.get("frame") == "base":
                old, self.base_note = self.base_note, None
                return [fade_out(old, 0.3)] if old is not None else []
            frames = [args["frame"]] if "frame" in args else range(len(self.notes))
            out = []
            for i in frames:
                if self.notes[i] is not None:
                    out.append(fade_out(self.notes[i], 0.3))
                    self.notes[i] = None
            self._sync_notes()
            return out
        return super().action(act, args)

    def _sync_notes(self) -> None:
        self.built.parts["note"] = [n for n in self.notes if n is not None]


# ---------- 名字 → 对象 ----------

class Bindings(CSWidget):
    name = "bindings"
    SIZE = 28
    ROW = 0.95

    def build(self) -> Built:
        t, d = self.theme, self.definition
        self.names = d["names"]
        self.object_ids = list(d["objects"])
        bind = d.get("bind") or {}
        labels = []
        for i, n in enumerate(self.names):
            lab = mtext(n, self.SIZE, t.FG, "bold", mono=True)
            lab.shift([-lab.bbox().x1, -i * self.ROW - lab.bbox().center[1]])
            labels.append(lab)
        self.col = 1.4
        self.boxes: dict[str, Group] = {}
        objects: list[Node] = []
        for k, oid in enumerate(self.object_ids):
            box = self._object(d["objects"][oid])
            box.shift([self.col - box.bbox().x0, -k * self.ROW - box.bbox().center[1]])
            if oid not in bind.values():
                box.set_style(opacity=0.0)
            self.boxes[oid] = box
            objects.append(box)
        self.bind = dict(bind)
        self.arrows: dict[str, PathNode] = {}
        arrows = []
        for i, n in enumerate(self.names):
            a = PathNode(path=self._arrow(i, bind.get(n)), style=Style(stroke=t.DIM, stroke_width=3,
                                                                      opacity=1.0 if n in bind else 0.0))
            self.arrows[n] = a
            arrows.append(a)
        self.labels = labels
        return Built(Group(*objects, *arrows, *labels),
                     parts={"name": labels, "object": objects, "arrow": arrows})

    def _object(self, value) -> Group:
        t = self.theme
        val = mtext(str(value), self.SIZE, t.A, "bold", mono=True)
        w, h = max(0.9, val.bbox().width + 0.5), em(self.SIZE, mono=True) + 0.45
        bg = PathNode(path=rounded_rect(w, h, 0.1), style=Style(fill=blend(WHITE, t.BG, 0.8), stroke=t.A, stroke_width=2.5))
        val.shift(bg.center() - val.bbox().center)
        return Group(bg, val)

    def _arrow(self, i: int, oid: str | None) -> Path:
        y0 = -i * self.ROW
        start = [0.15, y0]
        if oid is None:
            return arrow_path(start, [self.col - 0.12, y0])
        box = self.boxes[oid].bbox()
        return arrow_path(start, [box.x0 - 0.12, box.center[1]])

    def action(self, act: str, args: dict) -> list[Animation]:
        if act == "rebind":
            name, oid = args["name"], args["to"]
            arrow = self.arrows[name]
            target = PathNode(path=self._arrow(self.names.index(name), oid))
            out: list[Animation] = []
            if oid not in self.bind.values():
                out.append(FadeTo(self.boxes[oid], 1.0, 0.4))
            if name in self.bind:
                out.append(_Reshape(arrow, target.path, 0.6))
            else:
                arrow.path = target.path
                out.append(FadeTo(arrow, 1.0, 0.4))
            self.bind[name] = oid
            return out
        if act == "update":
            box = self.boxes[args["object"]]
            old = box.children[1]
            new = self._object(args["value"])
            new.shift([box.bbox().x0 - new.bbox().x0, box.bbox().center[1] - new.bbox().center[1]])
            text = new.children[1]
            text.set_style(opacity=0.0)
            box.add(text)
            out: list[Animation] = [fade_out(old, 0.3), fade_in(text, 0.4)]
            out.append(_Reshape(box.children[0], new.children[0].path.transformed(
                np.linalg.inv(box.children[0].matrix) @ new.children[0].matrix), 0.4))
            return out
        return super().action(act, args)


class _Reshape(Animation):
    """路径变成另一条（点数不同也行，按 moxian 的对齐插值）。"""

    def __init__(self, node: PathNode, target: Path, duration: float = 0.6):
        super().__init__(node, duration)
        self.target = target

    def begin(self) -> None:
        super().begin()
        from moxian.path import align

        self.src, self.dst = align(self.node.path, self.target)

    def interpolate(self, alpha: float) -> None:
        from moxian.path import lerp

        self.node.path = lerp(self.src, self.dst, alpha)

    def finish(self) -> None:
        self.node.path = self.target


# ---------- 卡片、胶囊、要点 ----------

CARD_TITLE, CARD_BODY = 30, 26


def card_content(t, d: dict) -> tuple[Group, dict[str, Node], str]:
    """卡片里的字：标题（角色色、粗体）和正文（多行，可混排片段；mono 时用代码字体）。"""
    color = t.ink(d["role"]) if d.get("role") else t.A
    head = mtext(d["title"], CARD_TITLE, color, "bold")
    rows: list[Node] = [head]
    spans: dict[str, Node] = {"title": head}
    body = d.get("body")
    if body:
        lines = body.split("\n") if isinstance(body, str) else body
        texts = [mtext(s, CARD_BODY, t.FG, mono=True) if d.get("mono") else rich(s, t, CARD_BODY).node for s in lines]
        arrange(texts, DOWN, gap=0.16, align=LEFT)
        block = Group(*texts)
        rows.append(block)
        spans["body"] = block
    arrange(rows, DOWN, gap=0.22, align=LEFT)
    return Group(*rows), spans, color


def card_size(content: Node, cols: int = 0, rows: int = 0) -> tuple[float, float]:
    box = content.bbox()
    w = max(box.width + 0.85, cols * em(CARD_BODY) * 0.5 + 0.85)
    h = max(box.height + 0.55, rows * em(CARD_BODY) * 1.35 + em(CARD_TITLE) * 1.3 + 0.55)
    return w, h


def card_frame(t, content: Node, color: str, w: float, h: float) -> Group:
    """浅底圆角卡片 + 左边的彩色竖条，内容靠左上。"""
    box = content.bbox()
    bg = PathNode(path=rounded_rect(w, h, 0.14), style=Style(fill=t.CARD))
    bar = PathNode(path=rect(0.08, h - 0.24), style=Style(fill=color))
    bg.move_to([box.x0 - 0.38 + w / 2, box.y1 + 0.27 - h / 2])
    bar.move_to([bg.bbox().x0 + 0.14, bg.center()[1]])
    return Group(bg, bar, content)


class Card(CSWidget):
    name = "card"

    def build(self) -> Built:
        d = self.definition
        content, spans, color = card_content(self.theme, d)
        w, h = card_size(content, d.get("cols", 0), d.get("rows", 0))
        return Built(card_frame(self.theme, content, color, w, h), spans=spans)


class Cards(CSWidget):
    """一排同样大小的卡片；每张下面可以有一行小字（note），一开始藏着，show obj.note[i] 时出现。"""

    name = "cards"
    GAP = 0.3

    def build(self) -> Built:
        t, d = self.theme, self.definition
        built = [card_content(t, item) for item in d["items"]]
        sizes = [card_size(c, d.get("cols", 0), d.get("rows", 0)) for c, _, _ in built]
        w, h = max(s[0] for s in sizes), max(s[1] for s in sizes)
        cards = [card_frame(t, c, color, w, h) for c, _, color in built]
        arrange(cards, RIGHT, gap=self.GAP)
        notes: list[Node] = []
        for card, item in zip(cards, d["items"]):
            note = rich(item["note"], t, 24, color=t.DIM).node if item.get("note") else Group()
            if item.get("note"):
                note.shift([card.bbox().x0 + 0.1 - note.bbox().x0, card.bbox().y0 - 0.3 - note.bbox().y1])
                note.set_style(opacity=0.0)
            notes.append(note)
        return Built(Group(*cards, *[n for n in notes if n.children]), parts={"note": notes}, items=cards)


class Pill(CSWidget):
    name = "pill"
    SIZE = 24

    def build(self) -> Built:
        t, d = self.theme, self.definition
        color = t.color(d.get("role"), t.A)
        filled = d.get("filled", True)
        label = mtext(d["text"], self.SIZE, t.BG if filled else color, "bold" if filled else "normal",
                      mono=bool(d.get("mono")))
        b = label.bbox()
        h = em(self.SIZE, mono=bool(d.get("mono"))) * 0.75 + 0.3
        style = Style(fill=color) if filled else Style(stroke=color, stroke_width=2)
        bg = PathNode(path=rounded_rect(b.width + 0.44, h, min(0.2, h / 2)), style=style)
        bg.move_to(b.center)
        return Built(Group(bg, label))


def number_tag(n: int, color: str, ink: str, size: float = 24) -> Group:
    dot = PathNode(path=circle(0.25), style=Style(fill=color))
    num = mtext(str(n), size, ink, "bold")
    num.shift(-num.bbox().center)
    return Group(dot, num)


class Bullets(CSWidget):
    name = "bullets"
    SIZE = 30
    GAP = 0.34

    def build(self) -> Built:
        t, d = self.theme, self.definition
        color = t.color(d.get("role"), t.A)
        rows: list[Node] = []
        for i, item in enumerate(d["items"], 1):
            tag = number_tag(i, color, t.BG)
            text = rich(item, t, self.SIZE).node
            text.shift([0.25 + 0.26 - text.bbox().x0, -text.bbox().center[1]])
            rows.append(Group(tag, text))
        arrange(rows, DOWN, gap=self.GAP, align=LEFT)
        return Built(Group(*rows), items=rows)


# ---------- 流程图 ----------

class Flow(CSWidget):
    name = "flow"
    SIZE = 22
    COL_GAP, ROW_GAP = 1.0, 0.45

    def build(self) -> Built:
        t, d = self.theme, self.definition
        labels, shapes = {}, {}
        for nid, spec in d["nodes"].items():
            role = spec.get("role")
            shape = spec.get("shape", "box")
            ink = t.BG if shape == "pill" else t.ink(role) if role else t.A
            labels[nid] = rich(spec["text"], t, self.SIZE, color=ink).node
            shapes[nid] = shape
        w = max(n.bbox().width for n in labels.values()) + 0.6
        h = em(self.SIZE) + 0.3
        rows = max(s["row"] for s in d["nodes"].values()) + 1
        self.cell_w, self.cell_h = w + self.COL_GAP, h + self.ROW_GAP + (0.3 if "diamond" in shapes.values() else 0)
        nodes: dict[str, Group] = {}
        self.boxes = {}
        for nid, spec in d["nodes"].items():
            color = t.color(spec.get("role"), t.A)
            cx, cy = spec["col"] * self.cell_w, (rows - 1 - spec["row"]) * self.cell_h
            if shapes[nid] == "diamond":
                dw, dh = labels[nid].bbox().width + 1.2, h + 0.4
                path = polygon([(-dw / 2, 0), (0, dh / 2), (dw / 2, 0), (0, -dh / 2)])
                style = Style(fill=color, fill_opacity=0.08, stroke=color, stroke_width=2.5)
            elif shapes[nid] == "pill":
                path = rounded_rect(w, h, h / 2)
                style = Style(fill=color)
            else:
                path = rounded_rect(w, h, 0.1)
                style = Style(fill=color, fill_opacity=0.1, stroke=color, stroke_width=2.5)
            bg = PathNode(path=path, style=style)
            bg.move_to([cx, cy])
            lab = labels[nid]
            lab.shift(np.array([cx, cy]) - lab.bbox().center)
            nodes[nid] = Group(bg, lab)
            self.boxes[nid] = bg.bbox()
        edges: list[Node] = []
        extra: list[Node] = []
        for e in d.get("edges") or []:
            path, mid = self._route(self.boxes[e["from"]], self.boxes[e["to"]], e.get("via"))
            edges.append(PathNode(path=path, style=Style(stroke=t.DIM, stroke_width=3)))
            if "label" in e:
                lab = mtext(e["label"], 20, t.DIM)
                lab.shift(np.asarray(mid) + [0.12, 0.12] - [lab.bbox().x0, lab.bbox().y0])
                extra.append(lab)
        return Built(Group(*edges, *nodes.values(), *extra), spans=dict(nodes), parts={"edge": edges},
                     items=list(nodes.values()))

    @staticmethod
    def _route(a, b, via: str | None) -> tuple[Path, list[float]]:
        gap = 0.05
        if via:
            off = 0.6
            if via in ("left", "right"):
                sx = (min(a.x0, b.x0) - off) if via == "left" else (max(a.x1, b.x1) + off)
                p0 = [a.x0 - gap, a.center[1]] if via == "left" else [a.x1 + gap, a.center[1]]
                p3 = [b.x0 - gap, b.center[1]] if via == "left" else [b.x1 + gap, b.center[1]]
                pts = [p0, [sx, p0[1]], [sx, p3[1]], p3]
            else:
                sy = (max(a.y1, b.y1) + off) if via == "up" else (min(a.y0, b.y0) - off)
                p0 = [a.center[0], a.y1 + gap] if via == "up" else [a.center[0], a.y0 - gap]
                p3 = [b.center[0], b.y1 + gap] if via == "up" else [b.center[0], b.y0 - gap]
                pts = [p0, [p0[0], sy], [p3[0], sy], p3]
        elif abs(a.center[1] - b.center[1]) < 1e-6:  # 同一行：从侧边到侧边
            left_to_right = a.center[0] < b.center[0]
            pts = [[a.x1 + gap, a.center[1]], [b.x0 - gap, b.center[1]]] if left_to_right else \
                [[a.x0 - gap, a.center[1]], [b.x1 + gap, b.center[1]]]
        elif abs(a.center[0] - b.center[0]) < 1e-6:  # 同一列：上下
            down = a.center[1] > b.center[1]
            pts = [[a.center[0], a.y0 - gap], [b.center[0], b.y1 + gap]] if down else \
                [[a.center[0], a.y1 + gap], [b.center[0], b.y0 - gap]]
        else:  # 先横着出去，再竖着进目标的上下边
            down = a.center[1] > b.center[1]
            p0 = [a.x1 + gap, a.center[1]] if b.center[0] > a.center[0] else [a.x0 - gap, a.center[1]]
            p2 = [b.center[0], b.y1 + gap] if down else [b.center[0], b.y0 - gap]
            pts = [p0, [p2[0], p0[1]], p2]
        path = polyline(pts)
        path.extend(chevron(pts[-1], np.subtract(pts[-1], pts[-2]), 0.15))
        mid = list((np.asarray(pts[0]) + np.asarray(pts[1])) / 2)
        return path, mid


# ---------- 图片 ----------

class Image(CSWidget):
    name = "image"
    HEIGHT = 4.0

    def build(self) -> Built:
        base = FsPath(self.opts.get("base_dir", "."))
        node = ImageNode.from_file(str(base / self.definition["src"]), height=self.HEIGHT)
        return Built(Group(node))


# ---------- 排队的人 ----------

class Queue(CSWidget):
    name = "queue"
    STEP = 1.31  # 相邻两人中心的距离

    def build(self) -> Built:
        t, d = self.theme, self.definition
        n = d["people"]
        self.you = d.get("you", n - 1)
        people: list[Node] = []
        for i in range(n):  # i = 0 是队首（最右边）
            color = t.A if i == self.you else t.DIM
            body = PathNode(path=rounded_rect(0.56, 0.72, 0.2), style=Style(fill=color))
            head = PathNode(path=circle(0.2), style=Style(fill=color))
            head.move_to([0.0, 0.36 + 0.06 + 0.2])
            p = Group(body, head)
            p.shift([-i * self.STEP, 0.0])
            people.append(p)
        self.people = people
        you = mtext("you", 26, t.A, "bold")
        you.shift([-self.you * self.STEP - you.bbox().center[0], -0.36 - 0.25 - you.bbox().y1])
        nodes: list[Node] = [*people, you]
        top = 0.82 + 1.0
        if "label" in d:
            stage = PathNode(path=rect(1.3, 2.6), style=Style(fill=t.A, fill_opacity=0.15, stroke=t.A, stroke_width=2))
            stage.move_to([0.6 + 0.65 + 0.6, 0.5])
            word = mtext(d["label"], 24, t.A, "bold")
            word.shift(-word.bbox().center)
            word.matrix = T.translate(*stage.center()) @ T.rotate(np.pi / 2) @ word.matrix
            nodes += [stage, word]
        left, right = -(n - 1) * self.STEP - 0.5, (2.5 if "label" in d else 0.5)
        nodes.insert(0, reserve(right - left, top + 1.0, (left + right) / 2, (top - 1.0) / 2))
        self.group = Group(*nodes)
        self.tags: list[Node] = []
        return Built(self.group, parts={"person": people, "you": [people[self.you]]})

    def above(self, i: int, lift: float = 0.0) -> np.ndarray:
        return np.array([-i * self.STEP, 0.82 + 0.3 + lift])

    def action(self, act: str, args: dict) -> list[Animation]:
        t = self.theme
        if act == "ask":
            bg = PathNode(path=rounded_rect(0.9, 0.5, 0.18), style=Style(fill=t.MARK, stroke=t.HL, stroke_width=2))
            mark = mtext("#?", 22, t.HL_INK, "bold")
            mark.shift(-mark.bbox().center)
            bubble = Group(bg, mark)
            bubble.move_to(self.above(self.you))
            bubble.set_style(opacity=0.0)
            self.group.add(bubble)
            steps: list[list[Animation]] = [[fade_in(bubble, 0.3)]]
            for i in range(self.you - 1, -1, -1):
                steps.append([Travel(bubble, straight(self.above(i + 1), self.above(i)), 0.35)])
            steps.append([fade_out(bubble, 0.2)])
            return [Steps(steps, 0.35 * len(steps))]
        if act == "number":
            steps = []
            for i in range(self.you + 1):
                tag = number_tag(i + 1, t.C if i == self.you else t.A, t.BG)
                tag.move_to(self.above(i))
                tag.set_style(opacity=0.0)
                self.group.add(tag)
                self.tags.append(tag)
                steps.append([fade_in(tag, 0.45)])
            return [Steps(steps, 0.45 * len(steps))]
        return super().action(act, args)


REGISTRY = {
    "heading": Heading, "code": Code, "terminal": Terminal, "call_stack": CallStack, "bindings": Bindings,
    "card": Card, "cards": Cards, "pill": Pill, "bullets": Bullets, "flow": Flow, "image": Image, "queue": Queue,
}
