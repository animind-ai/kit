"""数轴（分数除法的跳跃）的算术与组件测试：不渲染视频。"""

from fractions import Fraction as F

import pytest

from animind_kit import theme
from animind_kit.mx import numberline as nl
from animind_kit.mx import widgets
from animind_kit.mx.numberline import NumberLine

T = theme.theme("paper")
DEF = {"widget": "number_line", "range": [0, 6], "parts": 1, "measures": ["2", "2/3", "3/2", "4/5"], "fractions": True}


def run(anims):
    for a in anims:
        a.begin()
        a.finish()


@pytest.mark.parametrize("length,count,left", [
    ("2", 3, 0), ("2/3", 9, 0), ("3/2", 4, 0), ("4/5", 7, F(2, 5)), ("3/4", 8, 0),
])
def test_jumps_are_exact(length, count, left):
    whole, rest = nl.jumps(0, 6, nl.frac(length))
    assert len(whole) == count and rest == left
    assert whole[0][0] == 0 and all(b - a == nl.frac(length) for a, b in whole)
    assert all(whole[i][1] == whole[i + 1][0] for i in range(len(whole) - 1))
    # 6 ÷ (p/q) = 6 × q ÷ p：整跳数加上剩余占一跳的比例，就是商
    assert count + rest / nl.frac(length) == F(6) / nl.frac(length)


def test_remainder_is_read_in_jumps_not_units():
    whole, rest = nl.jumps(0, 6, F(4, 5))
    assert rest == F(2, 5)  # 按单位 1 读是 2/5
    assert rest / F(4, 5) == F(1, 2)  # 按一跳读是 1/2：6 ÷ 4/5 = 7½
    assert nl.tex(len(whole) + rest / F(4, 5)) == r"7\frac{1}{2}"


def test_frac_and_tex():
    assert nl.frac("2/3") == F(2, 3) and nl.frac(2) == 2 and nl.frac("3 / 2") == F(3, 2)
    with pytest.raises(ValueError):
        nl.frac(0.5)
    assert nl.tex(F(9)) == "9" and nl.tex(F(2, 3)) == r"\frac{2}{3}" and nl.tex(F(-1, 2)) == r"-\frac{1}{2}"


def test_ticks_and_max_jumps():
    assert nl.ticks(0, 2, 3) == [F(k, 3) for k in range(7)]
    assert nl.max_jumps(0, 6, ["2", "2/3", "3/2", "4/5"]) == 9


def test_morph_plan_keeps_shared_ticks_and_order():
    third = [F(1, 3), F(2, 3)]
    sixth = [F(k, 6) for k in range(1, 6)]
    pairs, gone, born = nl.morph_plan(third, sixth)
    assert pairs == [(0, 1), (1, 3)] and gone == [] and born == [0, 2, 4]  # 1/3、2/3 不动，其余长出来
    pairs, gone, born = nl.morph_plan(third, [F(1, 2)])
    assert len(pairs) == 1 and len(gone) == 1 and born == []  # 一根滑到 1/2，另一根缩回去
    fifth = [F(k, 5) for k in range(1, 5)]
    pairs, gone, born = nl.morph_plan([F(1, 2)], fifth)
    assert len(pairs) == 1 and len(born) == 3
    for plan_old, plan_new in ((third, fifth), (fifth, third), ([], third), (third, [])):
        pairs, gone, born = nl.morph_plan(plan_old, plan_new)
        assert [j for _, j in sorted(pairs)] == sorted(j for _, j in pairs)  # 配对不交叉
        assert sorted([i for i, _ in pairs] + gone) == list(range(len(plan_old)))
        assert sorted([j for _, j in pairs] + born) == list(range(len(plan_new)))


