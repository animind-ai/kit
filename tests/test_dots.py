"""十格框、点阵、area_grid 的 rect 模式（数的世界入门 #1–#5）：不渲染视频，只看节点的位置和样式。"""

import numpy as np
import pytest

from animind_kit import theme
from animind_kit.mx import widgets
from animind_kit.mx.dots import PITCH, DotArray, TenFrame, grid_pos, rotated
from animind_kit.mx.grid import AreaGrid

T = theme.theme("paper")


def run(anims):
    for a in anims:
        a.begin()
        a.finish()


def xy(node) -> tuple[float, float]:
    return round(float(node.matrix[0, 2]), 6), round(float(node.matrix[1, 2]), 6)


def opacity(node) -> float:
    return (node.style.opacity if node.style else 1.0)


def test_registered():
    assert widgets.lookup("ten_frame") is TenFrame and widgets.lookup("dot_array") is DotArray


# ---------- ten_frame ----------

def frame(side: int = 5) -> TenFrame:
    return TenFrame("tf", {"widget": "ten_frame", "side": side}, T, {})


def test_make_ten_eight_plus_five():
    tf = frame()
    fill, side = tf.action("fill", {"count": 8}), tf.action("side", {"count": 5})
    hop = tf.action("move_in", {"count": 2})
    # 建动作时什么都还没出现、没动
    assert all(opacity(d) == 0.0 for d in tf.dots.children)
    run(fill + side + hop)
    assert all(opacity(d) == 1.0 for d in tf.dots.children)
    assert all(s is not None for s in tf.slots)  # 框满了：一个十
    assert len(tf.side_dots) == 3  # 剩下三个在旁边
    # 跳进去的两个落在下排最后两格，颜色还是 B
    assert [xy(d) for d in tf.slots[8:]] == [tuple(np.round(tf.cell_pos[i], 6)) for i in (8, 9)]
    assert all(d.style.fill == T.B for d in tf.slots[8:]) and all(d.style.fill == T.A for d in tf.slots[:8])
    # 剩下的往左靠拢到前三个位置
    assert [xy(d) for d in tf.side_dots] == [tuple(np.round(tf.side_pos(k), 6)) for k in range(3)]


def test_count_labels_follow_the_order_and_go_away_on_move():
    tf = frame()
    run(tf.action("fill", {"count": 8}) + tf.action("side", {"count": 5}))
    run(tf.action("count", {"start": 9}))
    labels = list(tf.count_labels.values())
    assert len(labels) == 5 and all(opacity(lab) == 1.0 for lab in labels)
    run(tf.action("move_in", {"count": 2}))
    assert tf.count_labels == {} and all(opacity(lab) == 0.0 for lab in labels)


def test_recolor_and_remove_happen_at_playback():
    tf = frame()
    run(tf.action("fill", {"count": 8}) + tf.action("side", {"count": 2}) + tf.action("move_in", {"count": 2}))
    recolor = tf.action("recolor", {"which": "frame", "role": "HL"})
    remove = tf.action("remove", {"which": "frame"})
    assert tf.slots == [None] * 10
    dots = [d for d in tf.dots.children]
    assert {d.style.fill for d in dots} == {T.A, T.B} and all(opacity(d) == 1.0 for d in dots)
    run(recolor)
    assert {d.style.fill for d in dots} == {T.HL}
    run(remove)
    assert all(opacity(d) == 0.0 for d in dots)
    run(tf.action("fill", {"count": 3}))  # 收掉以后从头再放
    assert sum(s is not None for s in tf.slots) == 3


def test_reserve_does_not_change_with_actions():
    tf = frame()
    before = tf.node.bbox()
    run(tf.action("fill", {"count": 8}) + tf.action("side", {"count": 5}) + tf.action("count", {}))
    after = tf.node.bbox()
    assert (after.x0, after.x1) == pytest.approx((before.x0, before.x1))


# ---------- dot_array ----------

def array(rows: int, cols: int, **extra) -> DotArray:
    return DotArray("da", {"widget": "dot_array", "rows": rows, "cols": cols, **extra}, T, {})


