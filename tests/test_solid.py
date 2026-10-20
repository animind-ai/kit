"""立体组件 solid（洞察 #14 圆锥体积）：投影、消隐、截面、剖成三块、set 换颜色。不渲染视频，只看节点和几何。"""

import math

import numpy as np
import pytest
from moxian.anim import together
from scene_spec.selectors import parse

from animind_kit import theme
from animind_kit.backends.moxian_backend import MoxianCompiler, _FollowTimeline
from animind_kit.mx import widgets
from animind_kit.mx.solid import Camera, Solid, box, cube_pieces, make_shape

T = theme.theme("paper")


def run(anims):
    for a in anims:
        a.begin()
        a.finish()


def opacity(node) -> float:
    return node.style.opacity if node.style else 1.0


def solid(**d) -> Solid:
    return Solid("w", {"widget": "solid", **d}, T, {})


def volume(shape) -> float:
    """凸多面体的体积：以重心为顶点的锥体加起来。"""
    v, c = shape.verts, shape.verts.mean(axis=0)
    total = 0.0
    for f in shape.faces:
        for i in range(1, len(f) - 1):
            total += abs(np.dot(v[f[0]] - c, np.cross(v[f[i]] - c, v[f[i + 1]] - c))) / 6
    return total


def area(pts) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def test_registered():
    assert widgets.lookup("solid") is Solid


def test_camera_is_orthonormal_and_avoids_the_isometric_overlap():
    cam = Camera(30, 20)
    m = np.stack([cam.R, cam.U, cam.V])
    assert np.allclose(m @ m.T, np.eye(3))
    assert np.allclose(np.cross(cam.R, cam.U), cam.V)
    # 严格等轴测时正方体的前上角和后下角投到同一点；默认视角下分得开
    assert np.linalg.norm(cam.p([0, 0, 0]) - cam.p([1, 1, 1])) > 0.3


def test_cube_shows_three_faces_and_three_dashed_edges():
    s = solid(bodies={"C": {"cube": 2}})
    b = s.bodies["C"]
    assert len(b.faces) == 6 and sum(not f.path.is_empty() for f in b.faces) == 3  # 每个面一个节点，画出来的是前、右、上
    hidden = b.lines[1].path
    assert not hidden.is_empty() and len(hidden.subpaths) > 3  # 虚线：每条棱好几段
    assert b.lines[1].style.stroke_opacity < 1 and b.lines[0].style.stroke_opacity == 1


def test_three_pieces_are_congruent_and_fill_the_cube():
    pieces = cube_pieces(2.0)
    vols = [volume(p) for p, _ in pieces]
    assert vols == pytest.approx([8 / 3] * 3)
    assert sum(vols) == pytest.approx(volume(box(2, 2, 2)))
    # 全等：每块的棱长排序后一样
    def edge_lengths(p):
        edges = {tuple(sorted((f[i], f[(i + 1) % len(f)]))) for f in p.faces for i in range(len(f))}
        return sorted(round(float(np.linalg.norm(p.verts[i] - p.verts[j])), 9) for i, j in edges)
    assert edge_lengths(pieces[0][0]) == edge_lengths(pieces[1][0]) == edge_lengths(pieces[2][0])


@pytest.mark.parametrize("body,z,expect", [
    ({"cube": 2}, 1.3, 4.0),
    ({"pyramid": {"base": 2, "h": 2}}, 1.0, 1.0),  # 一半高处边长减半
    ({"pyramid": {"base": [2, 3], "h": 3, "apex": [0.5, 0.5]}}, 1.0, 6 * (2 / 3) ** 2),
    ({"cylinder": {"r": 1, "h": 2}}, 1.7, math.pi),
    ({"cone": {"r": 1, "h": 2}}, 1.0, math.pi / 4),
])
def test_section_areas(body, z, expect):
    pts = make_shape(body).section(z)
    assert area(pts) == pytest.approx(expect, rel=2e-3)  # 圆按 96 边形算


def test_section_outside_the_body_is_empty():
    shape = make_shape({"cone": {"r": 1, "h": 2}})
    assert shape.section(2.5) is None and shape.section(2.0) is None and shape.section(-0.1) is None


def test_bodies_without_at_line_up_on_screen():
    s = solid(bodies={"P": {"pyramid": {"base": 2, "h": 2}}, "K": {"cone": {"r": 1.128, "h": 2}}})
    ys = [float(s.cam.p(b.at)[1]) for b in s.bodies.values()]
    assert ys == pytest.approx([0.0, 0.0], abs=1e-9)  # 底面中心在画面上同一高度
    x0 = s.body_box["P"][2]
    assert s.body_box["K"][0] > x0  # 互不遮挡


ZU = {"bodies": {"P": {"pyramid": {"base": 2, "h": 2}, "role": "B"}, "K": {"cone": {"r": 2 / math.sqrt(math.pi), "h": 2}}},
      "plane": {"bodies": ["P", "K"], "z": 0.5, "compare": "right", "labels": ["$S_1$", "$S_2$"]}}


