"""随机试验组件（频率折线、高尔顿板、样本空间方格）：不渲染视频，只看几何、样式和推演的状态。"""

import numpy as np

from animind_kit import theme
from animind_kit.mx import widgets
from animind_kit.mx.chance import PER_ROW, CoinFlips, Galton, Outcomes

T = theme.theme("paper")


def run(anims):
    for a in anims:
        a.begin()
        a.finish()


def opacity(node) -> float:
    return node.style.opacity if node.style else 1.0


def test_registered():
    assert widgets.lookup("coin_flips") is CoinFlips
    assert widgets.lookup("galton") is Galton
    assert widgets.lookup("outcomes") is Outcomes


def test_flip_draws_the_line_up_to_k_at_playback():
    c = CoinFlips("c", {"n": 1000, "seed": 8770, "runs": 2}, T, {})
    anims = c.action("flip", {"to": 10})
    assert c.line.path.is_empty() and opacity(c.line) == 0.0  # 建动作时还没画
    run(anims)
    pts = c.line.path.all_points()
    assert np.isclose(pts[-1][1], 0.7 * c.H)  # 前十次 7 个正面
    assert opacity(c.readout.children[-1]) == 1.0
    run(c.action("flip", {"to": 1000}))
    assert np.isclose(c.line.path.all_points()[-1][1], 0.498 * c.H)


def test_runs_and_band():
    c = CoinFlips("c", {"n": 1000, "seed": 8770, "runs": 2}, T, {})
    run(c.action("runs", {"count": 2}) + c.action("band", {}))
    assert len(c.runs.children) == 2 and all(opacity(r) == 1.0 for r in c.runs.children)
    assert opacity(c.band) == 1.0 and not c.band.path.is_empty()


def test_galton_balls_land_in_their_slots():
    g = Galton("g", {"rows": 6, "balls": 128, "seed": 3814}, T, {})
    anims = g.action("drop", {"count": 128})
    assert g.counts == [3, 12, 31, 39, 30, 11, 2]
    assert all(opacity(b) == 0.0 for b in g.balls_layer.children)  # 建动作时还在漏斗上方、看不见
    run(anims)
    first = g.balls_layer.children[0]
    assert np.allclose(first.matrix[:2, 2], g.slot(2, 0))  # 第一个球往右两次，落进第 2 格最底下
    tallest = max(b.matrix[1, 2] for b in g.balls_layer.children)
    assert tallest <= g.slot(3, 38)[1] + 1e-9 and 38 // PER_ROW < 10


def test_galton_marks_and_erase():
    g = Galton("g", {"rows": 6, "balls": 20, "seed": 3814}, T, {})
    run(g.action("drop", {"count": 5}) + g.action("path", {"ball": 0}) + g.action("pascal", {}) + g.action("flat", {}))
    assert len(g.marks.children) == 1 + 7 + 1 and all(opacity(m) == 1.0 for m in g.marks.children)
    run(g.action("erase", {}))
    assert all(opacity(m) == 0.0 for m in g.marks.children)


def test_outcomes_unshade_and_restore_in_one_batch_do_not_fight():
    o = Outcomes("o", {"rows": 2, "cols": 2, "labels": ["男男", "男女", "女男", "女女"]}, T, {})
    run(o.action("shade", {"cells": ["男女"], "role": "C"}) + o.action("strike", {"cells": ["男女"]}))
    assert o.pads[1].style.fill == T.C
    batch = o.action("unshade", {"cells": ["男女"]}) + o.action("restore", {})
    run(batch)  # 同一批：后建的 restore 带着 unshade 的结果，垫色回到卡片色、不透明
    assert o.pads[1].style.fill == T.CARD and o.pads[1].style.fill_opacity == 1.0
    assert opacity(o.crosses[1]) == 0.0


def test_outcomes_frame_traces_the_outline_only():
    o = Outcomes("o", {"rows": 2, "cols": 2, "labels": ["男男", "男女", "女男", "女女"]}, T, {})
    run(o.action("frame", {"cells": ["男男", "男女", "女男"]}))
    segs = len(o.frame.children[-1].path.subpaths)
    assert segs == 8  # L 形三格：12 条边减去两条共用边各两次
