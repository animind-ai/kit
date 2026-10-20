"""预计算的数值轨迹（data + plane 的 curves / points、graph 的数据曲线）：轨迹按 progress 平滑长出来，
沿曲线走的点和轨迹的头同步；中途采样看到的是那一刻的几何；inspect、render 都过。"""

import copy
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
from moxian.anim import Param
from scene_spec import check, check_file, curves
from scene_spec.loader import load_file

from animind_kit import theme
from animind_kit.backends.moxian_backend import MoxianCompiler
from animind_kit.mx.graph import Graph
from animind_kit.mx.plane import Plane
from animind_kit.mx.track import length
from animind_kit.render import inspect, render

T = theme.theme("paper")
EXAMPLES = Path(__file__).resolve().parents[2] / "scene-spec" / "examples"
PHASE = EXAMPLES / "phase-portrait.scene.yaml"
DAMPED = json.loads((EXAMPLES / "assets" / "damped-phase.json").read_text())
PLANE = {"widget": "plane", "x": [-3, 4], "y": [-3, 3],
         "curves": [{"data": "damped", "progress": "p", "along": "param"}],
         "points": [{"curve": 0, "at": "p", "label": "P"}]}


def inline_doc() -> dict:
    doc = copy.deepcopy(load_file(PHASE))
    doc["data"] = {"damped": copy.deepcopy(DAMPED)}
    return doc


def trail(node) -> np.ndarray:
    """折线路径的顶点（line_to 存成三次贝塞尔：每段取终点）。"""
    if node.path.is_empty():
        return np.zeros((0, 2))
    pts = node.path.subpaths[0].points()
    return np.vstack([pts[:1], pts[3::3]])


def head(plane: Plane, i: int = 0) -> np.ndarray:
    return plane.built.parts["point"][i].children[0].matrix[:2, 2].copy()


def polyline_length(pts: np.ndarray) -> float:
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum()) if len(pts) > 1 else 0.0


# ---------- 组件 ----------

@pytest.mark.parametrize("along", ["length", "param"])
def test_point_rides_the_head_of_a_growing_trail(along):
    p = Param(0.0, "p")
    spec = copy.deepcopy(PLANE)
    spec["curves"][0]["along"] = along
    w = Plane("phase", spec, T, {"damped": DAMPED}, {"p": p})
    curve = w.built.parts["curve"][0]
    assert trail(curve).size == 0 and np.allclose(head(w), DAMPED[0])  # p = 0：还没画，点在起点
    pts = curves.points(DAMPED)
    cum = curves.ruler(pts, along)
    last = 0.0
    for k in range(1, 41):
        p.set(k / 40)
        drawn = trail(curve)
        assert np.allclose(drawn[-1], head(w), atol=1e-9)  # 点就在轨迹的头上
        assert np.allclose(head(w), curves.point_at(pts, cum, k / 40))  # 和检查器同一套插值
        now = polyline_length(drawn)
        assert now > last  # 轨迹一直在长
        last = now
    assert np.allclose(trail(curve), DAMPED) and math.isclose(last, length(pts))


def test_parametric_curve_follows_its_scene_params():
    r, p = Param(1.0, "r"), Param(1.0, "p")
    spec = {"widget": "plane", "x": [-3, 3], "y": [-3, 3],
            "curves": [{"x": "r*cos(t)", "y": "r*sin(t)", "domain": [0, math.pi], "progress": "p", "along": "param"}],
            "points": [{"curve": 0, "at": 0.5}]}
    w = Plane("c", spec, T, {}, {"r": r, "p": p})
    assert np.allclose(head(w), [0, 1], atol=1e-9)
    r.set(2.0)
    assert np.allclose(head(w), [0, 2], atol=1e-9) and np.allclose(trail(w.built.parts["curve"][0])[-1], [-2, 0], atol=1e-9)
    p.set(0.25)
    assert np.allclose(trail(w.built.parts["curve"][0])[-1], [2 * math.cos(math.pi / 4), 2 * math.sin(math.pi / 4)])


def test_mover_label_steps_off_tick_labels():
    p = Param(0.5, "p")  # 这时点在原点左下 (−0.43, −0.47)：名字放在默认的右上会压住刻度 0
    w = Plane("phase", PLANE, T, {"damped": DAMPED}, {"p": p})
    item = w.movers[0][0]
    box = item.label.bbox()
    assert not any(box.x0 < b.x1 and b.x0 < box.x1 and box.y0 < b.y1 and b.y0 < box.y1 for b in w.text_boxes)


