"""坐标平面 plane（向量、乘 i 转 90°、矩阵变形）：不渲染视频，只看几何和样式。"""

import math

import numpy as np
import pytest

from animind_kit import theme
from animind_kit.mx import widgets
from animind_kit.mx.plane import Plane, clip_line, rotation_angle, tween_matrix

T = theme.theme("paper")


def run(anims):
    for a in anims:
        a.begin()
        a.finish()


def plane(**extra) -> Plane:
    return Plane("p", {"widget": "plane", "x": [-2, 5], "y": [-1, 4], **extra}, T, {})


def tip(p: Plane, name: str) -> np.ndarray:
    """箭头尖的顶点（画面坐标，UNIT = 1 时等于数学坐标）。"""
    head = p.items[name].node.children[1]
    return np.round(head.path.subpaths[0].points()[0], 6)


def opacity(node) -> float:
    return node.style.opacity if node.style else 1.0


def test_registered():
    assert widgets.lookup("plane") is Plane


def test_vector_grows_at_playback():
    p = plane()
    anims = p.action("vector", {"name": "a", "v": [3, 1], "label": r"\vec a"})
    assert opacity(p.items["a"].node) == 0.0 and p.items["a"].node.children[1].path.is_empty()  # 建动作时还没画
    run(anims)
    assert tuple(tip(p, "a")) == (3.0, 1.0)
    assert opacity(p.items["a"].label) == 1.0


def test_slide_copy_puts_b_on_the_tip_of_a():
    p = plane()
    run(p.action("vector", {"name": "a", "v": [3, 1]}) + p.action("vector", {"name": "b", "v": [1, 2]}))
    anims = p.action("slide", {"name": "b", "at": [3, 1], "copy": "b2"})
    assert tuple(tip(p, "b")) == (1.0, 2.0)
    run(anims)
    assert tuple(tip(p, "b")) == (1.0, 2.0)  # 原来那支不动
    assert tuple(tip(p, "b2")) == (4.0, 3.0)
    assert p.items["b2"].dashed


def test_rotate_by_90_twice_is_times_minus_one():
    p = plane(x=[-4, 4], y=[-4, 4], complex=True)
    run(p.action("vector", {"name": "z", "v": [3, 1]}))
    run(p.action("rotate", {"name": "z", "angle": 90, "label": r"\times i"}))
    assert np.allclose(tip(p, "z"), (-1, 3))
    run(p.action("rotate", {"name": "z", "angle": 90}))
    assert np.allclose(tip(p, "z"), (-3, -1))
    arcs = [i for i in p.items.values() if i.kind == "arc"]
    assert len(arcs) == 2 and arcs[0].label is not None


def test_warp_composes_and_reset_undoes():
    p = plane(mesh=True)
    run(p.action("vector", {"name": "i", "v": [1, 0]}) + p.action("vector", {"name": "j", "v": [0, 1]}))
    run(p.action("warp", {"matrix": [[2, 1], [0, 1]]}))
    assert np.allclose(tip(p, "i"), (2, 0)) and np.allclose(tip(p, "j"), (1, 1))
    run(p.action("warp", {"matrix": [[0, -1], [1, 0]]}))
    assert np.allclose(p.M, [[0, -1], [2, 1]])  # 后做的在左边
    run(p.action("reset", {}))
    assert np.allclose(p.M, np.eye(2)) and np.allclose(tip(p, "j"), (0, 1))


def test_mesh_lines_stay_inside_the_range():
    p = plane(mesh=True)
    p.paint_mesh(np.array([[2.0, 1.0], [0.0, 1.0]]))
    pts = p.mesh.path.all_points()
    assert len(pts) > 0
    assert pts[:, 0].min() >= -2 - 1e-9 and pts[:, 0].max() <= 5 + 1e-9
    assert pts[:, 1].min() >= -1 - 1e-9 and pts[:, 1].max() <= 4 + 1e-9


def test_warp_is_lazy():
    p = plane(mesh=True)
    before = p.mesh.path.all_points().copy()
    p.action("warp", {"matrix": [[1, 1], [0, 1]]})
    assert np.array_equal(p.mesh.path.all_points(), before)


@pytest.mark.parametrize("m,angle", [([[0, -1], [1, 0]], math.pi / 2), ([[-1, 0], [0, -1]], math.pi), ([[2, 0], [0, 1]], None)])
def test_rotations_turn_instead_of_shrinking(m, angle):
    m = np.array(m, dtype=float)
    a = rotation_angle(m)
    assert (a is None) == (angle is None) and (a is None or math.isclose(abs(a), angle))
    if angle is not None:  # 转半圈的中途不会经过零矩阵
        assert abs(np.linalg.det(tween_matrix(m)(0.5))) == pytest.approx(1.0)


def test_clip_line():
    seg = clip_line(np.array([0.0, 0.0]), np.array([1.0, 1.0]), (-1, -1, 2, 3))
    assert np.allclose(seg[0], (-1, -1)) and np.allclose(seg[1], (2, 2))
    assert clip_line(np.array([0.0, 5.0]), np.array([1.0, 0.0]), (-1, -1, 2, 3)) is None


