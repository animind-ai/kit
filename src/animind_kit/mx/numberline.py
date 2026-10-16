"""数轴（墨线实现）：分数除法的"里面有几个"。

一条从 start 到 end 的数轴（整数端点），每个单位平均分成 parts 份。除法 a ÷ d 在这里就是"从 0 开始，
每次跳 d 这么长，跳几次能到 a"：

- jump：从 0 起画一串等长的跳跃弧，弧顶上标 1、2、3…，可以一个个数；
- remainder：跳不完整时（例如 6 ÷ 4/5 跳七次到 28/5，还剩 2/5），画出最后那一小段，并虚画一整跳作对照，
  标出"剩下的是一跳的几分之几"（2/5 ÷ 4/5 = 1/2），而不是按单位 1 去读它；
- subdivide：把每个单位重新等分（三等分 → 六等分），两种分法共有的刻度不动，其余刻度就近滑过去或长出来 / 缩回去；
- highlight：在轴上垫一段长度（例如 0 到 2/3）；
- mark_point：在某个数上点一个点，可以带标注；
- erase：收起跳跃、剩余段、高亮和点，换下一个除数之前用。

所有位置都用 fractions.Fraction 做精确运算（跳几次、剩多少、刻度落在哪），只在画图时才换成浮点。

暴露的子元素（scene-spec 里登记的长度和这里一致）：
- tick / label：整数刻度和它们的数字（end - start + 1 个）；
- unit：每个单位一格（里面是这一格的细分刻度，外加一个看不见的占位框，所以随时可以 focus / mark）；
- frac：第一格细分刻度的分数标注（fractions: true 时才有内容），一项；
- span：高亮段，一项；
- jump / count：跳跃弧和弧顶的计数，长度是 measures 里最多能跳的整跳数，按当前这次 jump 依次填；
- rest：剩余段（虚画的整跳 + 剩下那一小段 + 它的分数），一项；
- point：mark_point 点出来的点，最多 POINTS 个。
"""

from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
from moxian.anim import Animation, Draw, Fade, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import Path, circle, line, rect

from .text import mmath
from .widgets import Built, Widget

POINTS = 4

Number = int | str | Fraction


def frac(value: Number) -> Fraction:
    """"2/3"、"2"、2、Fraction → Fraction；不接受小数（0.1 这类值在二进制里不精确）。"""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"数轴上的数要写成整数或分数字符串（例如 \"2/3\"），不要写小数：{value!r}")
    return Fraction(str(value).replace(" ", ""))


def tex(value: Fraction) -> str:
    """分数的 TeX：整数直接写，真分数写 \\frac，假分数写成带分数（7½ 写 7\\frac{1}{2}）。"""
    if value.denominator == 1:
        return str(value.numerator)
    sign = "-" if value < 0 else ""
    v = abs(value)
    whole, rest = divmod(v.numerator, v.denominator)
    body = rf"\frac{{{rest}}}{{{v.denominator}}}"
    return f"{sign}{whole}{body}" if whole else f"{sign}{body}"


# ---------- 纯算术（测试直接用这些函数）----------

def ticks(start: int, end: int, parts: int) -> list[Fraction]:
    """所有刻度的位置（含整数刻度），从小到大。"""
    return [start + Fraction(k, parts) for k in range((end - start) * parts + 1)]


def jumps(start: int, end: int, length: Fraction) -> tuple[list[tuple[Fraction, Fraction]], Fraction]:
    """从 start 起每次跳 length：返回整跳的 [(起点, 终点)] 和剩下的长度（0 表示正好跳完）。"""
    if length <= 0:
        raise ValueError("跳的长度要大于 0")
    total = Fraction(end - start)
    n = math.floor(total / length)
    return [(start + i * length, start + (i + 1) * length) for i in range(n)], total - n * length


def max_jumps(start: int, end: int, measures: list[Number]) -> int:
    return max((math.floor(Fraction(end - start) / frac(m)) for m in measures), default=0)


