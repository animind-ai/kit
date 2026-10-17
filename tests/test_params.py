"""场景参数：graph 的参数表达式、readout 读数、tween 动作、补间中途的版式检查。"""

import math

import numpy as np
import pytest
from moxian.anim import Param, ParamTween, Timeline, easing

from animind_kit import theme
from animind_kit.backends.moxian_backend import DEFAULT_DUR, MoxianCompiler
from animind_kit.mx import widgets
from animind_kit.mx.readout import Readout, format_value, trim

T = theme.theme("paper")
GRAPH = {
    "widget": "graph", "x": [-2, 4], "y": [-3, 6],
    "plots": [{"f": "kx+1", "label": "y=kx+1"}],
    "points": [{"x": "t", "label": "P"}],
    "triangles": [{"point": 0, "run": 1}],
    "lines": [{"through": [0, 1], "slope": "k"}],
    "tangents": [{"point": 0}],
}


def params(**values) -> dict[str, Param]:
    return {k: Param(v, k) for k, v in values.items()}


def test_format_value():
    assert format_value(0.5) == ("text", "0.50")
    assert format_value(-0.001) == ("text", "0.00")  # 不显示 −0.00
    assert format_value(-1.25, 1) == ("text", "−1.2")
    assert format_value(0.5, style="fraction") == ("tex", r"\frac{1}{2}")
    assert format_value(-2 / 3, style="fraction") == ("tex", r"-\frac{2}{3}")
    assert format_value(3.0, style="fraction") == ("text", "3")
    assert format_value(math.pi, style="fraction") == ("text", "3.14")
    assert format_value(math.nan)[1] == "—"
    assert trim(2.5) == "2.5" and trim(2.0) == "2" and trim(-0.25) == "−0.25"


def test_graph_follows_params():
    p = params(k=0.5, t=1.0)
    g = widgets.lookup("graph")("g", GRAPH, T, {}, p)
    u = g.u
    marker = g.built.parts["point"][0]
    assert np.allclose(marker.matrix[:2, 2], np.array([1.0, 1.5]) * u)
    tri = g.built.parts["triangle"][0]
    rise = tri.children[1].path.bbox()
    assert rise[3] - rise[1] == pytest.approx(0.5 * u)
    curve = g.built.parts["plot"][0]
    old_path = curve.path
    p["k"].set(2.0)
    p["t"].set(0.5)
    assert np.allclose(marker.matrix[:2, 2], np.array([0.5, 2.0]) * u)
    assert tri.children[1].path.bbox()[3] - tri.children[1].path.bbox()[1] == pytest.approx(2.0 * u)
    assert curve.path is not old_path  # 换了新路径对象：渲染器按对象缓存
    x0, y0, x1, y1 = curve.path.bbox()
    assert y1 == pytest.approx(6 * u, abs=1e-6)  # y=2x+1 在 y=6 处出框，曲线截到上沿
    line = g.built.parts["line"][0].path.bbox()
    assert (line[3] - line[1]) / (line[2] - line[0]) == pytest.approx(2.0)
    tangent = g.built.parts["tangent"][0].path.bbox()
    assert (tangent[3] - tangent[1]) / (tangent[2] - tangent[0]) == pytest.approx(2.0)


def test_static_graph_has_no_updater():
    g = widgets.lookup("graph")("g", {**GRAPH, "plots": [{"f": "x^2"}], "points": [{"x": 1}],
                                      "lines": [{"x": 1}]}, T, {}, {})
    assert g.refreshers == [] and g.used == set()


def test_readout_rebuilds():
    p = params(k=1.0)
    r = Readout({"type": "readout", "label": "$k$ =", "value": "k/2", "format": "fraction"}, T, p, 36)
    first = r.node.children[1]
    w0 = r.node.width
    p["k"].set(4.0)  # 1/2 → 2：分数变成整数
    assert r.node.children[1] is not first and r.node.width != w0
    assert r.node.children[0] is first or r.node.children[0] is r.label
    # 左端固定：label 不动
    assert r.node.children[0].bbox().x0 == pytest.approx(0.0, abs=1e-9)


DOC = {
    "spec": "0.1",
    "meta": {"id": "t", "title": "t", "line": "worked", "author": "a", "license": "MIT"},
    "pedagogy": {"insight": "i", "status": "demo"},
    "params": {"k": 0},
    "objects": {
        "big": {"type": "readout", "label": "值", "value": "10^k", "digits": 0, "in": "stage"},
    },
    "beats": [{"id": "B", "do": [
        {"at": 0, "act": "show", "target": "big"},
        {"after": "prev", "act": "tween", "args": {"k": 100}, "dur": 1},
        {"after": "prev", "act": "tween", "args": {"k": 0}, "dur": 1, "ease": "linear"},
    ]}],
}


def test_tween_action_compiles_to_param_tweens():
    c = MoxianCompiler(DOC, T, None, log=lambda *_: None)
    anims, dur = c.animations({"act": "tween", "args": {"k": 2}, "ease": "linear"})
    assert dur == DEFAULT_DUR["tween"] and len(anims) == 1
    assert isinstance(anims[0], ParamTween) and anims[0].param is c.params["k"] and anims[0].ease is easing.linear


def test_layout_checked_while_params_move(tmp_path):
    """读数在补间中途变得很宽、出了画面，结束时又缩回去：结束帧没问题，中途的问题也要报出来。

    补间到 100，最先越界的采样点是中点 k=50（51 位数），远超边距，不受各平台字形宽度差异影响。
    """
    c = MoxianCompiler(DOC, T, None, log=lambda *_: None, quality="l")
    c.render_beat(DOC["beats"][0], tmp_path / "B.mp4")
    assert c.params["k"].value == 0.0
    assert len(c.problems) == 1 and "超出安全边距" in c.problems[0] and "k=50" in c.problems[0]


def test_layout_sampling_restores_params():
    c = MoxianCompiler(DOC, T, None, log=lambda *_: None)
    tl = Timeline()
    tl.add(0.0, ParamTween(c.params["k"], 1.0, 1.0))
    tl.seek(2.0)
    c._param_layout(tl, [(-1.0, {})], set(), set())
    assert c.params["k"].value == 1.0
