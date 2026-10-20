"""v2 版式（style.layout: v2）：无右侧栏、顶栏 1.0、满宽 stage、字幕沿用默认的双语两行烧录带、右下角 logo 位留空。"""

from animind_kit import regions as R
from animind_kit import subtitles

PX720 = 720 / R.FRAME_HEIGHT  # 90 px / 单位


def test_v2_stage_full_width_top_band():
    r = R.regions(template="v2", footer_used=False)
    stage, top, side = r["stage"], r["top"], r["side"]
    assert abs(stage.width - (R.FRAME_WIDTH - 2 * R.MARGIN)) < 1e-9  # 没有右侧栏
    assert side == R.Region("side", top.x0, top.x1, top.y0, top.y1)  # side 并进顶栏
    assert abs(top.height - 1.0) < 1e-9
    assert abs(stage.y1 - (top.y0 - 0.2)) < 1e-9
    assert abs(stage.y0 - (-R.FRAME_HEIGHT / 2 + R.MARGIN)) < 1e-9  # 底边不再给一行字幕条留位


def test_v2_uses_bilingual_band_scale():
    assert subtitles.DEFAULT == "burned"
    assert abs(subtitles.picture_scale("burned") - (1 - subtitles.BAND)) < 1e-9
    assert subtitles.picture_scale("soft") == 1.0
    assert not hasattr(subtitles, "burn_one_line")


def test_v2_stage_pixels_720p():
    stage = R.regions(template="v2", footer_used=False)["stage"]
    k = PX720 * subtitles.picture_scale("burned")
    w, h = round(stage.width * k), round(stage.height * k)
    assert (w, h) == (1079, 491)


def test_v2_logo_corner_checked():
    box = {"x": (6.0, 6.5, -3.6, -3.2)}
    assert any("logo" in p for p in R.check_boxes(box, set(), template="v2"))
    assert not any("logo" in p for p in R.check_boxes(box, set(), template="default"))
    assert not R.check_boxes({"y": (5.0, 6.0, -3.6, -3.2)}, set(), template="v2")
    # 烧录字幕时 logo 在画面外，不查
    assert not R.check_boxes(box, set(), template="v2", logo=False)
    # 字幕带不在版式里：压到原来一行字幕条的位置不再算出界
    assert not R.check_boxes({"z": (-3.0, 3.0, -3.65, -3.0)}, set(), template="v2")


def test_v2_min_font_is_render_size():
    from animind_kit.backends import moxian_backend as mb
    assert mb.V2_MIN_FONT == 40.5


def test_v2_side_column():
    r = R.regions(top_used=True, side_used=True, footer_used=False, template="v2", side_column=True)
    stage, side, top = r["stage"], r["side"], r["top"]
    assert abs(side.width - R.SIDE_WIDTH) < 1e-9 and abs(side.x1 - stage.x1 - R.SIDE_WIDTH - 0.2) < 1e-9
    assert side.y1 == stage.y1 == top.y0 - 0.2 and side.y0 == stage.y0
    # 没有 side 对象时 stage 仍占满宽度；默认（不开 side_column）side 仍是顶栏
    assert R.regions(side_used=False, template="v2", side_column=True)["stage"].width == R.FRAME_WIDTH - 2 * R.MARGIN
    assert R.regions(template="v2")["side"] == R.Region("side", top.x0, top.x1, top.y0, top.y1)
    # 只有 side、没有顶栏：stage 和 side 一直到安全区顶
    r2 = R.regions(top_used=False, side_used=True, footer_used=False, template="v2", side_column=True)
    assert abs(r2["stage"].y1 - (R.FRAME_HEIGHT / 2 - R.MARGIN)) < 1e-9


def test_v2_widget_min_text():
    """v2 后端给组件 opts["min_text"]：组件里比它小的字（计数、刻度、表头小字）抬到它；不给时字号不变（v1 不受影响）。"""
    from animind_kit.mx.numberline import NumberLine
    from animind_kit.mx.widgets import DiffTable
    from animind_kit import theme

    t = theme.theme("paper")
    line = {"widget": "number_line", "range": [0, 6], "parts": 1, "role": "A"}
    plain = NumberLine("line", line, t, {})
    assert plain.smallest_text() == min(NumberLine.COUNT, plain.label_size)
    raised = NumberLine("line", line, t, {}, opts={"min_text": 45.0})
    assert raised.smallest_text() == min(45.0, raised.label_size)
    table = {"widget": "diff_table", "rows": [{"data": "sq", "role": "A", "name": "位置"}]}
    data = {"sq": [1, 4, 9]}
    assert DiffTable("tab", table, t, data).smallest_text() == 17
    assert DiffTable("tab", table, t, data, opts={"min_text": 30.0}).smallest_text() == 30.0


def test_rich_text_line_breaks():
    """文字里的换行符分行（v2 英文版侧栏里的长句）；没有换行符时还是一行，片段可以跨行。"""
    from animind_kit import theme
    from animind_kit.mx.text import rich

    t = theme.theme("paper")
    one = rich("[Multiplying always]{#m} makes things bigger?", t, 36)
    two = rich("[Multiplying always]{#m}\nmakes things bigger?", t, 36)
    across = rich("[Measure finer and\na fraction fits?]{#m}", t, 36)
    assert two.node.width < one.node.width * 0.7
    assert two.node.height > one.node.height * 2
    assert set(two.spans) == {"m"} and set(across.spans) == {"m"}
    assert across.spans["m"].bbox() is not None