def morph_plan(old: list[Fraction], new: list[Fraction]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """一个单位里的细分刻度从 old 换成 new（都不含两端的整数刻度）。

    返回 (配对 [(旧下标, 新下标)], 只在旧里的下标, 只在新里的下标)：
    位置相同的先配上（不动）；剩下的旧刻度按顺序就近配给还空着的新刻度（滑过去）；
    多出来的新刻度长出来，多出来的旧刻度缩回去。配对保持左右顺序，刻度滑动时不会交叉。
    """
    pairs = [(i, new.index(p)) for i, p in enumerate(old) if p in new]
    used_old = {i for i, _ in pairs}
    used_new = {j for _, j in pairs}
    free_old = [i for i in range(len(old)) if i not in used_old]
    free_new = [j for j in range(len(new)) if j not in used_new]
    # 在两个已配好的刻度之间的空隙里，按顺序一一配对（不会越过已经不动的刻度）
    anchors = sorted(pairs)
    bounds = [(-1, -1)] + anchors + [(len(old), len(new))]
    for (i0, j0), (i1, j1) in zip(bounds, bounds[1:]):
        olds = [i for i in free_old if i0 < i < i1]
        news = [j for j in free_new if j0 < j < j1]
        if not olds or not news:
            continue
        # 少的一边逐个找最近的
        if len(olds) <= len(news):
            taken: list[int] = []
            for i in olds:
                j = min((j for j in news if j not in taken and (not taken or j > taken[-1])),
                        key=lambda j: abs(new[j] - old[i]), default=None)
                if j is not None:
                    taken.append(j)
                    pairs.append((i, j))
        else:
            taken = []
            for j in news:
                i = min((i for i in olds if i not in taken and (not taken or i > taken[-1])),
                        key=lambda i: abs(new[j] - old[i]), default=None)
                if i is not None:
                    taken.append(i)
                    pairs.append((i, j))
    pairs.sort()
    gone = [i for i in range(len(old)) if i not in {a for a, _ in pairs}]
    born = [j for j in range(len(new)) if j not in {b for _, b in pairs}]
    return pairs, gone, born


# ---------- 动画 ----------

class _Sequence(Animation):
    """几步依次播放（相邻两步略有重叠）；extra 里是 (开始, 结束, 动画)，按整段时长的比例放。"""

    OVERLAP = 0.2

    def __init__(self, steps: list[list[Animation]], extra: list[tuple[float, float, Animation]] | None = None,
                 head: float = 0.0, duration: float = 2.0):
        super().__init__(Node(), duration, easing.linear)
        steps = [s for s in steps if s]
        n = len(steps)
        span = 1.0 - head
        width = span / (n - (n - 1) * self.OVERLAP) if n else span
        self.windows = [(head + k * width * (1 - self.OVERLAP), head + k * width * (1 - self.OVERLAP) + width, a)
                        for k, step in enumerate(steps) for a in step]
        self.windows += extra or []
        self.started: set[int] = set()

    def interpolate(self, alpha: float) -> None:
        for k, (start, end, a) in enumerate(self.windows):
            if alpha < start:
                continue
            if k not in self.started:
                a.begin()
                self.started.add(k)
            local = min(1.0, (alpha - start) / max(1e-6, end - start))
            a.interpolate(a.ease(local))

    def finish(self) -> None:
        self.interpolate(1.0)


class _Late(Animation):
    """在所属窗口的 [start, 1] 这一段才播放里面的动画（箭头等弧画到落点再出现）。"""

    def __init__(self, anim: Animation, start: float = 0.75):
        super().__init__(anim.node, anim.duration, easing.linear)
        self.anim, self.start, self.begun_inner = anim, start, False

    def interpolate(self, alpha: float) -> None:
        if alpha < self.start:
            return
        if not self.begun_inner:
            self.anim.begin()
            self.begun_inner = True
        local = (alpha - self.start) / max(1e-6, 1 - self.start)
        self.anim.interpolate(self.anim.ease(min(1.0, local)))

    def finish(self) -> None:
        self.interpolate(1.0)


class _FadeTo(Fade):
    """从当前透明度渐变到目标值。"""

    def __init__(self, node: Node, end: float, duration: float = 0.4):
        super().__init__(node, 0.0, end, duration)

    def begin(self) -> None:
        super().begin()
        self.start = (self.node.style or Style()).opacity


class _Bar(Animation):
    """高亮段从当前范围滑到新范围（两端各自线性移动），同时淡到可见。"""

    def __init__(self, bar: PathNode, x0: float, x1: float, height: float, duration: float = 0.6):
        super().__init__(bar, duration, easing.smooth)
        self.x0, self.x1, self.h = x0, x1, height

    def begin(self) -> None:
        super().begin()
        b = self.node.path.bbox()
        visible = (self.node.style or Style()).opacity > 0.01 and b is not None and b[2] - b[0] > 1e-6
        self.from_ = (b[0], b[2]) if visible else (self.x0, self.x0)
        self.op = (self.node.style or Style()).opacity

    def interpolate(self, alpha: float) -> None:
        a = self.from_[0] + (self.x0 - self.from_[0]) * alpha
        b = self.from_[1] + (self.x1 - self.from_[1]) * alpha
        self.node.path = rect(max(b - a, 1e-4), self.h).transformed(T.translate((a + b) / 2, 0.0))
        self.node.set_style(opacity=self.op + (1.0 - self.op) * alpha)


class _Retick(Animation):
    """重新细分：配对的刻度从旧位置滑到新位置，新长出来的刻度从轴上长高，旧的多余刻度缩回去。

    新刻度节点在动作调用时就已经建好、放在终点、透明度 0；begin 时把配对的旧刻度藏起来、新刻度挪到旧位置显示。
    """

    def __init__(self, slides: list[tuple[Node, Node, float]], born: list[Node], gone: list[Node],
                 labels_out: list[Node], labels_in: list[Node], duration: float = 1.2):
        super().__init__(Node(), duration, easing.smooth)
        self.slides, self.born, self.gone = slides, born, gone
        self.labels_out, self.labels_in = labels_out, labels_in

    def begin(self) -> None:
        super().begin()
        self.base = {id(n): n.matrix.copy() for _old, n, _dx in self.slides}
        for old, new, _dx in self.slides:
            old.set_style(opacity=0.0)
            new.set_style(opacity=1.0)
        self.gone_base = {id(n): n.matrix.copy() for n in self.gone}

    def interpolate(self, alpha: float) -> None:
        for _old, new, dx in self.slides:
            new.matrix = T.translate(-dx * (1 - alpha), 0.0) @ self.base[id(new)]
        grow, shrink = max(1e-3, alpha), max(1e-3, 1 - alpha)
        for n in self.born:
            n.matrix = T.scale(1.0, grow)
            n.set_style(opacity=min(1.0, alpha * 2))
        for n in self.gone:
            n.matrix = T.scale(1.0, shrink) @ self.gone_base[id(n)]
            n.set_style(opacity=max(0.0, 1 - alpha * 1.5))
        out = max(0.0, 1 - alpha * 2)
        for n in self.labels_out:
            n.set_style(opacity=out)
        for n in self.labels_in:
            n.set_style(opacity=max(0.0, alpha * 2 - 1))

    def finish(self) -> None:
        self.interpolate(1.0)
        for n in self.born:
            n.matrix = T.identity()


# ---------- 组件 ----------

class NumberLine(Widget):
    name = "number_line"
    UNIT = 1.9  # 一个单位的长度（画面单位，排版前）
    OVERSHOOT = 0.9  # 轴在终点之后多画出去的一段（带箭头）
    MAJOR, MINOR = 0.2, 0.12  # 刻度半高
    LABEL_Y = -0.52
    FRAC_Y = -0.72
    LABEL, FRAC, COUNT = 34, 28, 30
    ARC_BASE = 0.06
    ARC_MAX = 0.85

    def build(self) -> Built:
        d, t = self.definition, self.theme
        start, end = (frac(v) for v in d.get("range", [0, 1]))
        if start.denominator != 1 or end.denominator != 1 or end <= start:
            raise ValueError(f"number_line 的 range 要是两个整数且后者更大（对象 {self.obj_id}）")
        self.start, self.end = int(start), int(end)
        self.n_units = self.end - self.start
        self.parts = int(d.get("parts", 1))
        self.fractions = bool(d.get("fractions", False))
        self.measures = [frac(m) for m in d.get("measures", [])]
        self.ink = t.color(d.get("role"), t.A)
        self.jumps: list[tuple[Fraction, Fraction]] = []
        self.left = Fraction(0)
        self.length: Fraction | None = None
        self.jump_set: Group | None = None
        self.points_used = 0

        node = Group(name=self.obj_id)
        U = self.UNIT
        x_end = self.n_units * U + self.OVERSHOOT
        axis = PathNode(path=line(-0.25, 0.0, x_end, 0.0), style=Style(stroke=t.FG, stroke_width=3))
        tip = PathNode(path=line(x_end - 0.16, 0.1, x_end, 0.0).extend(line(x_end, 0.0, x_end - 0.16, -0.1)),
                       style=Style(stroke=t.FG, stroke_width=3))
        # 看不见的占位框：跳跃弧和计数、分数标注都在里面，排版时整个组件的大小不随动作变化
        top = self.ARC_MAX + self.ARC_BASE + 0.7
        reserve = PathNode(path=rect(x_end + 0.5, top + 1.15).transformed(T.translate((x_end - 0.5) / 2, (top - 1.15) / 2)),
                           style=Style(opacity=0.0))
        self.bar = PathNode(path=rect(1e-4, 0.16), style=Style(fill=t.HL, fill_opacity=0.55, opacity=0.0), z=-1)

        # 整数刻度和数字
        self.major: list[PathNode] = []
        self.labels: list[Node] = []
        for k in range(self.n_units + 1):
            x = k * U
            tick = PathNode(path=line(0.0, -self.MAJOR, 0.0, self.MAJOR), style=Style(stroke=t.FG, stroke_width=3))
            tick.matrix = T.translate(x, 0.0)
            label = mmath(str(self.start + k), self.LABEL, t.FG).move_to([x, self.LABEL_Y])
            self.major.append(tick)
            self.labels.append(label)

        # 每个单位一格：细分刻度 + 占位框
        self.units: list[Group] = []
        self.minor: list[list[PathNode]] = []
        for k in range(self.n_units):
            cell = PathNode(path=rect(U, 2 * self.MAJOR).transformed(T.translate((k + 0.5) * U, 0.0)),
                            style=Style(opacity=0.0))
            unit = Group(cell, name=f"unit{k}")
            ticks_k = [self._minor_tick(k, Fraction(j, self.parts)) for j in range(1, self.parts)]
            unit.add(*ticks_k)
            self.units.append(unit)
            self.minor.append(ticks_k)

        self.frac_group = Group(name="frac")
        self.frac_labels = self._frac_labels(self.parts)
        for lab in self.frac_labels:
            self.frac_group.add(lab)

        self.jump_layer = Group(name="jumps")
        self.rest_group = Group(name="rest")  # 虚画的整跳 + rest_piece
        self.rest_piece = Group(name="rest_piece")  # 剩下那一小段和它的分数（rest[0] 指向它）
        self.point_slots = [Group(name=f"point{i}") for i in range(POINTS)]
        node.add(reserve, self.bar, axis, tip, *self.units, *self.major, *self.labels, self.frac_group,
                 self.jump_layer, self.rest_group, *self.point_slots)

        slots = max_jumps(self.start, self.end, self.measures)
        self.parts_map: dict[str, list[Node]] = {
            "tick": list(self.major),
            "label": list(self.labels),
            "unit": list(self.units),
            "frac": [self.frac_group],
            "span": [self.bar],
            "jump": [Group() for _ in range(slots)],
            "count": [Group() for _ in range(slots)],
            "rest": [self.rest_piece],
            "point": list(self.point_slots),
        }
        return Built(node, parts=self.parts_map)

    # ---- 几何 ----

    def x(self, value: Fraction) -> float:
        return float(value - self.start) * self.UNIT

    def _minor_tick(self, unit: int, offset: Fraction) -> PathNode:
        tick = PathNode(path=line(0.0, -self.MINOR, 0.0, self.MINOR), style=Style(stroke=self.theme.FG, stroke_width=2))
        tick.matrix = T.translate(self.x(self.start + unit + offset), 0.0)
        return tick

    def _frac_labels(self, parts: int) -> list[Node]:
        if not self.fractions or parts < 2:
            return []
        out = []
        for j in range(1, parts):
            lab = mmath(rf"\frac{{{j}}}{{{parts}}}", self.FRAC, self.theme.FG)
            lab.move_to([self.x(self.start + Fraction(j, parts)), self.FRAC_Y])
            out.append(lab)
        return out

    def arc_height(self, length: Fraction) -> float:
        w = float(length) * self.UNIT
        return min(self.ARC_MAX, max(0.3, 0.3 * w))

    def arc(self, a: Fraction, b: Fraction, height: float | None = None) -> Path:
        x0, x1 = self.x(a), self.x(b)
        h = self.arc_height(b - a) if height is None else height
        y = self.ARC_BASE
        c = h * 4 / 3  # 对称三次贝塞尔：控制点高 4h/3 时弧顶正好高 h
        w = x1 - x0
        return Path().move_to(x0, y).cubic_to(x0 + 0.08 * w, y + c, x1 - 0.08 * w, y + c, x1, y)

    def arrow_head(self, a: Fraction, b: Fraction, height: float | None = None) -> Path:
        """弧的落点处的小箭头：沿弧末端的切线方向。"""
        x0, x1 = self.x(a), self.x(b)
        h = self.arc_height(b - a) if height is None else height
        tangent = np.array([0.08 * (x1 - x0), -h * 4 / 3])
        tangent /= np.linalg.norm(tangent)
        tipx, tipy = x1, self.ARC_BASE
        size = 0.16
        out = []
        for sign in (1, -1):
            r = math.radians(28 * sign)
            back = -tangent
            v = np.array([back[0] * math.cos(r) - back[1] * math.sin(r), back[0] * math.sin(r) + back[1] * math.cos(r)])
            out.append(line(tipx, tipy, tipx + size * v[0], tipy + size * v[1]))
        return out[0].extend(out[1])

    # ---- 动作 ----

    def action(self, act: str, args: dict) -> list[Animation]:
        if act == "jump":
            return [self._jump(frac(args["length"]), args.get("count"))]
        if act == "remainder":
            return [self._remainder()]
        if act == "subdivide":
            return [self._subdivide(int(args["parts"]))]
        if act == "highlight":
            return [self._highlight(frac(args.get("from", self.start)), frac(args["to"]))]
        if act == "mark_point":
            return [self._mark_point(frac(args["at"]), args.get("label"))]
        if act == "erase":
            return self._erase()
        raise KeyError(f"组件 {self.name} 没有动作 {act}")

    def _jump(self, length: Fraction, count: int | None) -> Animation:
        whole, left = jumps(self.start, self.end, length)
        if count is not None:
            count = int(count)
            if count > len(whole):
                raise ValueError(f"{self.obj_id}：长度 {length} 最多跳 {len(whole)} 次，不能跳 {count} 次")
            left = Fraction(self.end - self.start) - count * length
            whole = whole[:count]
        slots = len(self.parts_map["jump"])
        if len(whole) > slots:
            raise ValueError(f"{self.obj_id}：跳 {len(whole)} 次超过了预留的 {slots} 个（把 {length} 写进 measures）")
        extra: list[tuple[float, float, Animation]] = []
        head = 0.0
        old = [n for n in (self.jump_set, self.rest_group) if n is not None and (n.style or Style()).opacity > 0]
        if old:
            head = 0.12
            for n in old:
                extra.append((0.0, head, _FadeTo(n, 0.0)))
        t, h = self.theme, self.arc_height(length)
        jump_set = Group(name=f"jumps{len(self.jump_layer.children)}")
        arcs, counts, steps = [], [], []
        for i, (a, b) in enumerate(whole):
            arc = PathNode(path=self.arc(a, b), style=Style(stroke=self.ink, stroke_width=4, opacity=0.0))
            head_node = PathNode(path=self.arrow_head(a, b), style=Style(stroke=self.ink, stroke_width=4, opacity=0.0))
            num = mmath(str(i + 1), self.COUNT, t.ink(self.definition.get("role") or "A"))
            num.move_to([(self.x(a) + self.x(b)) / 2, self.ARC_BASE + h + 0.3])
            num.set_style(opacity=0.0)
            jump = Group(arc, head_node, name=f"jump{i}")
            jump_set.add(jump, num)
            arcs.append(jump)
            counts.append(num)
            steps.append([Draw(arc), _Late(_FadeTo(head_node, 1.0)), _FadeTo(num, 1.0)])
        self.jump_layer.add(jump_set)
        self.jump_set = jump_set
        self.parts_map["jump"][:] = arcs + [Group() for _ in range(slots - len(arcs))]
        self.parts_map["count"][:] = counts + [Group() for _ in range(slots - len(counts))]
        self.jumps, self.left, self.length = whole, left, length
        # 剩余段先清空：上一次的已经在 old 里淡掉了
        return _Sequence(steps, extra, head=head)

    def _remainder(self) -> Animation:
        if self.length is None or self.left <= 0:
            raise ValueError(f"{self.obj_id}：上一次 jump 正好跳完（或还没跳），没有剩余段")
        t = self.theme
        a = self.jumps[-1][1] if self.jumps else Fraction(self.start)
        b = a + self.left
        full = a + self.length
        h = self.arc_height(self.length)
        ghost = PathNode(path=self.arc(a, full, h), style=Style(stroke=t.DIM, stroke_width=3, opacity=0.0))
        piece = PathNode(path=self.arc(a, b, h * 0.75), style=Style(stroke=t.HL, stroke_width=5, opacity=0.0))
        share = self.left / self.length  # 剩下的是一跳的几分之几
        label = mmath(tex(share), self.COUNT, t.HL_INK)
        label.move_to([(self.x(a) + self.x(b)) / 2, self.ARC_BASE + h * 0.75 + 0.42])
        label.set_style(opacity=0.0)
        self.rest_group.children.clear()
        self.rest_piece.children.clear()
        self.rest_piece.add(piece, label)
        self.rest_group.add(ghost, self.rest_piece)
        self.rest_group.set_style(opacity=1.0)
        return _Sequence([[_FadeTo(ghost, 0.7)], [Draw(piece)], [_FadeTo(label, 1.0)]])

    def _subdivide(self, parts: int) -> Animation:
        if parts < 1:
            raise ValueError("subdivide 的 parts 要是正整数")
        slides, born, gone = [], [], []
        for k in range(self.n_units):
            old_pos = [Fraction(j, self.parts) for j in range(1, self.parts)]
            new_pos = [Fraction(j, parts) for j in range(1, parts)]
            pairs, gone_i, born_j = morph_plan(old_pos, new_pos)
            old_nodes = self.minor[k]
            new_nodes: list[PathNode] = []
            for p in new_pos:
                tick = self._minor_tick(k, p)
                tick.set_style(opacity=0.0)
                new_nodes.append(tick)
            for i, j in pairs:
                dx = self.x(self.start + k + new_pos[j]) - self.x(self.start + k + old_pos[i])
                slides.append((old_nodes[i], new_nodes[j], dx))
            for j in born_j:
                # 长出来的刻度：节点矩阵在动画里从 scale(1, 0) 长到 1，路径里放好位置
                n = new_nodes[j]
                n.path = n.path.transformed(n.matrix)
                n.matrix = T.scale(1.0, 1e-3)
                born.append(n)
            gone += [old_nodes[i] for i in gone_i]
            unit = self.units[k]
            unit.add(*new_nodes)
            self.minor[k] = new_nodes
        labels_out = list(self.frac_labels)
        self.frac_labels = self._frac_labels(parts)
        for lab in self.frac_labels:
            lab.set_style(opacity=0.0)
            self.frac_group.add(lab)
        self.parts = parts
        return _Retick(slides, born, gone, labels_out, list(self.frac_labels))

    def _highlight(self, a: Fraction, b: Fraction) -> Animation:
        return _Bar(self.bar, self.x(a), self.x(b), 0.16)

    def _mark_point(self, at: Fraction, label: str | None) -> Animation:
        if self.points_used >= POINTS:
            raise ValueError(f"{self.obj_id}：erase 之前最多点 {POINTS} 个点")
        slot = self.point_slots[self.points_used]
        self.points_used += 1
        slot.children.clear()  # erase 过的旧点（已经淡掉）
        t = self.theme
        dot = PathNode(path=circle(0.09), style=Style(fill=t.HL_INK))
        dot.matrix = T.translate(self.x(at), 0.0)
        slot.add(dot)
        if label:
            lab = mmath(label, self.COUNT, t.HL_INK).move_to([self.x(at), -self.LABEL_Y + 0.05])
            slot.add(lab)
        slot.set_style(opacity=0.0)
        return _FadeTo(slot, 1.0)

    def _erase(self) -> list[Animation]:
        anims: list[Animation] = []
        for n in [self.jump_set, self.rest_group, self.bar, *self.point_slots]:
            if n is not None and (n.style or Style()).opacity > 0:
                anims.append(_FadeTo(n, 0.0))
        self.jump_set = None
        self.points_used = 0
        self.jumps, self.left, self.length = [], Fraction(0), None
        return anims or [_FadeTo(self.bar, 0.0)]
