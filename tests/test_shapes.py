"""type: shape（七种基础图形）、type: group，以及 transform 的 match: tex 片段配对和 replace（墨线后端）。"""

from pathlib import Path

import numpy as np
import pytest
from moxian.anim import Fade, TransformCopy, TransformMatching
from moxian.core import PathNode
from scene_spec import check_file

from animind_kit import analysis, theme
from animind_kit.backends.moxian_backend import MoxianCompiler
from animind_kit.mx import scan
from animind_kit.render import inspect, render

T = theme.theme("paper")
META = {"spec": "0.1", "meta": {"id": "t", "title": "t", "line": "worked"}, "style": {"theme": "paper"}}
FIXTURE = Path(__file__).parent / "fixtures" / "shapes.scene.yaml"
E1 = "[$x^2$]{#a} $+$ [$2x$]{#b} $=0$"
E2 = "[$x^2$]{#a} $+$ [$2x$]{#b} $+$ [$1$]{#c} $=$ [$1$]{#d}"


def compiler(doc: dict) -> MoxianCompiler:
    return MoxianCompiler(doc, T, None, log=lambda *_: None, quality="l")


def play(c: MoxianCompiler, beat: dict) -> None:
    plan = c._plan(beat)
    plan.tl.seek(plan.until)


def scanned(c: MoxianCompiler) -> set[str | None]:
    """此刻画面上（扫描、lint 看得到的）属于哪些对象。"""
    return {t.obj for t in scan.scan(c.root, c.labels(), c.camera)}


# ---------- 图形 ----------

SHAPES = {
    "c": {"type": "shape", "shape": "circle", "radius": 0.9, "label": "$r$"},
    "s": {"type": "shape", "shape": "square", "side": 1.6, "role": "B"},
    "r": {"type": "shape", "shape": "rectangle", "side": [2.4, 1.4]},
    "p": {"type": "shape", "shape": "polygon", "points": [[0, 0], [2, 0], [0.8, 1.6]], "role": "C"},
    "l": {"type": "shape", "shape": "line", "points": [[0, 0], [1.6, 1.2]], "label": "$\\ell$"},
    "a": {"type": "shape", "shape": "arrow", "side": 1.8, "role": "HL"},
    "d": {"type": "shape", "shape": "dot", "role": "NO", "label": "$P$"},
}


def paths(node) -> list[PathNode]:
    return [n for n, _m, _s in node.walk() if isinstance(n, PathNode) and not n.path.is_empty()]


def test_every_shape_kind_builds_with_its_geometry():
    c = compiler({**META, "objects": dict(SHAPES), "beats": []})
    box = {k: c.objects[k].node.bbox() for k in SHAPES}
    assert np.isclose(paths(c.objects["c"].node)[0].path.bbox()[2], 0.9)  # 半径
    s = paths(c.objects["s"].node)[0].path.bbox()
    assert np.isclose(s[2] - s[0], 1.6) and np.isclose(s[3] - s[1], 1.6)
    r = paths(c.objects["r"].node)[0].path.bbox()
    assert np.isclose(r[2] - r[0], 2.4) and np.isclose(r[3] - r[1], 1.4)
    assert paths(c.objects["p"].node)[0].path.subpaths[0].closed
    assert np.isclose(box["a"].width, 1.8)  # 箭头尖正好落在终点上
    assert len(paths(c.objects["a"].node)) == 2  # 箭杆 + 箭头尖
    dot = paths(c.objects["d"].node)[0]
    assert dot.style.fill == T.NO and dot.style.stroke is None  # 点是实心的
    # 有 role 的闭合图形垫淡色；没有的只描边
    assert paths(c.objects["s"].node)[0].style.fill == T.B
    assert paths(c.objects["r"].node)[0].style.fill is None


def test_labels_sit_inside_closed_shapes_and_clear_of_lines():
    c = compiler({**META, "objects": dict(SHAPES), "beats": []})
    circle_node = c.objects["c"].node
    ring, label = circle_node.children[0], circle_node.children[1]
    assert np.allclose(label.bbox().center, ring.bbox().center, atol=1e-6)
    line_node = c.objects["l"].node
    assert line_node.children[1].bbox().y0 > line_node.children[0].bbox().y1  # 线的标注在线的上方，不压线
    dot_node = c.objects["d"].node
    assert dot_node.children[1].bbox().x0 > dot_node.children[0].bbox().x1  # 点的标注在右上


