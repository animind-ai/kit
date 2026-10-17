"""函数图像组件（graph）的测试：表达式白名单、精确贝塞尔、单侧导数、割线逼近切线。不渲染视频。"""

import math

import numpy as np
import pytest
from scene_spec.expr import ExprError, evaluate, parse

from animind_kit import theme
from animind_kit.mx import graph, widgets
from animind_kit.mx.graph import Function, Graph

T = theme.theme("paper")
BASE = {
    "widget": "graph", "x": [-2, 3], "y": [-1, 5],
    "plots": [{"f": "x^2", "label": "y=x^2"}],
    "points": [{"x": 1, "label": "P"}],
    "secants": [{"point": 0, "h": 1, "label": "Q", "readout": True}, {"point": 0, "h": -1}],
    "tangents": [{"point": 0, "role": "HL"}],
    "lines": [{"x": 1}],
}


@pytest.mark.parametrize("text,x,want", [
    ("x^2", 3, 9), ("2x+1", 2, 5), ("-x^2", 2, -4), ("abs(x)", -2, 2), ("sin(pi/2)", 0, 1),
    ("2^-x", 1, 0.5), ("3(x+1)", 1, 6), ("x**3", 2, 8), ("sqrt(x)/2", 4, 1), ("exp(0)+ln(e)", 0, 2),
])
def test_expression_whitelist_parses_and_evaluates(text, x, want):
    assert math.isclose(evaluate(parse(text), x), want)


@pytest.mark.parametrize("text", [
    "__import__('os')", "x.real", "open(x)", "x; 1", "lambda: 1", "", "sin x", "x +", "y^2", "x[0]",
])
def test_expression_rejects_anything_else(text):
    with pytest.raises(ExprError):
        parse(text)


def test_domain_errors_are_nan():
    assert math.isnan(evaluate(parse("1/x"), 0))
    assert math.isnan(evaluate(parse("sqrt(x)"), -1))
    assert math.isnan(evaluate(parse("x^0.5"), -1))


def test_one_sided_derivatives():
    f = Function("abs(x)")
    assert f.right(0) == 1 and f.left(0) == -1 and math.isnan(f.slope(0))
    g = Function("x^3-2x")
    for x in (-1.5, 0.0, 0.7, 2.0):
        assert math.isclose(g.slope(x), 3 * x * x - 2, abs_tol=1e-12)
    assert math.isclose(Function("sin(x)").slope(1.0), math.cos(1.0))


def _bez(seg, t):
    s = 1 - t
    return (s ** 3 * np.array(seg[0:2]) + 3 * s * s * t * np.array(seg[2:4])
            + 3 * s * t * t * np.array(seg[4:6]) + t ** 3 * np.array(seg[6:8]))


@pytest.mark.parametrize("text", ["x^2", "x^3-x", "abs(x)", "0.5x+1"])
def test_low_degree_polynomials_are_exact(text):
    """三次及以下的多项式（和 |x| 的每一支）一段一个贝塞尔，处处精确：放大多少倍都不会露出折线。"""
    f = Function(text)
    runs = graph.curve_segments(f, -2, 2, -10, 10, [0.0])
    assert len(runs) == 1 and len(runs[0]) <= 8
    for seg in runs[0]:
        for t in np.linspace(0, 1, 41):
            x, y = _bez(seg, t)
            assert abs(y - f(x)) < 1e-12


@pytest.mark.parametrize("text", ["sin(x)", "exp(-x^2)", "x^4", "sqrt(x+2.5)"])
def test_other_functions_within_tolerance(text):
    f = Function(text)
    for seg in graph.curve_segments(f, -2, 2, -10, 10)[0]:
        for t in np.linspace(0, 1, 17):
            x, y = _bez(seg, t)
            assert abs(y - f(x)) < 5 * graph.TOL


def test_corner_is_a_breakpoint():
    """y=|x| 在 0 处不能被一段光滑曲线跨过去，否则放大后尖角会被抹圆。"""
    segs = graph.curve_segments(Function("abs(x)"), -1.3, 1.7, -1, 3, [0.0])[0]
    assert any(abs(s[0]) < 1e-15 for s in segs)


def test_clipped_to_y_range_and_split_at_poles():
    runs = graph.curve_segments(Function("1/x"), -2, 2, -4, 4)
    assert len(runs) == 2
    for run in runs:
        for seg in run:
            assert -4 - 1e-9 <= seg[1] <= 4 + 1e-9 and -4 - 1e-9 <= seg[7] <= 4 + 1e-9


def test_clip_line():
    a, b = graph.clip_line((1, 1), (1, 2), (-2, 3, -1, 5))
    assert np.allclose(a, (0, -1)) and np.allclose(b, (3, 5))
    assert graph.clip_line((10, 10), (0, 1), (-2, 3, -1, 5)) is None


def test_secant_slopes_approach_tangent():
    f = Function("x^2")
    slopes = [graph.secant_slope(f, 1.0, h) for h in (1, 0.5, 0.1, 0.01, -0.01, -1)]
    assert np.allclose(slopes, [3, 2.5, 2.1, 2.01, 1.99, 1])
    ab = Function("abs(x)")
    assert all(graph.secant_slope(ab, 0.0, h) == math.copysign(1, h) for h in (1, 0.1, -0.1, -1e-6))


def test_widget_parts_and_keep_size():
    g = widgets.lookup("graph")("g", BASE, T, {})
    assert isinstance(g, Graph)
    counts = {k: len(v) for k, v in g.built.parts.items()}
    assert counts == {"axes": 1, "plot": 1, "plot_label": 1, "point": 1, "dot": 1, "secant": 2,
                      "qpoint": 2, "slope": 1, "tangent": 1, "line": 1, "triangle": 0}
    assert len(g.built.items) == sum(counts.values()) - counts["dot"]
    p = g.built.parts["point"][0]
    assert p.keep_size and np.allclose(p.matrix[:2, 2], np.array([1.0, 1.0]) * g.u)  # 局部原点就是 P
    assert np.allclose(g.built.parts["dot"][0].bbox(p.matrix).center, np.array([1.0, 1.0]) * g.u)


def test_approach_moves_q_and_secant():
    g = Graph("g", BASE, T, {})
    seg, q = g.built.parts["secant"][0], g.built.parts["qpoint"][0]
    anim = g.action("approach", {"secant": 0, "h": 0.01})[0]
    anim.begin()
    anim.interpolate(0.5)
    assert math.isclose(g.h[0], 0.1, rel_tol=1e-9)  # 几何插值：1 和 0.01 的中点是 0.1
    anim.finish()
    assert g.h[0] == 0.01
    assert np.allclose(q.matrix[:2, 2], np.array([1.01, 1.01 ** 2]) * g.u)
    pts = seg.path.all_points() / g.u
    slope = (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])
    assert math.isclose(slope, 2.01, rel_tol=1e-9)
    assert g.action("draw", {"plot": 0})


def test_tangent_at_corner_is_refused():
    d = {**BASE, "plots": [{"f": "abs(x)"}], "points": [{"x": 0}], "secants": [], "lines": []}
    with pytest.raises(ValueError, match="没有切线"):
        Graph("g", d, T, {})