def test_widget_registered_with_parts():
    assert widgets.lookup("number_line") is NumberLine
    m = NumberLine("line", DEF, T, {})
    sizes = {k: len(v) for k, v in m.built.parts.items()}
    assert sizes == {"tick": 7, "label": 7, "unit": 6, "frac": 1, "span": 1, "jump": 9, "count": 9, "rest": 1, "point": 4, "strip": 1, "groups": 1, "arrows": 1, "turns": 1}
    xs = [float(t.matrix[0, 2]) for t in m.built.parts["tick"]]
    assert xs == pytest.approx([k * m.UNIT for k in range(7)])


def test_jump_subdivide_remainder_actions():
    m = NumberLine("line", DEF, T, {})
    size_before = (m.node.width, m.node.height)
    run(m.action("jump", {"length": "2/3"}))
    jumps = m.built.parts["jump"]
    assert all(j.children for j in jumps)  # 九跳都填上了
    ends = [j.children[0].path.bbox() for j in jumps]
    assert [round(b[2] / m.UNIT, 6) for b in ends] == [round(float(F(2, 3) * (i + 1)), 6) for i in range(9)]
    assert (m.node.width, m.node.height) == pytest.approx(size_before)  # 占位框：跳完大小不变

    run(m.action("subdivide", {"parts": 3}))
    minor = [round(float(t.bbox().center[0]) / m.UNIT, 6) for t in m.minor[0]]
    assert minor == [round(1 / 3, 6), round(2 / 3, 6)]
    assert len(m.frac_labels) == 2

    run(m.action("erase", {}))
    run(m.action("subdivide", {"parts": 5}))
    run(m.action("jump", {"length": "4/5"}))
    assert sum(1 for j in m.built.parts["jump"] if j.children) == 7 and m.left == F(2, 5)
    run(m.action("remainder", {}))
    assert m.built.parts["rest"][0].children  # 剩下那一小段和 1/2

    run(m.action("highlight", {"to": "4/5"}))
    b = m.bar.path.bbox()
    assert (b[0], b[2]) == pytest.approx((0.0, 0.8 * m.UNIT))
    run(m.action("mark_point", {"at": 6, "label": "6"}))
    assert m.point_slots[0].children


def test_jump_errors():
    m = NumberLine("line", DEF, T, {})
    with pytest.raises(ValueError):
        m.action("remainder", {})  # 还没跳
    run(m.action("jump", {"length": "2/3"}))
    with pytest.raises(ValueError):
        m.action("remainder", {})  # 正好跳完
    with pytest.raises(ValueError):
        m.action("jump", {"length": "2/3", "count": 10})
    with pytest.raises(KeyError):
        m.action("flip", {})


def test_unit_param_stretches_the_axis():
    """0–1 的短数轴写 unit 后一个单位画得更长；默认值和类属性一致，别的实例不受影响。"""
    wide = NumberLine("line", {**DEF, "range": [0, 1], "unit": 9}, T, {})
    plain = NumberLine("line", {**DEF, "range": [0, 1]}, T, {})
    assert wide.UNIT == 9 and plain.UNIT == NumberLine.UNIT
    assert float(wide.built.parts["tick"][1].matrix[0, 2]) == pytest.approx(9.0)
    assert wide.x(F(1, 2)) == pytest.approx(4.5)