def test_group_lays_children_out_and_exposes_items():
    objs = {**SHAPES, "g": {"type": "group", "children": ["c", "s", "r"]},
            "h": {"type": "group", "children": ["l", "a", "d"]},
            "v": {"type": "group", "children": ["g", "h"]}}  # 嵌套
    objs["h"] = {**objs["h"]}
    objs["a"] = {**objs["a"], "below": "l"}  # 组内的相对布局
    c = compiler({**META, "objects": objs, "beats": []})
    assert [n.name for n in c.root.children] == ["p", "v"]  # 成员不直接挂在根上
    g = c.objects["g"]
    assert g.items == [c.objects[k].node for k in ("c", "s", "r")]
    boxes = [n.bbox() for n in g.items]
    assert boxes[0].x1 < boxes[1].x0 < boxes[1].x1 < boxes[2].x0  # 默认从左到右排开
    lb, ab = c.objects["l"].node.bbox(), c.objects["a"].node.bbox()
    assert ab.y1 < lb.y0  # a 写了 below: l
    assert c.resolve("g[1]").children == [c.objects["s"].node]


def test_showing_a_member_opens_just_that_member():
    objs = {**SHAPES, "g": {"type": "group", "children": ["c", "s", "r"], "in": "stage"}}
    doc = {**META, "objects": objs, "beats": [{"id": "B", "do": [{"act": "show", "target": "s"}]}]}
    c = compiler(doc)
    play(c, doc["beats"][0])
    assert set(c.visible) == {"g"}  # 登记的是整组（版式检查看整组）
    assert scanned(c) == {"s"}  # 画面上只有 s
    c2 = compiler({**doc, "beats": [{"id": "B", "do": [{"act": "show", "target": "g[2]"}, {"act": "hide", "target": "r"}]}]})
    play(c2, c2.doc["beats"][0])
    assert scanned(c2) == set() and set(c2.visible) == {"g"}
    assert analysis.lifetimes(doc)["g"] == {0}  # show 成员算整组占着这个 beat


def test_fixture_passes_check_inspect_and_render(tmp_path):
    report = check_file(FIXTURE)
    assert report.issues == [], [str(i) for i in report.issues]
    problems = inspect(FIXTURE, None, tmp_path / "sheets", None, "l", log=lambda *_: None)
    assert problems == []
    summary = render(FIXTURE, tmp_path / "shapes.mp4", None, "l", log=lambda *_: None, backend="moxian")
    assert summary["layout_problems"] == [] and summary["beats"] == ["Shapes", "Complete", "Fold"]
    assert (tmp_path / "shapes.mp4").stat().st_size > 0


# ---------- transform: match: tex ----------

def eq_doc(action: dict) -> dict:
    return {**META,
            "objects": {"e1": {"type": "text", "text": E1, "at": [0, 2]}, "e2": {"type": "text", "text": E2, "at": [0, -1]}},
            "beats": [{"id": "Show", "do": [{"act": "show", "target": "e1"}]},
                      {"id": "Go", "do": [{"act": "transform", "from": "e1", "to": "e2", **action}]}]}


def centers(c: MoxianCompiler, obj: str, names) -> dict[str, np.ndarray]:
    return {k: c.wbox(c.objects[obj].spans[k]).center for k in names}


def opacity(c: MoxianCompiler, node) -> float:
    """节点在画面上的实际透明度（沿树相乘）。"""
    return next(s.opacity for n, _m, s in c.root.walk() if n is node)


def test_match_tex_moves_common_spans_and_fades_in_new_ones():
    doc = eq_doc({"match": "tex"})
    c = compiler(doc)
    play(c, doc["beats"][0])
    plan = c._plan(doc["beats"][1])
    [anim] = plan.inner.items[0].anim.anims
    assert isinstance(anim, TransformMatching) and anim.sample_alphas == ()  # 重排：不做中途采样
    start = centers(c, "e1", "ab")
    end = centers(c, "e2", "ab")
    e2 = c.objects["e2"]
    news = [m for k in "cd" for m in c._members(e2.spans[k])]
    plan.tl.seek(0.0)
    now = centers(c, "e2", "ab")
    for k in "ab":
        assert np.allclose(now[k], start[k], atol=1e-6)  # 一开始：共同项在原式里的位置
    assert all(opacity(c, n) == 0.0 for n in news)
    assert all(opacity(c, n) == 0.0 for k in "ab" for n in c._members(c.objects["e1"].spans[k]))  # 被目标顶替
    plan.tl.seek(0.5)  # 一秒的变形播到一半（smooth 缓动在 0.5 处正好一半）
    now = centers(c, "e2", "ab")
    for k in "ab":
        assert np.allclose(now[k], (start[k] + end[k]) / 2, atol=1e-6)  # 中途：正好在起点和终点中间
        assert not np.allclose(now[k], end[k])
    assert all(np.isclose(opacity(c, n), 0.5) for n in news)
    assert np.isclose(opacity(c, c.objects["e1"].node), 0.5)
    plan.tl.seek(plan.until)
    now = centers(c, "e2", "ab")
    for k in "ab":
        assert np.allclose(now[k], end[k], atol=1e-6)
    assert all(opacity(c, n) == 1.0 for n in news)
    # 结束：只剩目标
    assert opacity(c, c.objects["e1"].node) == 0.0 and set(c.visible) == {"e2"}
    assert scanned(c) == {"e2"}


