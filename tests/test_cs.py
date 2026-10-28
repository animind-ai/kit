"""编程课组件（mx/cs.py）：不渲染视频，只看节点、暴露的部件和动作前后的状态。"""

import pytest

from animind_kit import theme
from animind_kit.mx import cs, widgets
from animind_kit.mx.text import FONTS, set_fonts
from animind_kit.regions import FRAME_WIDTH, MARGIN, regions

T = theme.theme("classroom")
FACT = "def fact(n):\n    if n == 0:\n        return 1\n    else:\n        return n * fact(n - 1)\n"


def run(anims):
    for a in anims:
        a.begin()
        a.finish()


def make(name: str, **definition):
    return widgets.lookup(name)(name, {"widget": name, **definition}, T, {})


def opacity(node) -> float:
    return node.style.opacity if node.style else 1.0


def test_all_registered():
    for name in ("heading", "code", "terminal", "call_stack", "bindings", "card", "pill", "bullets", "flow", "image",
                 "queue"):
        assert widgets.lookup(name) is cs.REGISTRY[name]


def test_classroom_theme_matches_the_manim_look():
    assert (T.BG, T.A, T.HL, T.NO, T.CARD, T.MARK) == ("#F6F5F1", "#0F6E73", "#C27A1A", "#B03A2E", "#E7EBE8", "#F6E3BE")


def test_lecture_template_has_a_thin_top_band_and_half_width_side():
    r = regions(template="lecture")
    assert r["top"].height == pytest.approx(1.4)
    assert r["side"].width == pytest.approx(r["stage"].width)
    assert regions() == regions(template="default")


def test_fonts_fall_back_when_missing():
    logged = []
    set_fonts({"text": "No Such Font 123"}, logged.append)
    assert FONTS["text"] != "No Such Font 123" and logged
    set_fonts(None)


def test_code_lines_spans_and_highlight():
    w = make("code", src=FACT, spans={"call": {"line": 4, "text": "fact(n - 1)"}})
    assert len(w.built.parts["line"]) == 5
    assert len(w.built.spans["call"].children) == 9  # 空格不是字形：fact(n-1) 九个字
    glyphs = [g.world_bbox(w.node) for g in w.built.spans["call"].children]  # 片段只是引用容器，按树里的位置量
    row = w.rows[4].bbox()
    assert max(g.x1 for g in glyphs) <= row.x1 + 1e-6 and min(g.x0 for g in glyphs) > row.x0 + 1.0
    anims = w.action("highlight", {"lines": [1, 2]})
    assert all(opacity(b) == 0.0 for b in w.marks.values())  # 建动作时还藏着
    run(anims)
    bars = list(w.marks.values())
    assert len(bars) == 2 and all(opacity(b) == 1.0 for b in bars)
    assert abs(bars[0].center()[1] - w.rows[1].bbox().center[1]) < 0.1  # 底色条和那一行对齐
    run(w.action("unhighlight", {}))
    assert not w.marks and all(opacity(b) == 0.0 for b in bars)


def test_code_pointer_moves_between_lines():
    w = make("code", src=FACT)
    run(w.action("point", {"line": 0}))
    y0 = w.pointer.center()[1]
    run(w.action("point", {"line": 4}))
    assert w.pointer.center()[1] == pytest.approx(y0 - 4 * w.pitch)
    assert w.pointer.center()[0] < w.rows[0].bbox().x0  # 在面板左边


def test_terminal_keeps_indentation():
    w = make("terminal", lines=["fact(3)", "    fact(2)", {"text": "Error", "role": "NO"}])
    rows = w.built.items
    assert len(rows) == 3
    assert rows[1].bbox().x0 - rows[0].bbox().x0 == pytest.approx(4 * cs.char_width(w.SIZE), abs=0.02)
    assert rows[2].style.fill != rows[0].style.fill


