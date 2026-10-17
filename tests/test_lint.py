"""字形级版式检查：lint 的几何判断、墨线对象树的扫描、inspect 命令（不编码视频）。"""

import numpy as np
from moxian.core import Group, PathNode, Style
from moxian.path import line, rect
from moxian.text import Text

from animind_kit import lint, theme
from animind_kit.backends.moxian_backend import MoxianCompiler, _Sample, _Sampling
from animind_kit.lint import PX, Allow, Config, Thing, check
from animind_kit.mx import scan

T = theme.theme("paper")


def glyph(owner="a", box=(0.0, 0.0, 0.3, 0.4), run=0, em=40.0, text="x", obj=None, order=10):
    return Thing("text", owner, obj or owner, box, order, run, text, em)


def stroke(owner="l", pts=((-1.0, 0.2), (1.0, 0.2)), obj=None, act=None, order=20, fill=0.0, closed=False):
    pts = np.array(pts, dtype=float)
    return Thing("shape", owner, obj or owner, (pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()),
                 order, act=act, curves=[pts], closed=[closed], stroked=fill == 0.0, fill=fill)


def kinds(problems):
    return sorted(p.kind for p in problems)


# ---------- 几何 ----------

def test_line_through_glyph_is_reported():
    problems = check([glyph(), stroke()])
    assert kinds(problems) == ["cross"]
    assert problems[0].subject == "a" and problems[0].other == "l"


def test_line_beside_glyph_is_not():
    assert check([glyph(), stroke(pts=((-1.0, -0.1), (1.0, -0.1)))]) == []


def test_touch_tolerance_grows_the_glyph_box():
    near = stroke(pts=((-1.0, -2 * PX), (1.0, -2 * PX)))
    assert check([glyph(), near]) == []
    assert kinds(check([glyph(), near], Config(touch=3))) == ["cross"]


def test_cross_on_its_own_target_is_intended():
    deco = stroke(owner="cross(a)", obj="a", act="cross")
    assert check([glyph(), deco]) == []
    other = stroke(owner="cross(b)", obj="b", act="cross")
    assert kinds(check([glyph(), other])) == ["cross"]


def test_mark_pad_edge_is_not_a_crossing():
    pad = stroke(owner="mark(b)", obj="b", act="mark", fill=1.0, closed=True, order=0,
                 pts=((-1.0, -1.0), (0.1, -1.0), (0.1, 1.0), (-1.0, 1.0)))
    assert check([glyph(), pad]) == []


def test_line_hidden_behind_opaque_backdrop():
    behind = stroke(order=1)
    card = stroke(owner="card", order=5, fill=1.0, closed=True, pts=((-2.0, -2.0), (2.0, -2.0), (2.0, 2.0), (-2.0, 2.0)))
    assert check([glyph(), behind, card]) == []


def test_opaque_fill_over_text_is_covered():
    lid = stroke(owner="lid", order=30, fill=1.0, closed=True, pts=((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)))
    assert kinds(check([glyph(), lid])) == ["covered"]


def test_small_text_uses_font_size_and_min_font():
    assert kinds(check([glyph(em=12.0)])) == ["small"]
    assert check([glyph(em=12.0)], Config(min_font=10)) == []
    assert check([glyph(em=None)]) == []  # 残影：没有字号


def test_overlap_between_runs_not_within_one():
    a = glyph("a", (0.0, 0.0, 0.3, 0.4), run=0)
    b = glyph("b", (0.1, 0.1, 0.4, 0.5), run=1)
    assert kinds(check([a, b])) == ["overlap"]
    assert check([a, glyph("a", (0.1, 0.1, 0.4, 0.5), run=0)]) == []
    assert check([a, b], ignore_pairs={frozenset(("a", "b"))}) == []


def test_clip_partial_but_not_fully_outside():
    edge = lint.FRAME[2]
    assert kinds(check([glyph(box=(edge - 0.2, 0.0, edge + 0.2, 0.3))])) == ["clip"]
    assert check([glyph(box=(edge + 1.0, 0.0, edge + 1.3, 0.3))]) == []
    # 镜头推近：跟镜头走的东西被裁掉是预期的，钉住的不是
    zoomed = glyph(box=(edge - 0.2, 0.0, edge + 0.2, 0.3))
    assert check([zoomed], framed=True) == []
    zoomed.follows = False
    assert kinds(check([zoomed], framed=True)) == ["clip"]


def test_allow_is_symmetric_and_per_kind():
    allow = Allow({"l": {"cross": ["a"]}, "b": {"small": True, "clip": True}})
    assert check([glyph(), stroke()], allow=allow) == []
    assert check([glyph(), stroke()], allow=Allow({"a": {"cross": True}})) == []
    assert kinds(check([glyph(), stroke()], allow=Allow({"a": {"overlap": True}}))) == ["cross"]
    assert check([glyph("b", em=8.0)], allow=allow) == []


