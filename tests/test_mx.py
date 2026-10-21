"""墨线后端的单元测试（不渲染视频；文字和公式会真的走字体和 LaTeX）。"""

from pathlib import Path

import numpy as np
from moxian.text import Text
from scene_spec.loader import load_file

from animind_kit import theme
from animind_kit.backends.moxian_backend import MoxianCompiler
from animind_kit.mx import layout, widgets
from animind_kit.mx.text import Space, inline, rich, runs, segments

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


def _row_width(body: str) -> float:
    return inline(runs(body, 30, "#000")).width


def test_space_between_formulas_is_a_word_space():
    tight = _row_width(r"$d=0$$\Longrightarrow$")
    spaced = _row_width(r"$d=0$ $\Longrightarrow$")
    word = runs(" ", 30, "#000")[0]
    assert isinstance(word, Space) and word.width > 0.08
    assert abs(spaced - (tight - 0.06 + word.width)) < 1e-6  # 空格代替默认的公式间距
    assert abs(_row_width(r"$d=0$   $\Longrightarrow$") - spaced) < 1e-6  # 连续空白算一个
    assert abs(_row_width(r"$d=0$\ $\Longrightarrow$") - spaced) < 1e-6
    assert abs(_row_width(r"$d=0$~$\Longrightarrow$") - spaced) < 1e-6


def test_spacing_commands_between_runs_never_crash():
    tight = _row_width(r"$d=0$$\Longrightarrow$")
    word = runs(r"\ ", 30, "#000")[0].width
    quad = _row_width(r"$d=0$\quad$\Longrightarrow$") - tight + 0.06
    assert abs(quad - 3 * word) < 1e-6  # \quad 是 1em，词间空是 1/3em
    assert abs(_row_width(r"$d=0$\qquad$\Longrightarrow$") - tight + 0.06 - 2 * quad) < 1e-6
    assert abs(_row_width(r"$d=0$\ \ $\Longrightarrow$") - tight + 0.06 - 2 * word) < 1e-6
    assert _row_width(r"$d=0$\,$\Longrightarrow$") < _row_width(r"$d=0$\;$\Longrightarrow$") < quad + tight
    # 控制词后面的空格被吞掉，前面的算数（同 TeX 文本模式）
    assert abs(_row_width(r"$d=0$ \quad $\Longrightarrow$") - tight + 0.06 - quad - word) < 1e-6


def test_spacing_only_and_empty_segments():
    assert runs("", 30, "#000") == [] and runs("$$", 30, "#000") == []
    assert all(isinstance(n, Space) for n in runs(r" \quad ", 30, "#000"))
    assert inline(runs(r"\quad", 30, "#000")).bbox() is None
    nodes = runs(r"\quad x \underline{a b}\;", 30, "#000")  # 别的命令：只剥掉首尾间距
    assert isinstance(nodes[0], Space) and isinstance(nodes[-1], Space) and len(nodes) == 3


def test_plain_space_next_to_han_uses_cjk_gap():
    assert abs(_row_width("就是 $x$") - _row_width("就是$x$")) < 1e-6  # 同 xeCJK：空格不额外加宽
    assert _row_width(r"就是\quad 等比") > _row_width("就是等比") + 0.25  # 显式命令总是算数


def test_space_at_span_boundary_between_texts_is_kept():
    # 片段边界上的空格和段内的一样宽
    joined = rich("例 1(1) 定义", T, size=30).node.width
    split = rich("[例 1(1)]{#t} 定义", T, size=30).node.width
    tight = rich("[例 1(1)]{#t}定义", T, size=30).node.width
    assert abs(split - joined) < 1e-6 and split > tight + 0.05


def test_rich_spans_skip_spaces():
    r = rich(r"$d=0$\quad$\Longrightarrow$\quad[就是等比数列]{#q .B}", T, size=30)
    assert isinstance(r.spans["q"], Text)
    assert not any(isinstance(c, Space) for c in r.node.children)


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
    layout.place(text, {"in": "stage"}, {}, layout.regions(False, False, False)["stage"])
    assert np.allclose(text.center(), [0, 0], atol=1e-6)


def test_pin_top_with_size_keeps_the_natural_size():
    # 短公式钉在顶上：不写 size 撑满题卡宽度（字放大好几倍）；写了 size 保持自然大小，只在太宽时缩小
    short = rich("$A=U\\Sigma V^\\top$", T, 36).node
    w0 = short.width
    layout.place(short, {"pin": "top", "size": {"max_width": 1}}, {})
    assert abs(short.width - w0) < 1e-6 and abs(short.center()[0]) < 1e-6 and short.bbox().y1 > 3.5
    blown = rich("$A=U\\Sigma V^\\top$", T, 36).node
    layout.place(blown, {"pin": "top"}, {})
    assert blown.height > short.height * 1.5  # 以前的样子（不写 size 时不变）
    long = rich("一行很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长的题目", T, 36).node
    layout.place(long, {"pin": "top", "size": {"max_width": 0.5}}, {})
    assert abs(long.width - layout.PIN_WIDTH * 0.5) < 1e-6


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
