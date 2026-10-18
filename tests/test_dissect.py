"""剖分组件 dissect（墨线）：动作推演出的位姿、动画终点、标签检查。不渲染视频。"""

import numpy as np
import pytest

from animind_kit import theme
from animind_kit.mx import widgets
from animind_kit.mx.dissect import Dissect, apply

T = theme.theme("paper")

PARA = {
    "widget": "dissect", "in": "stage",
    "shapes": {"P": {"polygon": [[0, 0], [6, 0], [9, 3], [3, 3]],
                     "edges": [{"at": [[0, 0], [6, 0]], "text": "$a$"}]}},
    "cuts": {"c1": {"piece": "P", "line": [[3, 3], [3, 0]], "into": ["T", "Q"]}},
    "pieces": {"T": {"label": "$S_1$"}, "Q": {"label": "$S_2$"}},
    "guides": {"h": {"from": [3, 3], "to": [3, 0], "label": "$h$"}},
    "merges": {"R": {"pieces": ["T", "Q"], "label": "$ah$"}},
    "copies": {"T2": "T"},
}


def make(definition=PARA) -> Dissect:
    return Dissect("para", definition, T, {})


def play(anims) -> None:
    for a in anims:
        a.begin()
        for alpha in np.linspace(0, 1, 7):
            a.interpolate(alpha)
        a.finish()


def world(w: Dissect, name: str):
    """块的顶点在图形坐标里的位置（按节点矩阵，即播放后的实际位置）。"""
    piece = w.plan.pieces[name]
    return [tuple(np.round(apply(w.nodes[name].matrix, p), 6)) for p in piece.points]


def test_registered():
    assert widgets.lookup("dissect") is Dissect


def test_cut_then_snap_makes_rectangle():
    w = make()
    play(w.action("cut", {"cut": "c1"}))
    assert w.nodes["P"].style.opacity == 0 and w.nodes["T"].style.opacity == 1
    play(w.action("snap", {"piece": "T", "edge": [[0, 0], [3, 3]], "to": "Q", "to_edge": [[6, 0], [9, 3]]}))
    assert set(world(w, "T")) == {(6, 0), (9, 0), (9, 3)}
    np.testing.assert_allclose(w.nodes["T"].matrix, w.pose["T"])  # 动画终点 = 编译时推演的位姿


def test_rotate_and_flip_land_on_pose():
    w = make()
    play(w.action("cut", {"cut": "c1"}))
    play(w.action("rotate", {"piece": "T", "angle": 180, "about": {"vertex": [3, 3]}}))
    assert set(world(w, "T")) == {(6, 6), (3, 6), (3, 3)}
    play(w.action("flip", {"piece": "T", "axis": [[0, 4.5], [1, 4.5]]}))
    assert set(world(w, "T")) == {(6, 3), (3, 3), (3, 6)}
    np.testing.assert_allclose(w.nodes["T"].matrix, w.pose["T"], atol=1e-9)


def test_shift_by_and_to():
    w = make()
    play(w.action("cut", {"cut": "c1"}))
    play(w.action("shift", {"piece": "T", "by": [1, 2]}))
    assert (1, 2) in world(w, "T")
    play(w.action("shift", {"piece": "T", "to": [0, 0]}))
    np.testing.assert_allclose(apply(w.nodes["T"].matrix, w.plan.pieces["T"].center()), [0, 0], atol=1e-9)


def test_labels_stay_upright_and_same_size():
    w = make()
    play(w.action("cut", {"cut": "c1"}))
    label = w.labels["T"][0].node
    before = (w.nodes["T"].matrix @ label.matrix)[:2, :2].copy()
    play(w.action("rotate", {"piece": "T", "angle": 137}))
    after = (w.nodes["T"].matrix @ label.matrix)[:2, :2]
    np.testing.assert_allclose(after, before, atol=1e-9)


def test_copy_starts_on_source_and_items_track_alive_pieces():
    w = make()
    assert w.built.items == [w.nodes["P"]]
    play(w.action("cut", {"cut": "c1"}))
    assert w.nodes["P"] not in w.built.items and w.nodes["T"] in w.built.items
    play(w.action("copy", {"piece": "T2", "by": [0, -4]}))
    assert set(world(w, "T2")) == {(0, -4), (3, -4), (3, -1)}
    assert w.nodes["T2"] in w.built.items


def test_merge_outline_is_rectangle_perimeter():
    w = make()
    play(w.action("cut", {"cut": "c1"}))
    play(w.action("snap", {"piece": "T", "vertex": [0, 0], "to": "Q", "to_vertex": [6, 0]}))
    play(w.action("merge", {"name": "R"}))
    path = w.merge_nodes["R"].children[0].path
    length = sum(np.linalg.norm(sp.end() - sp.start) for sp in path.subpaths)
    assert length == pytest.approx(18)