def test_graph_data_plot_grows_with_progress():
    p = Param(0.0, "p")
    spec = {"widget": "graph", "x": [-3, 3], "y": [-3, 3],
            "plots": [{"f": "x/3"}, {"data": "damped", "progress": "p", "label": "v(x)"}]}
    g = Graph("g", spec, T, {"damped": DAMPED}, {"p": p})
    curve = g.built.parts["plot"][1]
    assert curve.path.is_empty()
    last = 0.0
    for v in (0.1, 0.5, 0.9, 1.0):
        p.set(v)
        now = polyline_length(trail(curve) / g.u)
        assert now > last
        last = now
    assert np.allclose(trail(curve)[-1] / g.u, DAMPED[-1])


# ---------- 整篇：inline 和 {src} 两种写法 ----------

def compiled(variant: str) -> MoxianCompiler:
    doc = load_file(PHASE) if variant == "src" else inline_doc()
    return MoxianCompiler(doc, T, None, log=lambda *_: None, quality="l", spec_dir=PHASE.parent)


@pytest.mark.parametrize("variant", ["inline", "src"])
def test_both_variants_load_the_same_trajectory(variant):
    c = compiled(variant)
    assert c.data["damped"] == DAMPED and len(DAMPED) == 500
    assert check(c.doc, root=PHASE.parent).issues == []


@pytest.mark.parametrize("variant", ["inline", "src"])
def test_tween_mid_samples_see_the_trail_and_point_in_sync(variant):
    c = compiled(variant)
    beats = {b["id"]: b for b in c.doc["beats"]}
    plan = c._plan(beats["Start"])
    plan.tl.seek(plan.until)  # 先把 Start 播完：Spiral 从它的结束状态出发
    plan = c._plan(beats["Spiral"])
    mids = [s for s in plan.samples if s.mid is not None]
    assert [s.mid for s in mids] == [0.25, 0.5, 0.75] and all(s.objs == frozenset({"phase"}) for s in mids)
    w = c.objects["phase"].widget
    lengths = []
    for s in sorted(mids, key=lambda s: s.time):
        plan.tl.seek(s.time)
        p = c.params["p"].value
        assert 0 < p < 1
        drawn = trail(w.built.parts["curve"][0])
        assert np.allclose(drawn[-1], head(w), atol=1e-9)
        pts = curves.points(DAMPED)
        assert np.allclose(head(w), curves.point_at(pts, curves.ruler(pts, "param"), p))
        lengths.append(polyline_length(drawn))
    plan.tl.seek(plan.until)
    assert c.params["p"].value == 1.0 and np.allclose(head(w), DAMPED[-1])
    lengths.append(polyline_length(trail(w.built.parts["curve"][0])))
    assert lengths == sorted(lengths) and len(set(lengths)) == 4


@pytest.mark.parametrize("variant", ["inline", "src"])
def test_inspect_passes(variant, tmp_path):
    if variant == "src":
        assert inspect(PHASE, None, None, log=lambda *_: None) == []
    else:
        spec = tmp_path / "phase.scene.yaml"
        spec.write_text(json.dumps(inline_doc(), ensure_ascii=False), encoding="utf-8")  # JSON 也是合法的受限 YAML
        assert check_file(spec).issues == []
        assert inspect(spec, None, None, log=lambda *_: None) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_passes_with_src_data(tmp_path):
    scene = tmp_path / "scene"
    shutil.copytree(EXAMPLES / "assets", scene / "assets", ignore=shutil.ignore_patterns("hook-plate"))
    shutil.copy(PHASE, scene / PHASE.name)
    summary = render(scene / PHASE.name, tmp_path / "out" / "phase.mp4", None, "l", log=lambda *_: None, backend="moxian")
    assert summary["layout_problems"] == [] and summary["timing_issues"] == []
    assert (tmp_path / "out" / "phase.mp4").stat().st_size > 0


def test_bad_src_is_refused_before_building(tmp_path):
    doc = load_file(PHASE)
    with pytest.raises(SystemExit, match="SS491"):
        MoxianCompiler(doc, T, None, log=lambda *_: None, quality="l", spec_dir=tmp_path)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "damped-phase.json").write_text("[[0, 0], [1, NaN]]")
    with pytest.raises(SystemExit, match="SS494"):
        MoxianCompiler(doc, T, None, log=lambda *_: None, quality="l", spec_dir=tmp_path)
    spec = tmp_path / PHASE.name
    shutil.copy(PHASE, spec)
    with pytest.raises(SystemExit, match="检查未通过"):
        inspect(spec, None, None, log=lambda *_: None)
