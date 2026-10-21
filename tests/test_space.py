"""三维坐标空间 space：裁剪、投影、3×3 变形（含奇异）、投影向量和误差、orbit 重算远近顺序。不渲染视频。"""

from pathlib import Path

import numpy as np
import pytest
import yaml

from animind_kit import theme
from animind_kit.mx.space import Space, clip_line, clip_plane, project_onto

T = theme.theme("paper")
LO, HI = np.array([-3.0, -3.0, -2.0]), np.array([3.0, 3.0, 3.0])
EXAMPLE = Path(__file__).resolve().parents[2] / "scene-spec" / "examples" / "space.scene.yaml"


def space(**extra) -> Space:
    return Space("sp", {"widget": "space", "x": [-3, 3], "y": [-3, 3], "z": [-2, 3], "mesh": True, **extra}, T, {})


def run(anims):
    for a in anims:
        a.begin()
        a.finish()


def test_clip_line_and_plane_stay_in_the_box():
    a, b = clip_line(np.zeros(3), np.array([1.0, 1.0, 1.0]), LO, HI)
    assert np.allclose(a, (-2, -2, -2)) and np.allclose(b, (3, 3, 3))
    assert clip_line(np.array([0.0, 0.0, 5.0]), np.array([1.0, 0.0, 0.0]), LO, HI) is None
    poly = clip_plane(np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), LO, HI)
    assert len(poly) == 4 and np.allclose(poly[:, 2], 0)
    tilted = clip_plane(np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 1.0]), LO, HI)
    assert np.all(tilted >= LO - 1e-9) and np.all(tilted <= HI + 1e-9)
    assert clip_plane(np.zeros(3), np.array([1.0, 0, 0]), np.array([2.0, 0, 0]), LO, HI) is None  # 退化


def test_projection_is_orthogonal_and_draws_a_right_angle():
    s = space()
    run(s.action("plane", {"name": "P", "span": [[1, 0, 0], [0, 1, 0.4]]}))
    run(s.action("vector", {"name": "b", "v": [1, 1.5, 2.5], "label": r"\mathbf b"}))
    run(s.action("project", {"name": "b", "onto": "P", "as": "p", "error": "e", "label": r"\mathbf p"}))
    p, e = s.items["p"].shown["v"], s.items["e"].shown["v"]
    assert np.allclose(p + e, (1, 1.5, 2.5))
    assert abs(e @ np.array([1.0, 0, 0])) < 1e-9 and abs(e @ np.array([0, 1.0, 0.4])) < 1e-9
    assert "p_perp" in s.items
    line = s.items["P"]
    assert np.allclose(project_onto(np.array([2.0, 0, 0]), line), (2, 0, 0))


def test_warp_moves_everything_and_reset_undoes():
    s = space()
    run(s.action("box", {"name": "Q", "edges": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "label": r"\det = 1"}))
    m = np.array([[1.5, 0.5, 0], [0, 1, 0.5], [0, 0, 2]])
    run(s.action("warp", {"matrix": m.tolist()}))
    g = s.items["Q"].shown
    assert abs(np.linalg.det(np.array([g["u"], g["v"], g["w"]]))) == pytest.approx(3)
    assert np.allclose(s.mesh_M, m)
    run(s.action("reset", {}))
    assert np.allclose(s.items["Q"].shown["w"], (0, 0, 1)) and np.allclose(s.M, np.eye(3))


def test_singular_warp_flattens_the_null_space_and_hides_its_label():
    s = space()
    run(s.action("line", {"name": "N", "dir": [1, -1, 0], "label": "N(A)"}))
    run(s.action("box", {"name": "Q", "edges": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}))
    run(s.action("warp", {"matrix": [[1, 1, 0], [1, 1, 0], [0, 0, 1]]}))
    n = s.items["N"]
    assert np.allclose(n.shown["d"], 0) and n.hidden and n.label.style.opacity == 0.0
    fill, solid, hidden = s.items["Q"].node.children
    assert len(fill.path.subpaths) == 0  # 压扁了：没有朝前的面
    assert len(solid.path.subpaths) > 0


def test_box_hides_back_edges_and_orbit_reorders():
    s = space()
    run(s.action("box", {"name": "Q", "edges": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}))
    run(s.action("vector", {"name": "far", "v": [-2, 2, 0]}))
    run(s.action("vector", {"name": "near", "v": [2, -2, 0]}))
    assert len(s.items["Q"].node.children[2].path.subpaths) > 0  # 有虚线棱
    order = [n.name for n in s.layer.children]
    assert order.index("far") < order.index("near")  # 远的先画
    run(s.action("orbit", {"d_azimuth": 180}))
    order = [n.name for n in s.layer.children]
    assert order.index("near") < order.index("far")  # 转到背面之后反过来


def test_plan_opts_reserve_room_for_every_orbit_view():
    doc = yaml.safe_load(EXAMPLE.read_text())
    assert Space.plan_opts(doc, "sp")["views"]
    a = Space("sp", doc["objects"]["sp"], T, {})
    b = Space("sp", doc["objects"]["sp"], T, {}, opts=Space.plan_opts(doc, "sp"))
    assert b.k <= a.k + 1e-9  # 要转的话缩小一点，转动中途不出框