def test_fixed_points_stay_put():
    p = plane(mesh=True)
    run(p.action("point", {"name": "o", "at": [1, 2], "fixed": True}) + p.action("vector", {"name": "e", "v": [1, 0]}))
    run(p.action("warp", {"matrix": [[2, 0], [0, 1]]}))
    assert np.allclose(p.items["o"].pts, [[1, 2]]) and np.allclose(tip(p, "e"), (2, 0))


def test_shape_label_sits_on_the_centroid_and_later_shapes_draw_on_top():
    p = plane(x=[0, 7], y=[0, 7], grid=False, numbers=False)
    run(p.action("shape", {"name": "big", "points": [[0, 0], [7, 0], [7, 7], [0, 7]], "role": "A"}))
    run(p.action("vector", {"name": "v", "v": [1, 0]}))
    run(p.action("shape", {"name": "ov", "points": [[2, 2], [5, 2], [5, 5], [2, 5]], "role": "HL", "label": "(2b-a)^2"}))
    assert np.allclose(p.items["ov"].label.matrix[:2, 2], (3.5, 3.5))
    order = p.layer.children
    assert order.index(p.items["big"].node) < order.index(p.items["ov"].node) < order.index(p.items["v"].node)


def test_outline_only_shape():
    p = plane()
    run(p.action("shape", {"name": "o", "points": [[0, 0], [1, 0], [1, 1]], "fill": False}))
    style = p.items["o"].node.children[0].style
    assert style.fill is None and style.stroke is not None


def ends(p: Plane, name: str) -> np.ndarray:
    """线段 / 直线画出来的两端（画面坐标）。"""
    return np.round(np.array(p.items[name].node.children[0].path.subpaths[0].points()), 6)[[0, -1]]


def test_segment_and_line_grow_at_playback():
    p = plane()
    anims = p.action("segment", {"name": "s", "from": [0, 0], "to": [2, 1], "label": "s"})
    anims += p.action("line", {"name": "l", "dir": [1, 1]})
    assert p.items["s"].node.children[0].path.is_empty() and p.items["l"].node.children[0].path.is_empty()
    run(anims)
    assert ends(p, "s").tolist() == [[0, 0], [2, 1]]
    assert ends(p, "l").tolist() == [[-1, -1], [4, 4]]  # 过原点的 span 裁在 x [-2, 5]、y [-1, 4] 里
    assert opacity(p.items["s"].label) == 1.0
    # 线段、直线垫在箭头下面
    run(p.action("vector", {"name": "v", "v": [1, 1]}))
    order = [c.name for c in p.layer.children]
    assert order.index("s") < order.index("v") and order.index("l") < order.index("v")


def test_line_by_equation_and_through():
    p = plane()
    run(p.action("line", {"name": "row", "eq": [1, 2, 4]}) + p.action("line", {"name": "x3", "dir": [0, 1], "through": [3, 0]}))
    a, b = ends(p, "row")
    assert sorted([tuple(a), tuple(b)]) == [(-2.0, 3.0), (5.0, -0.5)]  # x + 2y = 4 在范围里的两端
    assert sorted(map(tuple, ends(p, "x3"))) == [(3.0, -1.0), (3.0, 4.0)]


def test_eigenvector_stays_on_its_span_through_a_warp():
    p = plane(x=[-5, 5], y=[-5, 5], mesh=True)
    m = np.array([[3.0, 1.0], [0.0, 2.0]])  # 特征向量 (1, 0)、(1, -1)
    run(p.action("line", {"name": "e", "dir": [1, -1]}) + p.action("vector", {"name": "v", "v": [1, -1]}))
    anims = p.action("warp", {"matrix": m.tolist()})
    for a in anims:
        a.begin()
        for alpha in (0.25, 0.5, 0.75, 1.0):
            a.interpolate(alpha)
            (x0, y0), (x1, y1) = ends(p, "e")
            assert abs((x1 - x0) + (y1 - y0)) < 1e-6  # 直线方向一直是 (1, −1)
            tx, ty = tip(p, "v")
            assert abs(tx + ty) < 1e-6  # 箭头尖一直在直线 y = −x 上
        a.finish()
    # 不是特征向量方向的直线跟着转
    run(p.action("reset", {}))
    run(p.action("line", {"name": "g", "dir": [0, 1]}))
    run(p.action("warp", {"matrix": m.tolist()}))
    (x0, y0), (x1, y1) = ends(p, "g")
    assert abs((x1 - x0) * 2 - (y1 - y0) * 1) < 1e-6  # (0, 1) → (1, 2)


def test_lines_relabel_and_remove():
    p = plane()
    run(p.action("line", {"name": "l", "dir": [1, 0], "label": "L", "dashed": True}))
    run(p.action("relabel", {"name": "l", "label": "M"}))
    assert opacity(p.items["l"].label) == 1.0
    run(p.action("remove", {"name": "l"}))
    assert "l" not in p.items