def test_allow_from_doc():
    doc = {"objects": {"a": {"type": "text", "text": "x", "allow": {"cross": True}}, "b": {"type": "text", "text": "y"}}}
    assert Allow.from_doc(doc).rules == {"a": {"cross": True}}


def test_tracker_merges_moments_and_numbers_by_first_sight():
    tr = lint.Tracker()
    p = check([glyph(), stroke()])
    tr.see(lint.Moment("b1", 1.0, "do[0] show"), p)
    tr.see(lint.Moment("b1", 2.5, "do[2] show"), p)
    tr.see(lint.Moment("b2", 0.7, "do[0] hide"), check([glyph(em=10.0)]))
    first, second = tr.findings
    assert (first.number, second.number) == (1, 2)
    assert [m.time for m in first.moments] == [1.0, 2.5]
    assert first.line().startswith("[1] b1 t=1.0–2.5s do[0] show  cross  a × l")


# ---------- 墨线对象树 ----------

def test_scan_finds_glyphs_and_lines_in_one_object():
    label = Text("12", 30, "#000000")
    tick = PathNode(path=line(-1.0, 0.1, 1.0, 0.1), style=Style(stroke="#000000", stroke_width=2))
    obj = Group(label, tick, name="axis")
    root = Group(obj, name="root")
    things = scan.scan(root, {id(obj): scan.Label("axis", "axis")})
    texts = [t for t in things if t.kind == "text"]
    assert len(texts) == 2 and all(abs(t.em - 30.0) < 1e-6 for t in texts)
    assert {t.text for t in texts} == {"12"}
    assert kinds(check(things)) == ["cross"]  # 同一对象内部：刻度线穿过标签


def test_scan_font_size_follows_scale_and_skips_invisible():
    label = Text("ab", 40, "#000000")
    label.scale(0.25)
    hidden = PathNode(path=rect(1, 1), style=Style(fill="#000000", opacity=0.0))
    obj = Group(label, hidden)
    things = scan.scan(Group(obj), {id(obj): scan.Label("t", "t")})
    assert len(things) == 2
    assert abs(things[0].em - 10.0) < 1e-6


def test_sampling_wrapper_fires_once_per_sample_in_order():
    seen = []

    class Fake:
        end = 2.0

        def seek(self, t):
            return True

        def frames(self, fps=30, until=None):
            return iter(())

    s = _Sampling(Fake(), [_Sample(0.5, "a"), _Sample(0.5, "b"), _Sample(1.5, "c")], lambda x: seen.append(x.source))
    for t in (0.0, 0.4, 0.6, 1.0, 1.6, 2.0):
        s.seek(t)
    assert seen == ["a", "b", "c"]


# ---------- inspect ----------

DOC = {
    "spec": "0.1",
    "meta": {"id": "t", "title": "t", "line": "worked"},
    "style": {"theme": "paper"},
    "objects": {
        "eq": {"type": "math", "tex": "a^2+b^2=c^2", "at": [0.0, 0.0]},
        "twin": {"type": "math", "tex": "x+y", "at": [0.0, 0.0]},
    },
    "beats": [
        {"id": "b1", "do": [{"act": "show", "target": "eq"}, {"act": "cross", "target": "eq"}]},
        {"id": "b2", "do": [{"act": "show", "target": "twin"}]},
    ],
}


def test_inspect_reports_with_beat_time_source_and_writes_sheets(tmp_path):
    c = MoxianCompiler(DOC, T, None, log=lambda *_: None, quality="l")
    findings = c.inspect(None, tmp_path)
    found = {(f.problem.kind, f.problem.subject, f.problem.other) for f in findings}
    # 划掉 eq 自己不报；b2 里 twin 叠在 eq 上：字压字，eq 上的叉也穿过 twin 的字
    assert ("overlap", "eq", "twin") in found or ("overlap", "twin", "eq") in found
    assert ("cross", "twin", "cross(eq)") in found
    assert all(f.moments[0].beat == "b2" for f in findings)
    assert all(f.moments[0].source.startswith("do[0] show") for f in findings)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b1.storyboard.png", "b2.storyboard.png"]
    lines = [p for p in c.problems if p.startswith("[")]
    assert len(lines) == len(findings) and "\\displaystyle" not in "".join(lines)


def test_inspect_only_selected_beat_and_allow_silences(tmp_path):
    doc = {**DOC, "objects": {**DOC["objects"], "twin": {**DOC["objects"]["twin"], "allow": {"overlap": True, "cross": True}}}}
    c = MoxianCompiler(doc, T, None, log=lambda *_: None, quality="l")
    assert c.inspect({"b1"}, tmp_path) == []
    assert [p.name for p in tmp_path.iterdir()] == ["b1.storyboard.png"]
    assert MoxianCompiler(doc, T, None, log=lambda *_: None, quality="l").inspect(None, None) == []
