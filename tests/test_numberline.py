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
    assert sizes == {"tick": 7, "label": 7, "unit": 6, "frac": 1, "span": 1, "jump": 9, "count": 9, "rest": 1, "point": 4}
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
