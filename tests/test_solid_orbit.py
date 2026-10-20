"""立体组件 solid 的 orbit：相机连续转动，投影、消隐、虚实棱、绘制顺序、标签每一帧重算；转过的视角留给后面的动作和 beat。"""

import copy
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import yaml
from moxian.anim import together
from scene_spec import check_file

from animind_kit import theme
from animind_kit.backends.moxian_backend import MoxianCompiler, _FollowTimeline
from animind_kit.mx.solid import Solid, orbit_steps, orbit_target, planned_views
from animind_kit.render import inspect, render

T = theme.theme("paper")
FIXTURE = Path(__file__).parent / "fixtures" / "solid_orbit.scene.yaml"
CUBE = {"bodies": {"C": {"cube": 2, "label": "正方体"}}, "splits": {"s": {"body": "C", "into": ["P1", "P2", "P3"]}}}
ZU = {"bodies": {"P": {"pyramid": {"base": 2, "h": 2}, "role": "B"}, "K": {"cone": {"r": 1.128, "h": 2}}},
      "plane": {"bodies": ["P", "K"], "z": 0.5, "compare": "right", "labels": ["$S_1$", "$S_2$"]}}
# 正方体的顶点：0–3 底面 (-,-) (+,-) (+,+) (-,+)，4–7 顶面同序；y 向里，所以 y = -1 是前面
FRONT_RIGHT = (1, 5)  # 前面和右面相交的竖棱：默认视角看得见
BACK_LEFT = (3, 7)  # 后面和左面相交的竖棱：默认视角被挡住


def solid(opts=None, **d) -> Solid:
    return Solid("w", {"widget": "solid", **d}, T, {}, opts=opts)


def run(anims):
    for a in anims:
        a.begin()
        a.finish()


def play(anims, alpha: float) -> None:
    """把动画播到 alpha（时长的比例，这里直接当缓动后的值用）。"""
    for a in anims:
        if not a.begun:
            a.begin()
        a.interpolate(alpha)


def drawn(b) -> int:
    return sum(not f.path.is_empty() for f in b.faces)


def segments(path) -> int:
    return len(path.subpaths)


# ---------- 角度 ----------

def test_absolute_azimuth_takes_the_short_way_and_relative_turns_as_written():
    assert orbit_target((30, 20), {"azimuth": 65, "elevation": 28}) == (65, 28)
    assert orbit_target((30, 20), {"azimuth": 300}) == (-60, 20)  # 往回转 90°，不是往前 270°
    assert orbit_target((350, 20), {"azimuth": 10}) == (370, 20)
    assert orbit_target((30, 20), {"d_azimuth": 360, "d_elevation": -50}) == (390, -30)
    steps = orbit_steps((30, 20), (390, -30))
    assert steps[-1] == (390, -30) and all(abs(b[0] - a[0]) <= 5 for a, b in zip([(30, 20), *steps], steps))


def test_planned_views_follow_the_document_order():
    doc = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    cube = planned_views(doc, "cube")
    assert (65, 28) in cube and cube[-1] == (215, -25)  # 65 + 150；仰角直接插到 -25
    assert planned_views(doc, "zu")[-1] == (70, 35)
    doc["beats"] = [b for b in doc["beats"] if b["id"] not in ("Turn", "Split")]
    assert planned_views(doc, "cube") == []


# ---------- 虚实棱、朝前的面 ----------

def test_orbit_turns_hidden_edges_dashed_and_back_faces_forward():
    s = solid(**CUBE)
    b = s.bodies["C"]
    assert b.edges[FRONT_RIGHT] and not b.edges[BACK_LEFT]
    before = list(b.front)
    hidden_before = b.lines[1].path.subpaths[0].start.copy()
    anims = s.action("orbit", {"azimuth": 210, "elevation": -20})  # 从左后下方看
    assert s.cam.azimuth == 30 and b.edges[FRONT_RIGHT]  # 建动作时画面不动
    run(anims)
    assert (s.cam.azimuth, s.cam.elevation) == (210, -20)
    assert not b.edges[FRONT_RIGHT] and b.edges[BACK_LEFT]  # 原来的实线变虚线，被挡住的变实线
    assert b.front == [not f for f in before]  # 正好换成另外三个面朝前
    assert drawn(b) == 3 and segments(b.lines[1].path) > 3
    assert not np.allclose(b.lines[1].path.subpaths[0].start, hidden_before)


