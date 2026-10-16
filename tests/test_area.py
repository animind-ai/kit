"""面积模型（勾股定理拼图）的几何与组件测试：不渲染视频。"""

import math

import numpy as np
import pytest

from animind_kit import theme
from animind_kit.mx import area, widgets
from animind_kit.mx.area import AreaModel

T = theme.theme("paper")
LEGS = [(2.0, 3.0), (3.0, 4.0), (1.0, 5.0), (2.5, 2.5)]


@pytest.mark.parametrize("a,b", LEGS)
def test_triangles_are_congruent_right_triangles(a, b):
    for layout in area.LAYOUTS:
        for p, q, r in area.triangles(layout, a, b):
            assert math.isclose(math.dist(p, q), a) and math.isclose(math.dist(p, r), b)
            assert math.isclose(math.dist(q, r), math.hypot(a, b))
            assert abs(np.dot(np.subtract(q, p), np.subtract(r, p))) < 1e-9  # 直角在 P


@pytest.mark.parametrize("a,b", LEGS)
def test_rearrangement_is_pure_translation(a, b):
    """两种摆法之间每块只平移：直角边的方向不变。"""
    old, new = area.anchors("four_around_c", a, b), area.anchors("two_rectangles", a, b)
    assert [u for _, u in old] == [u for _, u in new]


@pytest.mark.parametrize("a,b", LEGS)
def test_areas_add_up(a, b):
    s = a + b
    tri = a * b / 2
    for layout in area.LAYOUTS:
        assert all(math.isclose(area.area(t), tri) for t in area.triangles(layout, a, b))
        hole_area = sum(area.area(h) for h in area.holes(layout, a, b).values())
        assert math.isclose(4 * tri + hole_area, s * s)  # 四块加空白正好铺满大正方形
    c2 = area.area(area.holes("four_around_c", a, b)["c2"])
    two = area.holes("two_rectangles", a, b)
    assert math.isclose(c2, a * a + b * b)
    assert math.isclose(c2, area.area(two["a2"]) + area.area(two["b2"]))  # 勾股定理


@pytest.mark.parametrize("a,b", LEGS)
def test_pieces_tile_the_square_without_overlap(a, b):
    s = a + b
    for layout in area.LAYOUTS:
        pieces = area.triangles(layout, a, b) + list(area.holes(layout, a, b).values())
        for i in range(len(pieces)):
            assert all(-1e-9 <= x <= s + 1e-9 and -1e-9 <= y <= s + 1e-9 for x, y in pieces[i])
            for j in range(i + 1, len(pieces)):
                assert not area.overlap(pieces[i], pieces[j]), (layout, i, j)


def test_hole_c2_is_a_square_of_side_c():
    a, b = 2.0, 3.0
    pts = area.holes("four_around_c", a, b)["c2"]
    sides = [math.dist(pts[i], pts[(i + 1) % 4]) for i in range(4)]
    assert all(math.isclose(x, math.hypot(a, b)) for x in sides)
    assert all(abs(np.dot(np.subtract(pts[(i + 1) % 4], pts[i]), np.subtract(pts[(i + 2) % 4], pts[(i + 1) % 4]))) < 1e-9
               for i in range(4))


def test_obtuse_pieces_do_not_fit():
    a, b = 2.0, 3.0
    for layout in area.LAYOUTS:
        bent = area.triangles(layout, a, b, angle=110)
        s = a + b
        outside = any(not (-1e-9 <= x <= s + 1e-9 and -1e-9 <= y <= s + 1e-9) for t in bent for x, y in t)
        overlapping = any(area.overlap(bent[i], bent[j]) for i in range(4) for j in range(i + 1, 4))
        assert outside or overlapping
        # 夹角 110° 时第三边的平方比 a²+b² 大
        p, q, r = bent[0]
        assert math.dist(q, r) ** 2 > a * a + b * b


def test_slide_order_avoids_collisions():
    a, b = 2.0, 3.0
    old, new = area.triangles("four_around_c", a, b), area.triangles("two_rectangles", a, b)
    steps = area.slide_order(old, new)
    assert sorted(i for step in steps for i in step) == [0, 2, 3]  # 右下那块不用动
    assert all(len(step) == 1 for step in steps)
    back = area.slide_order(new, old)
    assert sorted(i for step in back for i in step) == [0, 2, 3]


def test_widget_parts_and_actions():
    cls = widgets.lookup("area_model")
    assert cls is AreaModel
    m = cls("p", {"widget": "area_model", "kind": "right_triangles", "legs": [2, 3], "pieces": "hidden"}, T, {})
    assert {k: len(v) for k, v in m.built.parts.items()} == {"tri": 4, "hole": 3, "area": 3, "side": 3}
    assert len(m.built.items) == 4 and all(t.style.opacity == 0.0 for t in m.tris)
    assert all(h.style.opacity == 0.0 for h in m.holes.values())
    before = [t.matrix.copy() for t in m.tris]
    anims = m.action("slide", {"layout": "two_rectangles"})
    assert len(anims) == 1 and m.layout == "two_rectangles"
    anims[0].begin()
    anims[0].finish()
    moved = [np.array(t.matrix[:2, 2] - b[:2, 2]) / m.k for t, b in zip(m.tris, before)]
    assert np.allclose(moved, [[0, 2], [0, 0], [-3, 0], [2, -3]])  # 上移 a、不动、左移 b、(a, -b)
    m.action("bend", {"angle": 110})
    assert m.angle == 110
    single = cls("o", {"widget": "area_model", "kind": "right_triangles", "count": 1, "legs": [2, 3]}, T, {})
    assert len(single.built.items) == 1 and len(single.built.parts["side"]) == 3
