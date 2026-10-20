"""中途采样的版式检查：连续变化的动画（参数补间、割线逼近、平面变换、截面扫过）播到中途也看一眼。

中途的 clip / overlap 是错误，cross 只提醒；结束帧上也有的问题不重复报；刚体搬动、变形拷贝不采样。
"""

import re

import numpy as np
from moxian.anim import MIDMOTION, Animation, ParamTween, TransformCopy
from test_dissect import PARA, TRI
from test_solid import ZU

from animind_kit import theme
from animind_kit.backends.moxian_backend import MoxianCompiler
from animind_kit.mx.dissect import Dissect
from animind_kit.mx.graph import Graph
from animind_kit.mx.plane import Plane
from animind_kit.mx.solid import Solid

T = theme.theme("paper")
META = {"spec": "0.1", "meta": {"id": "t", "title": "t", "line": "worked"}, "style": {"theme": "paper"}}
GRAPH = {"widget": "graph", "x": [-2, 3], "y": [-1, 5], "plots": [{"f": "x^2"}], "points": [{"x": 1, "label": "P"}],
         "secants": [{"point": 0, "h": 1, "label": "Q"}]}


def everything(anims: list[Animation]) -> list[Animation]:
    """动画连同子动画（together 的 children、dissect 分段播放的 windows）。"""
    out = []
    for a in anims:
        out.append(a)
        out += everything([s for _o, s in a.children()])
        out += everything([w[2] for w in getattr(a, "windows", [])])
    return out


def compiler(doc: dict) -> MoxianCompiler:
    return MoxianCompiler(doc, T, None, log=lambda *_: None, quality="l")


# ---------- 哪些动画标了中途采样 ----------

def test_continuous_component_animations_are_sampled():
    g = Graph("g", GRAPH, T, {}, {})
    assert [a.sample_alphas for a in g.action("approach", {"secant": 0, "h": 0.01})] == [MIDMOTION]
    for act, args in (("rotate", {"angle": 90}), ("warp", {"matrix": [[1, 1], [0, 1]]}), ("scale", {"by": 2})):
        p = Plane("p", {"widget": "plane", "x": [-3, 3], "y": [-3, 3]}, T, {})
        p.action("vector", {"name": "v", "v": [2, 1]})
        assert MIDMOTION in [a.sample_alphas for a in p.action(act, args)], act
    s = Solid("w", {"widget": "solid", **ZU}, T, {})
    s.action("sweep", {"z": 0.5})  # 第一次只是把截平面摆出来（淡入），不采样
    assert [a.sample_alphas for a in s.action("compare", {})][0] == MIDMOTION
    assert [a.sample_alphas for a in s.action("sweep", {"z": 1.5})] == [MIDMOTION]
    c = compiler({**META, "params": {"k": 0}, "objects": {}, "beats": []})
    [tw], _d = c.animations({"act": "tween", "args": {"k": 2}})
    assert isinstance(tw, ParamTween) and tw.sample_alphas == MIDMOTION


def test_rigid_moves_and_transform_copy_are_not_sampled():
    w = Dissect("para", PARA, T, {})
    anims = (w.action("cut", {"cut": "c1", "spread": 0.5})
             + w.action("rotate", {"piece": "T", "angle": 180, "about": {"vertex": [3, 3]}})
             + w.action("flip", {"piece": "T", "axis": [[0, 4.5], [1, 4.5]]})
             + w.action("shift", {"piece": "T", "by": [1, 0]})
             + w.action("snap", {"piece": "T", "vertex": [0, 0], "to": "Q", "to_vertex": [6, 0]})
             + w.action("copy", {"piece": "T2", "by": [0, -4]}))
    anims += Dissect("tri", TRI, T, {}).action("fold", {"cut": "f1", "flap": "flap"})
    p = Plane("p", {"widget": "plane", "x": [-3, 3], "y": [-3, 3]}, T, {})
    anims += p.action("vector", {"name": "v", "v": [2, 1]}) + p.action("slide", {"name": "v", "at": [1, 1]})
    c = compiler({**META, "objects": {"a": {"type": "math", "tex": "a", "at": [-2, 0]},
                                      "b": {"type": "math", "tex": "b", "at": [2, 0]}}, "beats": []})
    copy, _d = c.animations({"act": "transform", "from": "a", "to": "b"})
    assert isinstance(copy[0], TransformCopy)
    anims += copy
    assert len(everything(anims)) > len(anims)  # 真的看到了分段播放里面的动画
    assert all(a.sample_alphas == () for a in everything(anims))