def test_end_problems_reports_overlapping_labels():
    w = make()
    play(w.action("cut", {"cut": "c1"}))
    w.node.set_style(opacity=1.0)
    assert w.end_problems() == []
    play(w.action("copy", {"piece": "T2"}))  # 复制品停在原块上：两个 S₁ 标签重叠
    assert any("T" in p and "T2" in p for p in w.end_problems())
    play(w.action("labels", {"piece": "T2", "show": False}))
    assert w.end_problems() == []


@pytest.mark.parametrize("n", [8, 16, 64])
def test_sectors_arrange(n):
    w = make({"widget": "dissect", "shapes": {"O": {"circle": 2}},
              "cuts": {"c": {"piece": "O", "sectors": n, "into": "s"}}})
    play(w.action("cut", {"cut": "c"}))
    play(w.action("arrange", {"cut": "c", "at": [0, 0]}))
    for i in range(n):
        np.testing.assert_allclose(w.nodes[f"s{i}"].matrix, w.pose[f"s{i}"], atol=1e-9)
    tips = [apply(w.pose[f"s{i}"], (0, 0))[1] for i in range(n)]
    assert all(t > 0 for t in tips[::2]) and all(t < 0 for t in tips[1::2])  # 尖一上一下交错


def test_bad_definition_raises_with_code():
    bad = {**PARA, "cuts": {"c1": {"piece": "P", "line": [[20, 0], [20, 3]], "into": ["T", "Q"]}}}
    with pytest.raises(ValueError, match="SS423"):
        make(bad)


# ---------- fold ----------

R2 = np.sqrt(2)
TRI = {
    "widget": "dissect", "in": "stage",
    "shapes": {"T": {"polygon": [[0, 0], [5, 0], [0, 5]]}},
    "cuts": {"f1": {"piece": "T", "line": [[0, 5], [5 * (R2 - 1), 0]], "into": ["flap", "rest"]}},
    "guides": {"g": {"from": [0, 0], "to": [5, 0]}},
}


def test_fold_lands_leg_on_hypotenuse():
    w = make(TRI)
    play(w.action("fold", {"cut": "f1", "flap": "flap"}))
    assert w.nodes["T"].style.opacity == 0 and w.nodes["flap"].style.opacity == 1
    d = 5 * (R2 - 1)
    e = (5 / R2, 5 - 5 / R2)  # 直角顶点落在斜边上、离 A 为 5 的地方
    got = world(w, "flap")
    for want in [(0, 5), (d, 0), e]:
        assert any(np.allclose(want, g, atol=1e-6) for g in got)
    np.testing.assert_allclose(w.nodes["flap"].matrix, w.pose["flap"])
    np.testing.assert_allclose(w.nodes["rest"].matrix, np.eye(3))
    assert np.linalg.det(w.pose["flap"][:2, :2]) == pytest.approx(-1)


def test_fold_shades_mid_turn_and_ends_on_back_colour():
    w = make(TRI)
    [anim] = w.action("fold", {"cut": "f1", "flap": "flap", "back": "B"})
    shape = w.shapes["flap"][0]
    front = shape.style.fill
    anim.begin()
    anim.interpolate(0.21)  # 切开了，刚开始翻
    anim.interpolate(0.4)
    mid = shape.style.fill
    assert mid not in (front, T.B)  # 翻到一半：混了黑色
    anim.finish()
    assert shape.style.fill == T.B and shape.style.stroke == T.B
    # 翻过来的块在最上层，但压不过参考线
    assert w.nodes["flap"].z > w.nodes["rest"].z
    assert w.guide_nodes["g"].z > w.nodes["flap"].z


def test_fold_back_returns_flap_and_fold_after_cut_uses_current_pose():
    w = make(TRI)
    play(w.action("fold", {"cut": "f1", "flap": "flap"}))
    play(w.action("fold", {"cut": "f1", "flap": "flap"}))
    np.testing.assert_allclose(w.nodes["flap"].matrix, np.eye(3), atol=1e-9)
    w2 = make(TRI)
    play(w2.action("cut", {"cut": "f1"}))
    play(w2.action("shift", {"piece": "flap", "by": [-2, 0]}))
    play(w2.action("fold", {"cut": "f1", "flap": "flap"}))
    # 折痕跟着块走：翻完以后 A 点和 D 点还在原地（各挪了 -2）
    got = world(w2, "flap")
    for want in [(-2, 5), (5 * (R2 - 1) - 2, 0)]:
        assert any(np.allclose(want, g, atol=1e-6) for g in got)