@pytest.mark.parametrize("ccw", [True, False])
def test_rotation_map_is_a_quarter_turn(ccw):
    R, C = 3, 4
    angle = np.pi / 2 if ccw else -np.pi / 2
    rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    seen = set()
    for r in range(R):
        for c in range(C):
            r2, c2 = rotated(r, c, R, C, ccw)
            assert 0 <= r2 < C and 0 <= c2 < R
            assert np.allclose(rot @ grid_pos(r, c, R, C), grid_pos(r2, c2, C, R))
            seen.add((r2, c2))
    assert len(seen) == R * C  # 一个不多、一个不少


@pytest.mark.parametrize("direction", ["ccw", "cw"])
def test_rotate_three_by_four_into_four_by_three(direction):
    da = array(3, 4, room=[4, 4])
    nodes = {id(s.node) for s in da.grid.values()}
    anims = da.action("rotate", {"dir": direction})
    assert (da.rows, da.cols) == (4, 3)
    run(anims)
    assert {id(s.node) for s in da.grid.values()} == nodes  # 同一批点
    for (r, c), slot in da.grid.items():
        assert xy(slot.node) == pytest.approx(tuple(grid_pos(r, c, 4, 3)), abs=1e-6)


def test_rotate_moves_along_an_arc():
    da = array(2, 5)
    slot = da.grid[0, 0]
    start = np.array(xy(slot.node))
    (anim,) = [a for a in da.action("rotate", {}) if a.node is slot.node]
    anim.begin()
    anim.interpolate(0.5)
    assert np.linalg.norm(xy(slot.node)) == pytest.approx(np.linalg.norm(start))  # 离中心一样远


def test_extend_to_ten_then_fade_last_column():
    da = array(4, 3, room=[4, 10])
    old = {k: s for k, s in da.grid.items()}
    extend = da.action("extend", {"cols": 10, "role": "B"})
    fade = da.action("fade_col", {})
    assert (da.rows, da.cols) == (4, 10) and len(da.grid) == 40
    # 建动作时：新点还看不见，最后一列还没淡
    assert all(opacity(s.node) == 0.0 for k, s in da.grid.items() if k not in old)
    assert all(opacity(da.grid[r, 9].dot) == 1.0 for r in range(4))
    run(extend + fade)
    for (r, c), slot in da.grid.items():
        assert xy(slot.node) == pytest.approx(tuple(grid_pos(r, c, 4, 10)), abs=1e-6)
        assert opacity(slot.node) == 1.0
    assert all(da.grid[r, 9].dot.style.fill == T.NO and opacity(da.grid[r, 9].dot) == pytest.approx(0.3) for r in range(4))
    assert sum(1 for s in da.grid.values() if not s.faded) == 36  # 4 × 10 少一列 = 4 × 9
    assert {da.grid[r, c].dot.style.fill for r in range(4) for c in range(3, 9)} == {T.B}


def test_new_rows_inherit_the_faded_column():
    da = array(1, 10, room=[6, 10])
    run(da.action("fade_col", {"col": -1}))
    run(da.action("extend", {"rows": 3}))
    assert [da.grid[r, 9].faded for r in range(3)] == [True, True, True]
    assert opacity(da.grid[2, 9].dot) == pytest.approx(0.3) and not da.grid[2, 8].faded


def test_squares_turn_the_array_into_a_rectangle():
    da = array(4, 8)
    assert all(opacity(s.cell) == 0.0 for s in da.grid.values())
    anims = da.action("squares", {"fill": "A", "dots": False})
    assert all(opacity(s.cell) == 0.0 for s in da.grid.values())  # 播放时才画
    run(anims)
    assert all(opacity(s.cell) == 1.0 and opacity(s.dot) == 0.0 for s in da.grid.values())
    box = da.dots.bbox()
    assert (box.width, box.height) == pytest.approx((8 * PITCH, 4 * PITCH), abs=0.02)


def test_cells_param_shows_the_ten_frame_look():
    da = array(2, 5, cells=True)
    assert all(opacity(s.cell) == 1.0 for s in da.grid.values())