def test_match_tex_with_replace_false_keeps_the_source():
    doc = eq_doc({"match": "tex", "replace": False})
    c = compiler(doc)
    play(c, doc["beats"][0])
    play(c, doc["beats"][1])
    assert set(c.visible) == {"e1", "e2"} and scanned(c) == {"e1", "e2"}


def test_changed_span_content_crossfades_and_auto_without_spans_falls_back():
    doc = eq_doc({"match": "auto"})
    doc["objects"]["e2"]["text"] = "[$x^2$]{#a} $+$ [$3x$]{#b}"
    c = compiler(doc)
    play(c, doc["beats"][0])
    plan = c._plan(doc["beats"][1])
    [anim] = plan.inner.items[0].anim.anims
    assert isinstance(anim, TransformMatching)
    assert [p[2] for p in anim.pairs] == [False, True]  # #b 的内容变了：交叉淡入淡出
    plan.tl.seek(plan.until)
    assert scanned(c) == {"e2"}
    doc = {**META, "objects": {"m1": {"type": "math", "tex": "a+b", "at": [0, 2]}, "m2": {"type": "math", "tex": "b+a"}},
           "beats": [{"id": "B", "do": [{"act": "show", "target": "m1"},
                                        {"act": "transform", "from": "m1", "to": "m2", "match": "auto"}]}]}
    c = compiler(doc)
    anims, _d = c.animations({"act": "show", "target": "m1"})
    anims, _d = c.animations(doc["beats"][0]["do"][1])
    assert isinstance(anims[0], TransformCopy)  # 没有片段：整体变形
    assert any(isinstance(a, Fade) and a.node is c.objects["m1"].node for a in anims)  # auto 默认替换源


# ---------- transform: replace / 默认 ----------

def whole_doc(action: dict) -> dict:
    return {**META, "objects": {"a": {"type": "math", "tex": "a+b", "at": [-3, 0]},
                                "b": {"type": "math", "tex": "b+a", "at": [3, 0]}},
            "beats": [{"id": "B", "do": [{"act": "show", "target": "a"}, {"act": "focus", "target": "a"},
                                         {"act": "transform", "from": "a", "to": "b", **action}]}]}


def test_default_transform_is_unchanged_transform_copy():
    doc = whole_doc({})
    c = compiler(doc)
    c.animations(doc["beats"][0]["do"][0])
    anims, d = c.animations(doc["beats"][0]["do"][2])
    assert len(anims) == 1 and isinstance(anims[0], TransformCopy) and d == 1.0
    c = compiler(doc)
    play(c, doc["beats"][0])
    assert set(c.visible) == {"a", "b"} and scanned(c) == {"a", "b"}  # 源保持不动
    assert "a" in c.focus_boxes


def test_replace_true_removes_the_source_from_screen_and_checks():
    doc = whole_doc({"replace": True})
    c = compiler(doc)
    play(c, doc["beats"][0])
    assert opacity(c, c.objects["a"].node) == 0.0
    assert set(c.visible) == {"b"}  # 版式求解、重叠检查不再看它
    assert scanned(c) == {"b"}  # lint 扫描也看不到它（强调框一起收掉了）
    assert "a" not in c.focus_boxes
    assert analysis.lifetimes(doc) == {"a": {0}, "b": {0}}
    later = {**doc, "beats": doc["beats"] + [{"id": "C", "do": []}]}
    assert analysis.lifetimes(later)["a"] == {0}  # 下一个 beat 不再占着画面
    c = compiler(doc)
    assert not c.inspect(None, None)


def test_replace_changes_the_beat_cache_key():
    keys = []
    for action in ({}, {"replace": True}):
        doc = whole_doc(action)
        c = compiler(doc)
        base = c._cache_base()
        plan = c._plan(doc["beats"][0])
        keys.append(c._beat_key(base, "", doc["beats"][0], plan))
    assert keys[0] != keys[1]


@pytest.mark.parametrize("bad", [{"g": {"type": "group", "children": ["c", "c"]}}])
def test_member_in_two_places_is_rejected(bad):
    with pytest.raises(ValueError):
        compiler({**META, "objects": {**SHAPES, **bad}, "beats": []})