def test_transform_and_dissect_beats_get_no_mid_samples():
    doc = {**META, "objects": {"a": {"type": "math", "tex": "a+b", "at": [-2, 0]},
                               "b": {"type": "math", "tex": "b+a", "at": [2, 0]},
                               "para": PARA},
           "beats": [{"id": "B", "do": [
               {"act": "show", "target": "a"}, {"act": "transform", "from": "a", "to": "b"},
               {"act": "show", "target": "para"}, {"act": "cut", "target": "para", "args": {"cut": "c1"}},
               {"act": "rotate", "target": "para", "args": {"piece": "T", "angle": 180, "about": {"vertex": [3, 3]}}},
           ]}]}
    c = compiler(doc)
    assert all(s.mid is None for s in c._plan(doc["beats"][0]).samples)
    c = compiler(doc)
    c.inspect(None, None)
    assert c.mid_tracker.findings == [] and c.warnings == []


# ---------- 中途的问题怎么报 ----------

def readout_doc(power: int) -> dict:
    """k 从 0 补间到 1：读数 10^(power·k(1−k)) 两头都是 1，中途变成一长串数字，伸出画面、压到右边的字。"""
    return {**META, "params": {"k": 0},
            "objects": {"big": {"type": "readout", "label": "N =", "value": f"10^({power}*k*(1-k))", "digits": 0,
                                "at": [-5.5, 0]},
                        "other": {"type": "text", "text": "右边", "at": [1.0, 0]}},
            "beats": [{"id": "B", "do": [{"act": "show", "target": "big"}, {"with": "prev", "act": "show", "target": "other"},
                                         {"act": "tween", "args": {"k": 1}, "dur": 2}]}]}


def mid_lines(c: MoxianCompiler) -> list[str]:
    return [p for p in c.problems if p.startswith("[mid")]


def test_tween_mid_motion_clip_and_overlap_are_errors_with_their_time():
    c = compiler(readout_doc(240))
    findings = c.inspect(None, None)
    assert c.tracker.findings == []  # 动作结束、beat 末尾的画面都没问题
    # inspect() 的返回值里有中途的错误，带中途的时刻
    assert {f.problem.kind for f in findings} == {"clip", "overlap"} and all(f.prefix == "mid" for f in findings)
    assert all(f.moments[0].source == "mid α=0.5 do[2] tween" and round(f.moments[0].time, 2) == 1.6 for f in findings)
    assert len(findings) == len(mid_lines(c))
    lines = mid_lines(c)
    kinds = {re.search(r"  (\w+)  ", ln).group(1) for ln in lines}
    assert kinds == {"clip", "overlap"}
    for ln in lines:
        assert "mid α=0.5 do[2] tween" in ln and " B t=1.6s" in ln  # k=0.5 那一刻（tween 从 0.6s 开始，2s 长）
    assert any("big × other" in ln for ln in lines)
    assert c.warnings == []


def test_same_mid_problem_is_reported_once_with_the_first_time():
    c = compiler(readout_doc(400))  # α=0.25、0.5、0.75 三处都压到右边的字
    c.inspect(None, None)
    overlaps = [ln for ln in mid_lines(c) if "  overlap  " in ln]
    assert len(overlaps) == 1 and "mid α=0.25" in overlaps[0] and " B t=1.1s（" in overlaps[0]
    assert len(c.mid_tracker.findings[0].moments) == 1


