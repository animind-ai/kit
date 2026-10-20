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
    # 字幕带不在版式里：压到原来一行字幕条的位置不再算出界
    assert not R.check_boxes({"z": (-3.0, 3.0, -3.65, -3.0)}, set(), template="v2")
