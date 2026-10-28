"""综合几何图 geometry（构造求点、角标、名字避让、动作）：不渲染视频，只看几何和样式。"""

import math

import numpy as np
import pytest

from animind_kit import theme
from animind_kit.lint import crosses, segments_of
from animind_kit.mx import widgets
from animind_kit.mx.geometry import Geometry, arc_path, clip_line, dash_polyline, default_label
from animind_kit.mx.scan import flatten

T = theme.theme("paper")

FIG = {
    "widget": "geometry",
    "points": {
        "O": [0, 0],
        "A": {"on": "c", "angle": 100},
        "B": {"on": "c", "angle": 215},
        "C": {"on": "c", "angle": 330},
        "D": {"meet": [["A", "O"], "c"], "not": "A", "hidden": True},
        "Ma": {"mid": ["B", "C"]},
    },
    "circles": {"c": {"center": "O", "radius": 2}},
    "draw": [
        {"segment": ["A", "B"]}, {"segment": ["A", "C"]}, {"segment": ["B", "C"]},
        {"segment": ["A", "D"], "aux": True, "hidden": True},
        {"line": ["B", "C"], "name": "bc", "dashed": True},
        {"angle": ["B", "A", "C"], "label": r"\alpha"},
        {"angle": ["A", "B", "D"]},
        {"tick": ["B", "Ma"]}, {"tick": ["Ma", "C"]},
        {"polygon": ["A", "B", "C"], "name": "tri"},
        {"arc": {"circle": "c", "from": "B", "to": "C"}, "role": "A"},
    ],
    "assert": [{"collinear": ["A", "O", "D"]}, {"perpendicular": [["A", "B"], ["B", "D"]]}],
}


def run(anims):
    for a in anims:
        a.begin()
        a.interpolate(0.5)
        a.finish()


def geo(**extra) -> Geometry:
    return Geometry("fig", {**FIG, **extra}, T, {})


def opacity(node) -> float:
    return node.style.opacity if node.style else 1.0


def test_registered():
    assert widgets.lookup("geometry") is Geometry


def test_parts_are_named_and_hidden_ones_start_hidden():
    g = geo()
    assert set(g.built.parts) == {"c", "AB", "AC", "BC", "AD", "bc", "ang_BAC", "ang_ABD", "tick_BMa", "tick_MaC", "tri",
                                  "arc_BC", "O", "A", "B", "C", "D", "Ma"}
    assert opacity(g.parts["AD"].node) == 0.0 and opacity(g.parts["D"].node) == 0.0
    assert opacity(g.parts["AB"].node) == 1.0


def test_layering_puts_fills_under_lines_and_points_on_top():
    g = geo()
    order = [c.name for c in g.node.children[2:]]
    assert order.index("tri") < order.index("ang_BAC") < order.index("c") < order.index("AB") < order.index("A")


def test_aux_line_is_dark_red_and_angle_fill_is_gold():
    g = geo()
    assert g.parts["AD"].strokes[0].style.stroke == T.NO
    assert g.parts["ang_BAC"].fill.style.fill == T.HL and opacity(g.parts["ang_BAC"].fill) == 0.0


def test_right_angle_gets_a_square():
    g = geo()
    path = g.parts["ang_ABD"].strokes[0].path  # ∠ABD 是直径所对的圆周角：自动画方块（两段直线）
    assert len(path.subpaths) == 1 and len(path.subpaths[0].segments) == 2


def test_line_is_clipped_to_the_box():
    g = geo(box=[-3, -3, 3, 3])
    pts = g.parts["bc"].strokes[0].path.all_points()
    half = 3 * g.u
    assert np.all(np.abs(pts) <= half + 1e-6)
    assert np.isclose(np.abs(pts[:, 0]).max(), half)  # 两端落在框上


def test_labels_do_not_cross_lines():
    g = geo()
    a, b = [], []
    for p in g.parts.values():
        for s in p.strokes:
            sa, sb = segments_of(*flatten(s.path, np.eye(3)))
            a.append(sa)
            b.append(sb)
    A, B = np.vstack(a), np.vstack(b)
    for p in g.parts.values():
        if p.label is None:
            continue
        box = p.label.bbox()
        assert not crosses(A, B, (box.x0, box.y0, box.x1, box.y1)), p.el.name


def test_point_label_prefers_given_direction():
    d = {**FIG, "points": {**FIG["points"], "O": {"at": [0, 0], "dir": "dl"}}}
    g = Geometry("fig", d, T, {})
    off = g.parts["O"].offset
    assert off[0] < 0 and off[1] < 0