def test_render_reports_mid_motion_like_inspect(tmp_path):
    doc = readout_doc(240)
    c = compiler(doc)
    c.render_beat(doc["beats"][0], tmp_path / "B.mp4")
    c._report_lint()
    seen = compiler(doc)
    seen.inspect(None, None)
    assert mid_lines(c) == mid_lines(seen) and mid_lines(c)


def plane_doc(label: str | None, angle: float = 90, v=(2, 1)) -> dict:
    vec = {"name": "v", "v": list(v), **({"label": label} if label else {})}
    return {**META, "objects": {"p": {"widget": "plane", "x": [-3, 3], "y": [-3, 3]}},
            "beats": [{"id": "B", "do": [
                {"act": "show", "target": "p"},
                {"act": "vector", "target": "p", "args": vec},
                {"act": "point", "target": "p", "args": {"name": "P", "at": [1, 1], "label": "P", "dir": "ur"}},
                {"act": "rotate", "target": "p", "args": {"name": "v", "angle": angle, "trace": False}},
            ]}]}


def test_plane_rotate_label_overlap_mid_motion_is_an_error():
    """向量从 (2,1) 转 90° 到 (−1,2)：起止都干净，转到一半时名字 v 压到 y 轴的刻度 1 上。"""
    c = compiler(plane_doc("$v$"))
    findings = c.inspect(None, None)
    assert c.tracker.findings == [] and findings and all(f.problem.kind == "overlap" for f in findings)
    errors = mid_lines(c)
    assert errors and all("  overlap  " in ln and "mid α=" in ln and "do[3] rotate" in ln for ln in errors)
    assert all(re.search(r" B t=\d+\.\ds（片中 ", ln) for ln in errors)


def test_mid_motion_cross_is_only_a_warning():
    """没有名字的向量转过去：线扫过 y 轴的刻度数字，只提醒，不算版式问题（inspect 不因此失败）。"""
    c = compiler(plane_doc(None))
    assert c.inspect(None, None) == []
    assert c.problems == []
    assert len(c.warnings) == 1
    assert "  cross  " in c.warnings[0] and "mid α=0.75 do[3] rotate" in c.warnings[0] and "B t=" in c.warnings[0]


def test_cross_that_stays_at_rest_is_reported_once_as_an_error(monkeypatch):
    """转到 y 轴上停住：箭头压着刻度数字，结束帧照常报错；中途已经压上的同一处 cross 不再重复提醒。"""
    c = compiler(plane_doc("$v$", v=(2.5, 0)))
    pending = []
    settle = c._settle_mid
    monkeypatch.setattr(c, "_settle_mid", lambda beat: (pending.extend(c.mid_pending), settle(beat)))
    findings = c.inspect(None, None)
    rest = {f.problem.ident() for f in findings if f.problem.kind == "cross"}
    assert rest and {p.ident() for _m, ps in pending for p in ps} & rest  # 中途确实也看到了
    assert c.warnings == [] and not [ln for ln in c.problems if ln.startswith("[mid") and "  cross  " in ln]


def test_mid_samples_land_on_frames_and_skip_rest_frames():
    doc = readout_doc(240)
    c = compiler(doc)
    plan = c._plan(doc["beats"][0])
    mids = [s for s in plan.samples if s.mid is not None]
    assert [s.mid for s in mids] == [0.25, 0.5, 0.75]
    assert all(s.objs == frozenset({"big"}) for s in mids)  # 只有依赖 k 的读数在变
    assert all(np.isclose(s.time * c.fps, round(s.time * c.fps)) for s in plan.samples)
    rest = {s.time for s in plan.samples if s.mid is None}
    assert not rest & {s.time for s in mids}
    assert [s.time for s in plan.samples] == sorted(s.time for s in plan.samples)