# ---------- area_grid rect 模式 ----------

def rect_grid(cols: int, rows: int, **extra) -> AreaGrid:
    return AreaGrid("g", {"widget": "area_grid", "cols": cols, "rows": rows, "rect": True, **extra}, T, {})


def test_fraction_grid_is_unchanged():
    g = AreaGrid("sq", {"widget": "area_grid", "cols": 4, "rows": 3}, T, {})
    assert not g.rect_mode and g.node.bbox().width == pytest.approx(g.SIDE, abs=0.05)
    with pytest.raises(KeyError):
        g.action("split", {"cols": [2, 2]})


def test_rect_cells_are_square():
    g = rect_grid(8, 4)
    assert len(g.cells) == 32
    assert (g.W, g.H) == pytest.approx((8 * PITCH, 4 * PITCH))
    with pytest.raises(KeyError):
        g.action("shade", {"cols": 2})


def test_split_into_four_blocks_and_label_areas():
    g = rect_grid(23, 14, lines=False)
    assert g.u < PITCH and g.W <= g.MAX_W + 1e-9 and g.H <= g.MAX_H + 1e-9  # 格子多时每格缩小，整块大小有上限
    assert g.W / g.H == pytest.approx(23 / 14)
    run(g.action("split", {"cols": [20, 3], "rows": [10, 4]}))
    assert [mx_tex(lab) for lab in g.top_labels] == ["20", "3"] and [mx_tex(lab) for lab in g.left_labels] == ["10", "4"]
    assert g.block_of(0, 19) == (0, 0) and g.block_of(0, 20) == (1, 0) and g.block_of(10, 22) == (1, 1)
    run(g.action("fill", {"label": True}))
    areas = sorted(int(mx_tex(lab)) for lab in g.areas.children[1::2])
    assert areas == [12, 30, 80, 200] and sum(areas) == 23 * 14


def test_apart_and_join_round_trip():
    g = rect_grid(8, 4)
    run(g.action("split", {"cols": [5, 3]}))
    home = [xy(c) for c in g.cells]
    run(g.action("fill", {"col": 0, "label": True, "role": "A"}) + g.action("fill", {"col": 1, "label": True, "role": "B"}))
    run(g.action("apart", {"gap": 0.4}))
    left, right = xy(g.cells[0]), xy(g.cells[7])
    assert left[0] == pytest.approx(-0.2) and right[0] == pytest.approx(0.2)  # 两块各往外 0.2
    assert opacity(g.outline) == 0.0 and all(opacity(f) == 1.0 for f in g.block_frames.values())
    label = g.areas.children[3]
    cx, _cy, _w, _h = g.block_box(1, 0)
    assert label.center()[0] == pytest.approx(cx + 0.2, abs=1e-3)  # 块里的数跟着块走
    run(g.action("join", {}))
    assert label.center()[0] == pytest.approx(cx, abs=1e-3)
    assert [xy(c) for c in g.cells] == pytest.approx(home)
    assert opacity(g.outline) == 1.0 and all(opacity(f) == 0.0 for f in g.block_frames.values())


def test_fill_cells_for_the_wrong_answer_and_unfill():
    g = rect_grid(8, 4)
    run(g.action("split", {"cols": [5, 3]}))
    anims = g.action("fill", {"col": 1, "role": "B"}) + g.action("fill", {"cells": "0:5", "role": "NO"})
    assert all(opacity(f) == 0.0 for f in g.fills)  # 播放时才涂
    run(anims)
    filled = [k for k, f in enumerate(g.fills) if opacity(f) > 0]
    (block,) = g.block_fill.children
    assert len(filled) == 5 and opacity(block) == pytest.approx(0.45)  # 17：右边 3 × 4 一块，加 5 个散格子
    assert (block.bbox().width, block.bbox().height) == pytest.approx((3 * PITCH, 4 * PITCH))
    run(g.action("unfill", {}))
    assert all(opacity(f) == 0.0 for f in g.fills) and opacity(block) == 0.0


def mx_tex(node) -> str:
    return node.tex