def test_default_labels_get_subscripts():
    assert default_label("A") == "A" and default_label("Ma") == "M_{a}" and default_label("A1") == "A_{1}"
    assert default_label("AB") == "AB"


def test_failed_assert_refuses_to_build():
    with pytest.raises(ValueError, match="断言不成立"):
        geo(**{"assert": [{"equal": [["A", "B"], ["A", "C"]]}]})


def test_bad_construction_refuses_to_build():
    pts = {**FIG["points"], "D": {"meet": [["A", "O"], "c"]}}
    with pytest.raises(ValueError, match="两个交点"):
        geo(points=pts)


def test_draw_reveals_and_grows():
    g = geo()
    anims = g.action("draw", {"name": ["AD", "D"]})
    assert opacity(g.parts["AD"].node) == 0.0  # 建动作时不改
    run(anims)
    assert opacity(g.parts["AD"].node) == 1.0 and opacity(g.parts["D"].node) == 1.0
    assert len(g.parts["AD"].strokes[0].path.subpaths[0].segments) == 1


def test_shade_ring_and_unshade():
    g = geo()
    run(g.action("shade", {"name": "ang_BAC"}) + g.action("ring", {"name": ["A", "AB"]}))
    assert opacity(g.parts["ang_BAC"].fill) == 1.0
    assert opacity(g.parts["A"].halo) == 1.0 and g.parts["AB"].halo in g.halos.children
    run(g.action("unshade", {"name": "ang_BAC"}) + g.action("unring", {"name": "A"}))
    assert opacity(g.parts["ang_BAC"].fill) == 0.0 and g.parts["A"].halo is None
    with pytest.raises(KeyError):
        g.action("shade", {"name": "AB"})


def test_dim_skips_hidden_parts_and_restore_brings_back():
    g = geo()
    run(g.action("dim", {"except": ["AB", "A", "B"]}))
    assert opacity(g.parts["AC"].node) == pytest.approx(0.25)
    assert opacity(g.parts["AD"].node) == 0.0  # 没露出来的不会被调出来
    assert opacity(g.parts["AB"].node) == 1.0
    run(g.action("restore", {}))
    assert opacity(g.parts["AC"].node) == 1.0 and opacity(g.parts["AD"].node) == 0.0


def test_highlight_and_focus_part():
    g = geo()
    run(g.action("highlight", {"name": "AB"}))
    assert g.parts["AB"].strokes[0].style.stroke.lower() == T.HL_INK.lower()
    run(g.focus_part("AC"))
    assert g.parts["AC"].strokes[0].style.stroke.lower() == T.HL_INK.lower()
    assert opacity(g.parts["AB"].node) == pytest.approx(0.3)
    run(g.focus(None))
    assert opacity(g.parts["AB"].node) == 1.0 and g.parts["AB"].strokes[0].style.stroke.lower() == T.FG.lower()


def test_trace_moves_dependents_and_keeps_label_offset():
    g = geo()
    off = g.parts["A"].offset.copy()
    run(g.action("trace", {"point": "A", "to": 150}))
    a = g.W((2 * math.cos(math.radians(150)), 2 * math.sin(math.radians(150))))
    assert np.allclose(g.parts["A"].dots[0].matrix[:2, 2], a)
    assert np.allclose(g.parts["D"].dots[0].matrix[:2, 2], -a + 2 * g.W((0, 0)))  # D 跟着变成 A 的对径点
    assert np.allclose(g.parts["A"].label.matrix[:2, 2], a + off)
    with pytest.raises(KeyError):
        g.action("trace", {"point": "D", "to": 1})


def test_figure_keeps_size_after_actions():
    g = geo()
    before = g.node.bbox()
    run(g.action("draw", {"name": "AD"}) + g.action("trace", {"point": "C", "to": 300}))
    after = g.node.bbox()
    assert np.allclose([before.x0, before.y0, before.x1, before.y1], [after.x0, after.y0, after.x1, after.y1])


def test_helpers():
    p = arc_path(np.zeros(2), 1.0, 0.0, math.pi)
    assert np.allclose(p.all_points()[-1], [-1, 0])
    d = dash_polyline([np.zeros(2), np.array([1.0, 0.0])], 0.2, 0.1)
    assert len(d.subpaths) == 4
    seg = clip_line(np.zeros(2), np.array([1.0, 0.0]), (-1, -1, 2, 1), ray=True)
    assert np.allclose(seg[0], [0, 0]) and np.allclose(seg[1], [2, 0])