def test_rest_cross_on_a_changing_readout_is_not_repeated_mid_motion(monkeypatch):
    """叉一直压着读数：结束帧报错（文字是「1」）；中途读数变成一长串数字，文字不同，但还是同一段字，不再提醒。
    读数的名字和数值是两段字，各报各的，不会被并成一条。"""
    doc = {**META, "params": {"k": 0},
           "objects": {"big": {"type": "readout", "label": "N =", "value": "10^(40*k*(1-k))", "digits": 0, "at": [-4, 0]},
                       "bar": {"type": "math", "tex": r"\rule{3cm}{0.4pt}", "at": [-4, 0], "allow": {"overlap": True}}},
           "beats": [{"id": "B", "do": [{"act": "show", "target": "big"}, {"with": "prev", "act": "show", "target": "bar"},
                                        {"act": "cross", "target": "bar"},
                                        {"act": "tween", "args": {"k": 1}, "dur": 2}]}]}
    c = compiler(doc)
    pending = []
    settle = c._settle_mid
    monkeypatch.setattr(c, "_settle_mid", lambda beat: (pending.extend(c.mid_pending), settle(beat)))
    findings = c.inspect(None, None)
    rest = {f.problem.text: f.problem for f in findings}
    assert set(rest) == {"N =", "1"} and rest["N ="].ident() != rest["1"].ident()
    mid_values = [p for _m, ps in pending for p in ps if p.ident() == rest["1"].ident()]
    assert mid_values and all(p.text != "1" for p in mid_values)  # 中途确实看到了，文字也确实变了
    assert c.warnings == [] and mid_lines(c) == [] and c.mid_tracker.findings == []


def two_readouts(**extra) -> dict:
    """k 和 m 各驱动一个读数；k 补间 2 秒，m 同一批补间 1 秒（同时开始、时长不同）。"""
    return {**META, "params": {"k": 0, "m": 0, "unused": 0}, **extra,
            "objects": {"a": {"type": "readout", "label": "a =", "value": "k", "at": [-4, 1]},
                        "b": {"type": "readout", "label": "b =", "value": "m", "at": [-4, -1]}},
            "beats": [{"id": "B", "do": [
                {"act": "show", "target": "a"}, {"with": "prev", "act": "show", "target": "b"},
                {"act": "tween", "args": {"k": 1}, "dur": 2}, {"with": "prev", "act": "tween", "args": {"m": 1}, "dur": 1},
                {"act": "tween", "args": {"unused": 5}, "dur": 1},
            ]}]}


def test_parallel_tweens_of_different_lengths_merge_on_a_shared_frame():
    doc = two_readouts()
    c = compiler(doc)
    mids = [s for s in c._plan(doc["beats"][0]).samples if s.mid is not None]
    # 都从 0.6s 开始：k 在 1.1、1.6、2.1，m 在 0.85、1.1、1.35；落到 15fps 的帧上（向上取整）
    assert [round(s.time * c.fps) for s in mids] == [13, 17, 21, 24, 32]
    merged = mids[1]
    assert merged.source == "mid α=0.25 do[2] tween + α=0.5 do[3] tween"  # 两个来源都列出
    assert merged.objs == frozenset({"a", "b"})
    assert [s.objs for s in mids] == [frozenset({"b"}), frozenset({"a", "b"}), frozenset({"b"}),
                                      frozenset({"a"}), frozenset({"a"})]
    # 没有对象依赖的参数（unused）补间：不采样（之后的 2.6–3.6s 里没有中途采样点）
    assert all(s.time < 2.6 for s in mids)


def test_mid_samples_follow_step_freezes():
    """子步定格：定格之后的中途采样点按成片时刻顺延定格的长度。"""
    step = {"act": "step", "args": {"hold": 2}}
    plain, frozen = two_readouts(), two_readouts(style={"theme": "paper", "segmented": True})
    for doc in (plain, frozen):
        doc["beats"][0]["do"].insert(2, dict(step))
    times = []
    for doc in (plain, frozen):
        c = compiler(doc)
        times.append([round(s.time * c.fps) for s in c._plan(doc["beats"][0]).samples if s.mid is not None])
    assert times[0] == [13, 17, 21, 24, 32]
    assert times[1] == [t + 2 * 15 for t in times[0]]