def test_keep_role_and_strip_actions():
    """几段接着走（keep / from）、带角色的点和垫色、轴下方的分数条和分组框。"""
    d = {**DEF, "range": [0, 1], "measures": ["1/2", "1/3", "1/6"], "bars": True}
    m = NumberLine("line", d, T, {})
    size_before = (m.node.width, m.node.height)
    run(m.action("jump", {"length": "1/2", "count": 1, "numbers": False}))
    first = m.jump_set
    run(m.action("jump", {"length": "1/3", "count": 1, "from": "1/2", "keep": True, "role": "B"}))
    assert first in m.kept_jumps and (first.style is None or first.style.opacity > 0)  # 第一段没被收起
    end = m.built.parts["jump"][0].children[0].path.bbox()[2]
    assert end / m.UNIT == pytest.approx(5 / 6)
    run(m.action("highlight", {"to": "1/2", "role": "A"}))
    run(m.action("highlight", {"from": "1/2", "to": "5/6", "role": "B", "keep": True}))
    assert len(m.bars_kept) == 1
    run(m.action("mark_point", {"at": "2/5", "role": "NO"}))
    assert m.point_slots[0].children[0].style.fill == T.NO
    run(m.action("bar", {"parts": 6}))
    run(m.action("fill", {"cells": "0:3", "role": "A"}))
    assert [round(c.style.fill_opacity, 2) for c in m.cells] == [0.6] * 3 + [0.0] * 3
    run([m.action("group", {"size": 2})[0]])
    assert len(m.groups.children) == 6  # 三组，每组一个框一个数
    assert (m.node.width, m.node.height) == pytest.approx(size_before)  # bars: true 预留了位置
    run(m.action("erase", {}))
    assert m.strip.style.opacity == 0 and first.style.opacity == 0
    run(m.action("bar", {"parts": 6}))  # erase 之后再分组：框要重新看得见
    run(m.action("group", {"size": 3}))
    assert m.groups.style.opacity == 1 and m.groups.children[0].style.opacity == 1


# ---------- 从 0 出发的箭头（#15 负负得正）----------

ARROWS = {"widget": "number_line", "range": [-6, 6], "arrows": True}


def tip(line: NumberLine) -> float:
    """现在这支箭头尖的 x（箭头尖路径的最远点，按节点矩阵换算）。"""
    b = line.arrow.children[1].path.bbox()
    o = line.x(F(0))
    return b[2] if abs(b[2] - o) > abs(b[0] - o) else b[0]


def test_arrow_reserve_covers_the_half_turn():
    plain = NumberLine("n", {**ARROWS, "arrows": False}, T, {})
    turning = NumberLine("n", ARROWS, T, {})
    assert turning.node.bbox().y1 >= 6 * turning.UNIT > plain.node.bbox().y1


def test_turn_rotates_at_playback_and_lands_on_minus():
    line = NumberLine("n", ARROWS, T, {})
    grow = line.action("arrow", {"to": 3})
    turn = line.action("turn", {"label": r"\times(-1)"})
    assert line.arrow_value == -3
    run(grow)
    assert tip(line) == pytest.approx(line.x(F(3)))  # 建 turn 时还没转
    rot = turn[0]
    rot.begin()
    rot.interpolate(0.5)  # 转到一半：箭头竖着朝上
    b = line.arrow.bbox()
    assert b.y1 > 3 * line.UNIT * 0.9 and abs((b.x0 + b.x1) / 2 - line.x(F(0))) < 0.3
    for a in turn:
        a.begin()
        a.finish()
    assert tip(line) == pytest.approx(line.x(F(-3)))
    assert len(line.turn_layer.children) == 2  # 半圆 + 标注


def test_stretch_then_two_turns_end_at_six():
    line = NumberLine("n", ARROWS, T, {})
    run(line.action("arrow", {"to": 3}) + line.action("stretch", {"to": 6}))
    assert tip(line) == pytest.approx(line.x(F(6)))
    run(line.action("turn", {}))
    run(line.action("turn", {}))
    assert line.arrow_value == 6 and tip(line) == pytest.approx(line.x(F(6)))
    shown = [n for n in line.turn_layer.children if n.style.opacity > 0]
    assert len(shown) == 1  # 只留最后一次转身的半圈


def test_keep_leaves_a_faint_copy_and_erase_clears():
    line = NumberLine("n", ARROWS, T, {})
    run(line.action("arrow", {"to": 3}))
    first = line.arrow
    run(line.action("arrow", {"to": -3, "keep": True}))
    assert first.style.opacity == pytest.approx(0.3) and line.arrow.style.opacity == 1.0
    run(line._erase())
    assert all(n.style.opacity == 0.0 for n in line.arrow_layer.children) and line.arrow is None
