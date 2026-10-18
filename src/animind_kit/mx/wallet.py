"""钱包和余额条（墨线实现）：负负得正（洞察 #15）、减去一个负数（#16）用的"撕欠条"。

wallet：一个钱包，上面一排是现金（绿色卡片，写"3 元"），下面一排是欠条（红色卡片，写"欠 3 元"）；
右边一根竖着的余额条，从 0 往上（绿）或往下（红）长，顶端写现在一共有多少钱（现金减欠条）。动作：
- put：kind（cash / iou）那一排放进 count 张，每张 value 元（默认组件的 value），从上方落进空位；余额条跟着变；
- take：kind 那一排拿走最后放进去的 count 张：现金往上飘走，欠条从中间撕成两半落下去；余额条跟着变。
  quiet: true 时只是原地淡出（两种情况之间把钱包复原，不演"拿走"）。
参数：value（可选，默认 3）：每张多少元；cash、iou（可选，默认 0）：一开始各有几张；slots（可选，默认 4）：每排几个位置；
range（可选，默认 12）：余额条上下各画到多少元。
暴露：cash / iou（两排卡片，各一项）、balance（余额条和读数，一项）。

颜色、透明度和位置都在动画开始播放时才改：一个 beat 的动作先全部建好再播放，建动作时不能动画面。
"""

from __future__ import annotations

import numpy as np
from moxian.anim import Animation, easing
from moxian.core import Group, Node, PathNode, Style
from moxian.core import transform as T
from moxian.path import Path, line, rect, rounded_rect

from .dots import FadeTo, Steps, Travel, reserve, straight
from .text import mmath, mtext
from .widgets import Built, Widget

W, H = 1.5, 0.8  # 一张卡片
GAP = 0.22
ROW = 0.62  # 两排中心到中线的距离
LABEL = 40
CARD_TEXT = 40
BAR_W = 0.55
BAR_H = 2.4  # 余额条从 0 到 range 的长度
BAR_DX = 1.3  # 余额条离钱包右边多远


def _half(left: bool) -> Path:
    """撕开的半张欠条：外边是直的，撕口是锯齿。"""
    w, h = W / 2, H
    x0 = -w if left else 0.0
    x1 = 0.0 if left else w
    p = Path().move_to(x0, -h / 2)
    teeth = 5
    if left:
        p.line_to(x1, -h / 2)
        for k in range(teeth):
            y = -h / 2 + h * (k + 0.5) / teeth
            p.line_to(x1 + (0.08 if k % 2 == 0 else -0.04), y)
        p.line_to(x1, h / 2).line_to(x0, h / 2)
    else:
        p.line_to(x1, -h / 2).line_to(x1, h / 2).line_to(x0, h / 2)
        for k in reversed(range(teeth)):
            y = -h / 2 + h * (k + 0.5) / teeth
            p.line_to(x0 + (0.08 if k % 2 == 0 else -0.04), y)
    return p.close()


class _Tear(Animation):
    """半张欠条往外、往下落并转一点，最后淡出。"""

    def __init__(self, node: Node, base: np.ndarray, side: float, duration: float = 0.8):
        super().__init__(node, duration, easing.linear)
        self.base, self.side = base, side

    def begin(self) -> None:
        super().begin()
        self.node.set_style(opacity=1.0)

    def interpolate(self, alpha: float) -> None:
        dx = self.side * 0.5 * alpha
        dy = -1.0 * alpha * alpha
        self.node.matrix = T.translate(float(self.base[0] + dx), float(self.base[1] + dy)) @ T.rotate(self.side * 0.5 * alpha)
        self.node.set_style(opacity=max(0.0, 1.0 - max(0.0, alpha - 0.4) / 0.6))


class _Balance(Animation):
    """余额条从当前余额长到 target，旧读数跟着条顶走；结束时换成新读数（新读数在建动作时就建好、藏着）。"""

    def __init__(self, w: Wallet, target: int, new: Node, duration: float = 0.6):
        super().__init__(w.bar, duration, easing.smooth)
        self.w, self.target, self.new = w, target, new

    def begin(self) -> None:
        super().begin()
        self.start = self.w.shown

    def interpolate(self, alpha: float) -> None:
        self.w.draw_bar(self.start + (self.target - self.start) * alpha)

    def finish(self) -> None:
        self.interpolate(1.0)
        if self.w.readout is not None:
            self.w.readout.set_style(opacity=0.0)
        self.new.set_style(opacity=1.0)
        self.w.readout = self.new