def test_call_stack_push_note_pop_keeps_its_box():
    w = make("call_stack", depth=4)
    box = w.node.bbox()
    run(w.action("push", {"name": "fact", "vars": {"n": 3}}))
    run(w.action("note", {"text": "waiting"}))
    run(w.action("push", {"name": "fact", "vars": {"n": 2}}))
    frames = w.built.parts["frame"]
    assert len(frames) == 2 and frames[1].center()[1] > frames[0].center()[1]  # 新帧压在上面
    assert len(w.built.parts["value"]) == 2 and len(w.built.parts["note"]) == 1
    note = w.built.parts["note"][0]
    assert note.bbox().x0 > frames[0].bbox().x1  # 状态标签在帧的右边
    top = frames[1]
    run(w.action("pop", {}))
    assert opacity(top) == 0.0 and len(w.built.parts["frame"]) == 1
    assert w.node.bbox().width == pytest.approx(box.width) and w.node.bbox().height == pytest.approx(box.height)
    run(w.action("unnote", {}))
    assert opacity(note) == 0.0 and w.built.parts["note"] == []


def test_bindings_rebind_moves_the_arrow():
    w = make("bindings", names=["a", "b"], objects={"o1": "[1, 2]", "o2": "[3]"}, bind={"a": "o1", "b": "o1"})
    o2 = w.boxes["o2"]
    assert opacity(o2) == 0.0  # 没人指着的对象一开始藏着
    before = w.arrows["b"].bbox()
    run(w.action("rebind", {"name": "b", "to": "o2"}))
    assert opacity(o2) == 1.0 and w.arrows["b"].bbox().y0 < before.y0  # 箭头转向下面的对象


def test_heading_fills_the_top_band_and_retitles():
    w = make("heading", text="Example: factorial", kicker="AIE1001 · Lecture 6")
    assert w.node.bbox().width == pytest.approx((FRAME_WIDTH - 2 * MARGIN) * 0.96)
    old = w.title
    run(w.action("retitle", {"text": "The call stack"}))
    assert opacity(old) == 0.0 and w.built.spans["title"] is w.title and w.title.text == "The call stack"


def test_card_pill_bullets():
    c = make("card", title="Base case", body="the smallest problem\nanswered directly", cols=26, rows=2)
    assert set(c.built.spans) == {"title", "body"} and c.node.bbox().width > 3.5
    assert make("pill", text="base case").node.bbox().height < 0.7
    b = make("bullets", items=["one", "two", "three"])
    assert len(b.built.items) == 3


def test_flow_nodes_on_a_grid_with_routed_edges():
    w = make("flow", nodes={"a": {"text": "Start", "row": 0, "col": 0}, "b": {"text": "Tool use?", "row": 1, "col": 0,
                                                                         "shape": "diamond"},
                            "c": {"text": "Call tool", "row": 1, "col": 1}},
             edges=[{"from": "a", "to": "b"}, {"from": "b", "to": "c", "label": "YES"},
                    {"from": "c", "to": "a", "via": "right"}])
    assert set(w.built.spans) == {"a", "b", "c"} and len(w.built.parts["edge"]) == 3
    a, b = w.boxes["a"], w.boxes["b"]
    assert a.center[1] > b.center[1] and a.center[0] == pytest.approx(b.center[0])
    assert w.built.parts["edge"][2].bbox().x1 > w.boxes["c"].x1  # 从右边绕回去


def test_image_keeps_aspect(tmp_path):
    import skia

    surface = skia.Surface(80, 40)
    with surface as canvas:
        canvas.clear(skia.ColorRED)
    surface.makeImageSnapshot().save(str(tmp_path / "s.png"), skia.kPNG)
    w = widgets.lookup("image")("im", {"widget": "image", "src": "s.png"}, T, {}, opts={"base_dir": tmp_path})
    b = w.node.bbox()
    assert b.width == pytest.approx(2 * b.height)


def test_queue_ask_and_number():
    w = make("queue", people=6, you=5, label="STAGE")
    box = w.node.bbox()
    run(w.action("ask", {}))
    run(w.action("number", {}))
    assert len(w.tags) == 6 and all(opacity(t) == 1.0 for t in w.tags)
    assert w.tags[0].center()[0] > w.tags[5].center()[0]  # 1 号在队首（右边）
    assert w.node.bbox().height == pytest.approx(box.height)


def test_code_reserves_a_gutter_for_its_jump_arrow():
    plain = make("code", src=FACT)
    w = widgets.lookup("code")("c", {"widget": "code", "src": FACT}, T, {}, opts={"acts": {"jump"}})
    before = w.node.bbox()
    assert before.width > plain.node.bbox().width + 0.5
    run(w.action("jump", {"from": 4, "to": 0}))
    after = w.node.bbox()
    assert after.x0 >= before.x0 - 1e-6  # 弯箭头画在预留的空里，包围盒不变
