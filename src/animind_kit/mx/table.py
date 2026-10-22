"""数例表 seq_table（墨线实现）：几行带行头的格子，一列一列地露出来。

竞赛题讲"先试几步"时用：行是 a_n、p_n、b_n 这样的量，列是数列的一项一项。学生反馈枚举太快，
所以表默认一列都不露（known: 0），旁白讲到哪一列就 `show table[i]` 哪一列；`mark {row, col}` 给一格垫色。

参数：
- rows：[{label, values, role?}]，label 是行头，values 是这一行的值：数直接写；字符串没有 $ 也没有中文时整个当公式
  （'b_1+1'），否则按文字和 $公式$ 混排（'商的格子'、'第 $k$ 项'）；
- known：一开始就露出的列数（默认 0，即只露行头）；
- size：格子里的字号（默认 30）。

暴露的子元素：
- table[i]：第 i 列（不含行头列），show table[i] / table[i:j] 一列或几列；
- row[r]：第 r 行的数据格（focus / mark 用）；
- head[r]：第 r 行的行头。

动作：
- shade {row, col, role?}：给第 row 行第 col 列的格子垫色（默认 HL 的 MARK 色）；unshade 同参数去掉。
  （不叫 mark：mark 是规范内置的动作，指向整个对象。）
"""

from __future__ import annotations

from moxian.anim import Animation, Restyle
from moxian.core import Group, Node, PathNode, Style
from moxian.path import rect

from .text import rich
from .widgets import Built, Widget


class SeqTable(Widget):
    name = "seq_table"
    SIZE = 30
    H = 0.62  # 行高
    PAD = 0.28  # 格子左右留白

    def build(self) -> Built:
        t = self.theme
        d = self.definition
        size = float(d.get("size", self.SIZE))
        rows = d["rows"]
        n = max(len(r["values"]) for r in rows)
        known = int(d.get("known", 0))
        self.cells: list[list[PathNode]] = []  # 格子底 [r][c]
        self.texts: list[list[Node]] = []
        heads: list[Node] = []
        for r in rows:
            color = t.ink(r.get("role")) if r.get("role") else t.FG
            heads.append(rich(self._text(r["label"]), t, size=size, color=color).node)
            self.texts.append([rich(self._text(v), t, size=size, color=color).node for v in r["values"]])
        head_w = max(h.width for h in heads) + 2 * self.PAD
        col_w = [max((self.texts[r][c].width for r in range(len(rows)) if c < len(self.texts[r])), default=0.3) + 2 * self.PAD
                 for c in range(n)]
        col_w = [max(w, 0.9) for w in col_w]
        h = self.H
        xs = [0.0]
        for w in col_w:
            xs.append(xs[-1] + w)
        node = Group(name=self.obj_id)
        columns = [Group(name=f"col{c}") for c in range(n)]
        row_groups = [Group(name=f"row{r}") for r in range(len(rows))]
        head_nodes: list[Node] = []
        for r in range(len(rows)):
            y = -r * h
            bg = PathNode(path=rect(head_w, h), style=Style(fill=t.CARD, stroke=t.DIM, stroke_width=1.6, stroke_opacity=0.6))
            bg.move_to([-head_w / 2, y])
            heads[r].move_to([-head_w / 2, y])
            head = Group(bg, heads[r])
            node.add(head)
            head_nodes.append(head)
            self.cells.append([])
            for c in range(n):
                cx = xs[c] + col_w[c] / 2
                cell_bg = PathNode(path=rect(col_w[c], h), style=Style(fill=t.BG, stroke=t.DIM, stroke_width=1.6, stroke_opacity=0.6))
                cell_bg.move_to([cx, y])
                cell = Group(cell_bg)
                if c < len(self.texts[r]):
                    self.texts[r][c].move_to([cx, y])
                    cell.add(self.texts[r][c])
                self.cells[r].append(cell_bg)
                columns[c].add(cell)
                row_groups[r].add(cell)
        for c, col in enumerate(columns):
            if c >= known:
                col.set_style(opacity=0.0)
            node.add(col)
        return Built(node, parts={"row": row_groups, "head": head_nodes}, items=columns)

    @staticmethod
    def _text(v) -> str:
        """格子里的内容：数当公式排；字符串里有 $ 或中文就按文字公式混排，否则整个当公式。"""
        if not isinstance(v, str):
            return f"${v}$"
        if "$" in v or any(ord(ch) > 0x2E80 for ch in v):
            return v
        return f"${v}$"

    def action(self, act: str, args: dict) -> list[Animation]:
        if act not in ("shade", "unshade"):
            raise KeyError(act)
        cell = self.cells[int(args["row"])][int(args["col"])]
        t = self.theme
        fill = t.BG if act == "unshade" else (t.color(args["role"]) if args.get("role") else t.MARK)
        return [Restyle(cell, 0.4, fill=fill)]
