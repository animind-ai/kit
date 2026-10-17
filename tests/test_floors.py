"""字号下限（数轴标注、侧栏、练一练框）：lint 按类别判，后端排版后把数轴标注抬到下限。"""

import pytest

from animind_kit import lint, subtitles, theme
from animind_kit.backends.moxian_backend import MoxianCompiler
from animind_kit.lint import Config, Thing, check
from animind_kit.mx import scan

T = theme.theme("paper")


def glyph(em: float, floor: str | None) -> Thing:
    return Thing("text", "a", "a", (0.0, 0.0, 0.3, 0.4), 10, 0, "x", em, floor=floor)


def test_floors_are_final_720p_pixels_scaled_for_burned_subtitles():
    plain = lint.floors()
    assert plain["label"] == pytest.approx(54.0)  # 720p 36px = 1080p 54px
    burned = lint.floors(subtitles.picture_scale("burned"))
    assert burned["label"] == pytest.approx(54.0 / (1 - subtitles.BAND))
    assert burned["side"] > plain["side"] and plain["practice"] >= 36.0


def test_small_uses_the_floor_of_the_glyph_kind():
    cfg = Config(floors=lint.floors())
    assert check([glyph(50.0, None)], cfg) == []  # 普通文字只看 min_font
    assert [p.kind for p in check([glyph(50.0, "label")], cfg)] == ["small"]
    assert check([glyph(55.0, "label")], cfg) == []
    assert "label 下限" in check([glyph(50.0, "label")], cfg)[0].detail


def _doc(subs: str) -> dict:
    return {
        "spec": "0.1",
        "meta": {"id": "t", "title": "t", "line": "insight", "author": "t", "license": "CC-BY-4.0"},
        "style": {"theme": "paper", "subtitles": subs},
        "objects": {
            "q": {"type": "text", "text": "标题", "level": "title", "in": "top"},
            "line": {"widget": "number_line", "range": [0, 1], "parts": 4, "fractions": True, "measures": ["1/4"],
                     "unit": 9, "bars": True, "in": "stage"},
            "note": {"type": "text", "text": "底部一行", "in": "footer"},
            "tip": {"type": "text", "text": "侧栏提示", "in": "side"},
            "practice": {"widget": "think_box", "lines": ["[练一练]{.HL}", "一行"], "below": "tip"},
        },
        "beats": [{"id": "b1", "do": [{"act": "show", "target": k} for k in ("q", "line", "note", "tip", "practice")]
                   + [{"act": "mark_point", "target": "line", "args": {"at": "1/2", "label": r"\frac{1}{2}"}}]}],
    }


@pytest.mark.parametrize("subs", ["burned", "soft"])
def test_number_line_labels_are_raised_to_the_floor_after_layout(subs):
    c = MoxianCompiler(_doc(subs), T, None, log=lambda *_: None, quality="l")
    assert c.inspect(None, None) == []
    things = scan.scan(c.root, c.labels(), c.camera)
    floors = c.lint_config.floors
    labels = [t for t in things if t.kind == "text" and t.floor == "label"]
    assert labels and min(t.em for t in labels) >= floors["label"] - 0.05
    assert any(r"\dfrac" in t.text for t in labels)  # 分数标注是 \dfrac，不是缩小的上下标
    side = [t for t in things if t.kind == "text" and t.floor in ("side", "practice")]
    assert {t.floor for t in side} == {"side", "practice"}
    assert min(t.em for t in side) >= min(floors["side"], floors["practice"]) - 0.05
