"""墨线后端的单元测试（不渲染视频；文字和公式会真的走字体和 LaTeX）。"""

from pathlib import Path

import numpy as np
from moxian.text import Text
from scene_spec.loader import load_file

from animind_kit import theme
from animind_kit.backends.moxian_backend import MoxianCompiler
from animind_kit.mx import layout, widgets
from animind_kit.mx.text import inline, rich, segments

EXAMPLE = Path(__file__).resolve().parents[2] / "scene-spec" / "examples" / "s67-p01.scene.yaml"
T = theme.theme("paper")


def test_segments_keep_order_and_spans():
    parts = segments("定义：对 [$b_n$]{#b .B}，则 \\[注\\]")
    assert [p[0] for p in parts] == ["定义：对 ", "$b_n$", "，则 \\[注\\]"]
    assert parts[1][1].id == "b" and "B" in parts[1][1].classes


def test_inline_keeps_spaces_and_shares_baseline():
    a, b = Text("已知 ", 30), Text("为 ", 30)
    row = inline([a, b])
    assert b.bbox().x0 - a.bbox().x1 > 0.04  # 空格没有丢
    assert abs(a.matrix[1, 2] - b.matrix[1, 2]) < 1e-9  # 同一基线
    assert row.width > a.width + b.width * 0.9


def test_rich_colors_spans_by_role():
    r = rich("令 [$b_n$]{#b .B}，则", T, size=30)
    assert "b" in r.spans
    assert r.spans["b"].style.fill == T.B
    assert r.node.width > 0.5


def test_widgets_expose_parts_and_items():
    data = {"A": [2, 5, 11, 21], "B": [3, 6, 10], "C": [3, 4]}
    table = widgets.DiffTable("t", {"widget": "diff_table", "rows": [
        {"data": "A", "role": "A"}, {"data": "B", "role": "B"}, {"data": "C", "role": "C"}], "known": [4, 0, 0]}, T, data)
    assert [len(table.built.parts[k]) for k in ("A", "B", "C")] == [4, 3, 2]
    assert table.cells[1][0].style.opacity == 0.0
    anims = table.action("derive", {"row": "B", "index": 0})
    assert len(anims) == 1 and table.shown[1] == 1
    chips = widgets.Chips("p", {"widget": "chips", "items": ["一", "二"]}, T, {})
    assert len(chips.built.items) == 2 and len(chips.focus(1)) == 4
    card = widgets.ProblemCard("c", {"widget": "problem_card", "lines": ["[甲]{#x .A} 乙", "丙"]}, T, {})
    assert "x" in card.built.spans and len(card.built.items) == 2


def test_place_pins_and_stacks():
    card = widgets.ProblemCard("c", {"widget": "problem_card", "lines": ["一行很长很长很长很长很长很长很长很长很长很长很长很长的题目"]}, T, {}).node
    layout.place(card, {"pin": "top"}, {})
    assert abs(card.width - layout.PIN_WIDTH) < 1e-6 and abs(card.center()[0]) < 1e-6
    assert card.bbox().y1 > 3.5
    text = rich("标题", T, 36).node
    layout.place(text, {"in": "stage"}, {}, "stage", stack_below=0.0, usage=(False, False, False))
    assert np.allclose(text.center(), [0, 0], atol=1e-6)


def test_compiler_builds_example_and_places_lazily():
    doc = load_file(EXAMPLE)
    c = MoxianCompiler(doc, T, None, log=lambda *_: None)
    assert set(c.objects) == set(doc["objects"])
    assert all(b.node.style.opacity == 0.0 for b in c.objects.values())  # 一开始都隐藏
    card = c.place("card")
    assert c.objects["card"].placed and card.bbox().y1 > 3.5
    answer = c.place("answer")
    blank = c.wbox(c.resolve("card#blank"))
    assert answer.bbox().y0 >= blank.y1  # 答案坐在填空线上方，不被线穿过
    anims, dur = c.animations({"act": "show", "target": "title"})
    assert len(anims) == 1 and dur > 0 and "title" in c.visible