def test_edges_switch_continuously_during_the_orbit():
    s = solid(**CUBE)
    b = s.bodies["C"]
    anims = s.action("orbit", {"d_azimuth": 180})
    seen = set()
    for alpha in np.linspace(0, 1, 37):
        play(anims, float(alpha))
        assert 1 <= drawn(b) <= 3
        seen.add(tuple(sorted(e for e, on in b.edges.items() if on)))
        # 每一帧：一条棱画实线，当且仅当它挨着的面有一个朝前
        for e, on in b.edges.items():
            faces = [i for i, f in enumerate(b.shape.faces) if e[0] in f and e[1] in f]
            assert on == any(b.front[i] for i in faces)
    assert len(seen) >= 3  # 中途换过好几次


def test_round_bodies_from_below_show_the_bottom():
    s = solid(bodies={"Y": {"cylinder": {"r": 1, "h": 2}}, "K": {"cone": {"r": 1, "h": 2}}})
    y, k = s.bodies["Y"], s.bodies["K"]
    assert drawn(k) == 1 and not k.lines[1].path.is_empty()  # 从上面看：侧面，底面后半圈虚线
    top = y.faces[1].path.bbox()
    run(s.action("orbit", {"elevation": -30}))
    assert drawn(k) == 2 and k.lines[1].path.is_empty()  # 从下面看：底面整个朝前，没有虚线
    low = y.faces[1].path.bbox()
    assert low[3] < top[1]  # 圆柱涂色的那个底面从顶上换到了底下
    assert not y.lines[1].path.is_empty()


# ---------- 远近：绘制顺序 ----------

def body_order(s: Solid) -> list[str]:
    names = {id(b.node): n for n, b in s.bodies.items()}
    return [names[id(c)] for c in s.fig.children if id(c) in names]


@pytest.mark.parametrize("turn,order", [(90, ["P", "K"]), (-90, ["K", "P"])])
def test_nearer_body_is_drawn_on_top_after_the_orbit(turn, order):
    s = solid(**ZU)
    assert body_order(s) == ["P", "K"]  # 静止的画面：按声明顺序（排成一行，互不遮挡）
    run(s.action("orbit", {"d_azimuth": turn}))
    assert body_order(s) == order
    depth = {n: float(np.dot(b.at, s.cam.V)) for n, b in s.bodies.items()}
    assert depth[order[0]] < depth[order[1]]  # 远的先画，近的盖在上面
    # 转了 90°，原来排成一行的两个立体前后叠在一起：画面上真的重叠，所以顺序要紧
    a, b = (s.bodies[n].node.bbox(np.eye(3)) for n in ("P", "K"))
    assert a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1


# ---------- 标签、截面 ----------

def test_label_follows_the_projection_without_flipping():
    s = solid(**CUBE)
    b = s.bodies["C"]
    anims = s.action("orbit", {"d_azimuth": 140, "elevation": -40})
    for alpha in (0.0, 0.3, 0.6, 1.0):
        play(anims, alpha)
        m = b.label.matrix
        assert m[0, 0] > 0 and m[1, 1] > 0 and abs(m[0, 1]) < 1e-12  # 不转、不翻
        x0, y0, x1, _ = s.body_box["C"]
        here = s.cam.p(b.at)
        assert m[0, 2] + here[0] == pytest.approx((x0 + x1) / 2)  # 立体投影的正下方居中
        assert m[1, 2] + here[1] < y0
        assert s.body_box["C"] == pytest.approx(s._proj_box(b.shape.outline_pts() + b.at))


def test_plane_and_sections_turn_with_the_bodies():
    s = solid(**ZU)
    run(s.action("sweep", {"z": 0.5}))
    sec = s.sections[1].path.subpaths[0].points().copy()
    plane = s.plane_node.path.subpaths[0].points().copy()
    run(s.action("orbit", {"azimuth": 70, "elevation": 40}))
    pts = s._section_pts(1, 0.5)
    assert np.allclose(s.sections[1].path.subpaths[0].points()[::3][: len(pts)], s.cam.p(pts))
    assert not np.allclose(s.sections[1].path.subpaths[0].points(), sec)
    corners = s._plane_corners(0.5)
    assert np.allclose(s.plane_node.path.subpaths[0].points()[::3][:4], s.cam.p(corners))
    assert not np.allclose(s.plane_node.path.subpaths[0].points()[::3][:4], plane[::3][:4])
    # 截平面固定在立体上：和视角无关
    assert np.allclose(corners, solid(**ZU)._plane_corners(0.5))