class Wallet(Widget):
    name = "wallet"

    def build(self) -> Built:
        d, t = self.definition, self.theme
        self.value = int(d.get("value", 3))
        self.slots = int(d.get("slots", 4))
        self.range = int(d.get("range", 12))
        node = Group(name=self.obj_id)
        self.width = self.slots * (W + GAP) - GAP
        self.unit = BAR_H / self.range
        self.bar_x = self.width + BAR_DX
        label_w = mtext("欠条", LABEL, t.FG).width + 0.3
        top = max(ROW + H / 2 + 0.25, BAR_H + 0.5)
        node.add(reserve(self.bar_x + 2.2 + label_w, 2 * top, (self.bar_x + 2.2 - label_w) / 2, 0.0))
        frame = PathNode(path=rounded_rect(self.width + 0.4, 2 * ROW + H + 0.4, 0.15).transformed(T.translate(self.width / 2 - W / 2, 0.0)),
                         style=Style(stroke=t.FG, stroke_width=3, fill=t.DIM, fill_opacity=0.08))
        node.add(frame)
        for text, y in (("现金", ROW), ("欠条", -ROW)):
            lab = mtext(text, LABEL, t.FG)
            lab.move_to([-W / 2 - 0.35 - lab.width / 2, y])
            node.add(lab)
        self.rows: dict[str, list[tuple[Node, int]]] = {"cash": [], "iou": []}
        self.cards = {"cash": Group(name="cash"), "iou": Group(name="iou")}
        node.add(self.cards["cash"], self.cards["iou"])
        for kind in ("cash", "iou"):
            for _ in range(int(d.get(kind, 0))):
                self._card(kind, self.value, visible=True)
        # 余额条：0 刻度线、竖轴、填充、读数
        bx = self.bar_x
        self.balance = Group(name="balance")
        axis = PathNode(path=line(bx, -BAR_H - 0.15, bx, BAR_H + 0.15), style=Style(stroke=t.DIM, stroke_width=2))
        zero = PathNode(path=line(bx - BAR_W / 2 - 0.15, 0.0, bx + BAR_W / 2 + 0.15, 0.0), style=Style(stroke=t.FG, stroke_width=3))
        self.bar = PathNode(path=rect(BAR_W, 1e-3), style=Style(fill=t.A, fill_opacity=0.7))
        title = mtext("余额", LABEL, t.FG)
        title.move_to([bx, -BAR_H - 0.45])
        self.balance.add(axis, self.bar, zero, title)
        node.add(self.balance)
        self.readout: Node | None = None  # 播放到现在显示的读数
        self.total = self.compute()
        self.shown = float(self.total)
        self.readout = self.make_readout(self.total, visible=True)
        self.draw_bar(self.shown)
        return Built(node, parts={"cash": [self.cards["cash"]], "iou": [self.cards["iou"]], "balance": [self.balance]})

    # ---- 几何 ----

    def slot(self, kind: str, k: int) -> np.ndarray:
        return np.array([k * (W + GAP), ROW if kind == "cash" else -ROW])

    def _card(self, kind: str, value: int, visible: bool) -> Node:
        t = self.theme
        color = t.A if kind == "cash" else t.NO
        g = Group(PathNode(path=rounded_rect(W, H, 0.1), style=Style(fill=color, fill_opacity=0.35, stroke=color, stroke_width=3)))
        text = mtext(f"{value} 元" if kind == "cash" else f"欠 {value} 元", CARD_TEXT, t.ink("NO") if kind == "iou" else t.FG)
        text.move_to([0.0, 0.0])
        g.add(text)
        p = self.slot(kind, len(self.rows[kind]))
        g.matrix = T.translate(float(p[0]), float(p[1]))
        g.set_style(opacity=1.0 if visible else 0.0)
        self.cards[kind].add(g)
        self.rows[kind].append((g, value))
        return g

    def compute(self) -> int:
        return sum(v for _, v in self.rows["cash"]) - sum(v for _, v in self.rows["iou"])

    def draw_bar(self, value: float) -> None:
        t = self.theme
        h = max(1e-3, abs(value) * self.unit)
        y = h / 2 if value >= 0 else -h / 2
        self.bar.path = rect(BAR_W, h).transformed(T.translate(self.bar_x, y))
        self.bar.style = (self.bar.style or Style()).with_(fill=t.A if value >= 0 else t.NO)
        if self.readout is not None:
            self.readout.move_to([self.bar_x + BAR_W / 2 + 0.25 + self.readout.width / 2, (h if value >= 0 else -h)])
        self.shown = value

    def make_readout(self, value: int, visible: bool) -> Node:
        """余额读数（+6、0、−6），放在条顶旁边。"""
        t = self.theme
        tex = f"+{value}" if value > 0 else str(value)
        node = mmath(tex, LABEL + 8, t.ink("HL") if value > 0 else (t.ink("NO") if value < 0 else t.FG))
        h = abs(value) * self.unit
        node.move_to([self.bar_x + BAR_W / 2 + 0.25 + node.width / 2, h if value >= 0 else -h])
        node.set_style(opacity=1.0 if visible else 0.0)
        self.balance.add(node)
        return node

    # ---- 动作 ----

    def action(self, act: str, args: dict) -> list[Animation]:
        kind = args.get("kind", "cash")
        n = int(args.get("count", 1))
        steps: list[list[Animation]] = []
        if act == "put":
            value = int(args.get("value", self.value))
            for _ in range(n):
                card = self._card(kind, value, visible=False)
                base = card.matrix.copy()
                card.matrix = T.translate(0.0, 1.2) @ base
                steps.append([Travel(card, straight((0, 1.2), (0, 0)), 0.5), FadeTo(card, 1.0, 0.3)])
        elif act == "take":
            for _ in range(n):
                card, _v = self.rows[kind].pop()
                p = card.matrix[:2, 2].copy()
                if args.get("quiet"):
                    steps.append([FadeTo(card, 0.0, 0.3)])
                elif kind == "cash":
                    steps.append([Travel(card, straight((0, 0), (0, 1.2)), 0.5), FadeTo(card, 0.0, 0.5)])
                else:
                    halves = []
                    for left in (True, False):
                        half = PathNode(path=_half(left), style=Style(fill=self.theme.NO, fill_opacity=0.35,
                                                                     stroke=self.theme.NO, stroke_width=3, opacity=0.0))
                        half.matrix = T.translate(float(p[0]), float(p[1]))
                        self.cards[kind].add(half)
                        halves.append(_Tear(half, p, -1.0 if left else 1.0))
                    steps.append([FadeTo(card, 0.0, 0.15)] + halves)
        else:
            raise KeyError(f"组件 {self.name} 没有动作 {act}")
        self.total = self.compute()
        new = self.make_readout(self.total, visible=False)
        return [Steps(steps, duration=max(0.7, 0.6 * n)), _Balance(self, self.total, new)]

