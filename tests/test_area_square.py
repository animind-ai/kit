"""kind: square 的 side：写 a+b 或等于两直角边之和的数才合法；画出来的大正方形的边长就是这个。"""

import pytest

from animind_kit import theme
from animind_kit.mx import widgets

T = theme.theme("paper")


def square(**extra):
    return widgets.lookup("area_model")("big", {"widget": "area_model", "kind": "square", **extra}, T, {})


def test_square_side_a_plus_b_is_accepted_with_default_legs():
    m = square(side="a+b")
    assert len(m.tris) == 4  # 四个三角形拼成边长 a+b 的大正方形
    assert m.built.parts["tri"] and len(m.built.parts["tri"]) == 4


def test_square_side_numeric_must_equal_leg_sum():
    assert square(side=7) is not None  # 默认 legs 是 3 和 4
    assert square(side=7, legs=[2, 5]) is not None
    with pytest.raises(ValueError, match="side"):
        square(side=9)  # 3 + 4 不是 9
    with pytest.raises(ValueError, match="side"):
        square(side="c")  # 大正方形的边不是斜边


def test_square_side_missing_is_the_default_square():
    assert len(square().tris) == 4


def test_right_triangles_is_unchanged_by_the_square_rule():
    m = widgets.lookup("area_model")("p", {"widget": "area_model", "kind": "right_triangles", "legs": [2, 3]}, T, {})
    assert len(m.tris) == 4