def test_compare_and_sweep_after_orbit_use_the_new_view():
    s = solid(**ZU)
    anims = s.action("sweep", {"z": 0.5}) + s.action("orbit", {"azimuth": 70, "elevation": 40})
    cmp_ = s.action("compare", {})  # 建动作时：截面从转过之后的投影平铺出去
    run(anims)
    play(cmp_, 0.0)
    pts = s._section_pts(0, 0.5)
    assert np.allclose(s.flats[0].path.subpaths[0].points()[::3][: len(pts)], s.cam.p(pts))
    run(cmp_)
    flat = s.flats[0].path.subpaths[0].points().copy()
    run(s.action("orbit", {"azimuth": 20}))
    assert np.allclose(s.flats[0].path.subpaths[0].points(), flat)  # 平铺的是俯视的真实形状，不跟着转


def test_apart_after_orbit_moves_along_the_new_screen_axes():
    s = solid(**CUBE)
    run(s.action("orbit", {"azimuth": 60, "elevation": 35}) + s.action("split", {"split": "s"}))
    pieces = [s.bodies[n] for n in ("P1", "P2", "P3")]
    start = [p.node.matrix[:2, 2].copy() for p in pieces]
    run(s.action("apart", {"split": "s"}))
    moved = [p.node.matrix[:2, 2] - m for p, m in zip(pieces, start)]
    gap = 0.75 * 2
    assert moved[1] == pytest.approx([-gap, 0], abs=1e-9) and moved[2] == pytest.approx([gap, 0], abs=1e-9)
    # 分开着再转：挪开的量是立体坐标，跟着新视角重新投影
    run(s.action("orbit", {"azimuth": 0, "elevation": 35}))
    for p in pieces:
        assert np.allclose(p.node.matrix[:2, 2], s.cam.p(p.at + p.off))
    run(s.action("join", {"split": "s"}))
    assert all(np.allclose(p.node.matrix[:2, 2], s.cam.p(p.at)) for p in pieces)


# ---------- 状态、排版 ----------

def test_state_and_restore_keep_the_new_view():
    s = solid(**CUBE)
    s.action("orbit", {"azimuth": 65, "elevation": 28})
    state = s.state()
    assert state == {"view": [65.0, 28.0]}  # 推演到的视角
    fresh = solid(**CUBE)
    fresh.restore(copy.deepcopy(state))
    assert (fresh.cam.azimuth, fresh.cam.elevation) == (65, 28)
    run(s.action("orbit", {"d_azimuth": 0}))  # 画面播完
    a, b = s.bodies["C"], fresh.bodies["C"]
    assert a.front == b.front and a.edges == b.edges
    assert np.allclose(a.label.matrix, b.label.matrix) and np.allclose(a.node.matrix, b.node.matrix)
    for x, y in zip(a.lines + a.faces, b.lines + b.faces):
        assert np.allclose([sp.start for sp in x.path.subpaths], [sp.start for sp in y.path.subpaths])


def test_planned_views_widen_the_frame_and_keep_every_frame_inside():
    plain = solid(**ZU)
    views = orbit_steps((30, 20), (120, -40))
    s = solid(opts={"views": views}, **ZU)
    assert s.k <= plain.k
    x0, y0, x1, y1 = s.box
    anims = s.action("sweep", {"z": 0.5}) + s.action("orbit", {"azimuth": 120, "elevation": -40})
    for alpha in np.linspace(0, 1, 25):
        play(anims, float(alpha))
        for b in s.bodies.values():
            bx = b.node.bbox(np.eye(3))  # 图形坐标
            assert x0 - 1e-6 <= bx.x0 and bx.x1 <= x1 + 1e-6 and y0 - 1e-6 <= bx.y0 and bx.y1 <= y1 + 1e-6
    assert solid(opts={}, **ZU).k == plain.k  # 没有 orbit：排版和以前一样


def test_orbit_is_sampled_mid_motion():
    s = solid(**CUBE)
    (small,) = s.action("orbit", {"d_azimuth": 30})
    (big,) = s.action("orbit", {"d_azimuth": 360})
    assert small.sample_alphas == (0.25, 0.5, 0.75)
    assert len(big.sample_alphas) == 11 and big.duration > small.duration


# ---------- 后端：跨 beat、缓存、检查、渲染 ----------

def compiler(doc: dict) -> MoxianCompiler:
    return MoxianCompiler(doc, T, None, log=lambda *_: None, quality="l")


def play_beat(c: MoxianCompiler, beat: dict) -> None:
    plan = c._plan(beat)
    plan.tl.seek(plan.until)