def test_sweep_and_compare_only_change_the_picture_when_played():
    s = solid(**ZU)
    first = s.action("sweep", {"z": 0.5})
    cmp_ = s.action("compare", {})
    move = s.action("sweep", {"z": 1.5})
    assert opacity(s.plane_node) == 0.0 and all(opacity(f) == 0.0 for f in s.flats)  # 建动作时画面不动
    run(first + cmp_)
    assert opacity(s.plane_node) == 1.0 and all(opacity(f) == 1.0 for f in s.flats)
    assert all(opacity(lab) == 1.0 for lab in s.flat_labels)
    # 祖暅：底面积一样、高一样，每一层的截面一样大（平铺出来的是真实大小）
    flat_area = [area(f.path.subpaths[0].points()) for f in s.flats]
    assert flat_area[0] == pytest.approx(flat_area[1], rel=2e-3)
    assert flat_area[0] == pytest.approx(4 * 0.75 ** 2, rel=2e-3)
    run(move)
    flat_area = [area(f.path.subpaths[0].points()) for f in s.flats]
    assert flat_area[0] == pytest.approx(4 * 0.25 ** 2, rel=2e-3) and flat_area[1] == pytest.approx(flat_area[0], rel=2e-3)


def test_flats_sit_to_the_right_and_do_not_overlap():
    s = solid(**ZU)
    run(s.action("sweep", {"z": 0.0}) + s.action("compare", {}))
    right = max(s.body_box[n][2] for n in ("P", "K"))
    boxes = [f.path.bbox() for f in s.flats]
    assert boxes[0][0] > right and boxes[1][0] > boxes[0][2]


def test_split_apart_join():
    s = solid(bodies={"C": {"cube": 2}}, splits={"s": {"body": "C", "into": ["P1", "P2", "P3"]}})
    pieces = [s.bodies[n].node for n in ("P1", "P2", "P3")]
    assert opacity(s.bodies["C"].node) == 1.0 and all(opacity(p) == 0.0 for p in pieces)
    split = s.action("split", {"split": "s"})
    assert s.items == pieces  # focus 只调暗现在看得见的块
    apart = s.action("apart", {"split": "s"})
    run(split)
    assert opacity(s.bodies["C"].node) == 0.0 and all(opacity(p) == 1.0 for p in pieces)
    start = [p.matrix.copy() for p in pieces]
    run(apart)
    moved = [p.matrix[:2, 2] - m[:2, 2] for p, m in zip(pieces, start)]
    gap = 0.75 * 2
    assert np.allclose(moved[0], [0, -gap * math.cos(math.radians(20))])  # 底下那块往下
    assert moved[1][0] < -1 and moved[2][0] > 1 and abs(moved[1][1]) < 1e-9  # 前面往左、右边往右
    run(s.action("join", {"split": "s"}) + s.action("unsplit", {"split": "s"}))
    assert all(np.allclose(p.matrix, m) for p, m in zip(pieces, start))
    assert opacity(s.bodies["C"].node) == 1.0 and s.items == [s.bodies["C"].node]


def test_restyle_recolors_faces_and_edges():
    s = solid(bodies={"C": {"cube": 2}}, splits={"s": {"body": "C", "into": ["P1", "P2", "P3"]}})
    b = s.bodies["P2"]
    assert b.faces[0].style.fill == T.B
    anims = s.restyle(parse("w#P2"), {"role": "HL"})
    assert b.faces[0].style.fill == T.B  # 播放时才改
    run(anims)
    assert all(f.style.fill == T.HL for f in b.faces) and all(ln.style.stroke == T.HL_INK for ln in b.lines)
    assert b.faces[0].style.fill_opacity < 0.5  # 还是淡色面
    assert s.restyle(parse("w"), {"role": "A"}) is None  # 整个对象：后端照常处理


DOC = {
    "spec": "0.1",
    "meta": {"id": "t", "title": "t"},
    "style": {"theme": "paper"},
    "objects": {"zu": {"widget": "solid", "in": "stage", **ZU}},
    "beats": [{"id": "b1", "do": [{"act": "show", "target": "zu"}]}],
}


def play(c: MoxianCompiler, action: dict) -> None:
    anims, dur = c.animations(action)
    tl = _FollowTimeline(follow=c._follow)
    tl.add(0.0, together(*anims))
    tl.seek(0.0)
    tl.seek(dur + 1.0)


def test_backend_set_goes_through_restyle_and_mark_works_on_parts():
    c = MoxianCompiler(DOC, T, None, log=lambda *_: None)
    play(c, {"act": "show", "target": "zu"})
    play(c, {"act": "set", "target": "zu#K", "args": {"role": "NO"}})
    w = c.objects["zu"].widget
    assert all(f.style.fill == T.NO for f in w.bodies["K"].faces)
    play(c, {"act": "sweep", "target": "zu", "args": {"z": 1.0}})
    play(c, {"act": "set", "target": "zu.section[0]", "args": {"role": "HL"}})
    assert w.sections[0].style.fill == T.HL and w.sections[1].style.fill != T.HL
    play(c, {"act": "mark", "target": "zu.section[1]"})
    assert "zu.section[1]" in c.marks