def test_backend_keeps_the_rotated_view_in_later_beats():
    doc = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    c = compiler(doc)
    beats = {b["id"]: b for b in doc["beats"]}
    play_beat(c, beats["Turn"])
    cube = c.objects["cube"].widget  # 第一次排版时后端可能按字号下限重建过组件：播完再取
    assert cube.extra_cams  # 后端把 orbit 经过的视角交给组件排版（重建时也带着）
    assert (cube.cam.azimuth, cube.cam.elevation) == (65, 28)
    play_beat(c, beats["Split"])
    assert (cube.cam.azimuth, cube.cam.elevation) == (215, -25)
    assert all(np.allclose(cube.bodies[n].node.matrix[:2, 2], cube.cam.p(cube.bodies[n].at)) for n in ("P1", "P2", "P3"))
    play_beat(c, beats["Section"])
    zu = c.objects["zu"].widget
    assert zu.extra_cams and (zu.cam.azimuth, zu.cam.elevation) == (70, 35)
    play_beat(c, beats["Later"])
    assert c.objects["zu"].widget is zu and (zu.cam.azimuth, zu.cam.elevation) == (70, 35)  # 后面一个 beat 的 sweep 还在转过之后的视角下画
    pts = zu._section_pts(0, 1.5)
    assert np.allclose(zu.sections[0].path.subpaths[0].points()[::3][: len(pts)], zu.cam.p(pts))
    assert zu.state() == {"view": [70.0, 35.0]}


def test_set_after_orbit_still_recolors_the_drawn_faces():
    doc = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    c = compiler(doc)
    beats = {b["id"]: b for b in doc["beats"]}
    play_beat(c, beats["Turn"])

    def go(action):
        anims, dur = c.animations(action)
        tl = _FollowTimeline(follow=c._follow)
        tl.add(0.0, together(*anims))
        tl.seek(0.0)
        tl.seek(dur + 1.0)

    go({"act": "set", "target": "cube#C", "args": {"role": "HL"}})
    go({"act": "orbit", "target": "cube", "args": {"d_azimuth": 120}})
    b = c.objects["cube"].widget.bodies["C"]
    assert drawn(b) == 3 and all(f.style.fill == T.HL for f in b.faces)


def test_fixture_passes_check_inspect_and_render(tmp_path):
    report = check_file(FIXTURE)
    assert report.issues == [], [str(i) for i in report.issues]
    problems = inspect(FIXTURE, None, tmp_path / "sheets", None, "l", log=lambda *_: None)
    assert problems == []
    summary = render(FIXTURE, tmp_path / "orbit.mp4", None, "l", log=lambda *_: None, backend="moxian")
    assert summary["layout_problems"] == [] and summary["beats"] == ["Turn", "Split", "Section", "Later"]
    assert (tmp_path / "orbit.mp4").stat().st_size > 0


def framemd5(video):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-map", "0:v", "-f", "framemd5", "-"],
                         check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line and not line.startswith("#")]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_beat_cache_hits_reproduce_the_rotated_view(tmp_path, monkeypatch):
    seen: list[str] = []
    original = MoxianCompiler.render_beat

    def spy(self, beat, clip, plan=None):
        seen.append(beat["id"])
        return original(self, beat, clip, plan)

    monkeypatch.setattr(MoxianCompiler, "render_beat", spy)
    spec = tmp_path / "solid_orbit.scene.yaml"
    shutil.copyfile(FIXTURE, spec)
    out = tmp_path / "out" / "orbit.mp4"
    render(spec, out, None, "l", log=lambda *_: None, backend="moxian")
    first = framemd5(out)
    assert seen == ["Turn", "Split", "Section", "Later"]
    seen.clear()
    render(spec, out, None, "l", log=lambda *_: None, backend="moxian")
    assert seen == [] and framemd5(out) == first  # 全部命中，拼出来逐帧一样
    # 只改最后一个 beat：重渲的 Later 从转过之后的视角开始，和全量重渲一致
    text = spec.read_text(encoding="utf-8").replace("args: {z: 1.5}", "args: {z: 1.25}")
    spec.write_text(text, encoding="utf-8")
    seen.clear()
    render(spec, out, None, "l", log=lambda *_: None, backend="moxian")
    assert seen == ["Later"]
    cached = framemd5(out)
    render(spec, tmp_path / "full" / "orbit.mp4", None, "l", log=lambda *_: None, backend="moxian", cache=False)
    assert cached == framemd5(tmp_path / "full" / "orbit.mp4")
