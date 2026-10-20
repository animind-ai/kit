"""English-first scenes (style.lang: en): default language, timing estimate, subtitle track tag, Latin font."""

import copy
import json
from pathlib import Path

import pytest
import scene_spec
from scene_spec.loader import load_file

from animind_kit import subtitles, theme, timing
from animind_kit.mx import text as mxtext
from animind_kit.render import load_spec

EXAMPLE = Path(__file__).resolve().parents[2] / "scene-spec" / "examples" / "s67-p01.scene.yaml"


def english_first() -> dict:
    doc = copy.deepcopy(load_file(EXAMPLE))
    doc.setdefault("style", {})["lang"] = "en"
    for beat in doc["beats"]:
        for s in beat.get("say", []):
            s.pop("en", None)
    return doc


@pytest.mark.skipif(not hasattr(scene_spec, "source_lang"),
                    reason="English-first localize needs scene-spec with source_lang (animind-ai/scene-spec#2)")
def test_render_defaults_to_the_files_own_language(tmp_path):
    path = tmp_path / "en.scene.yaml"
    path.write_text(json.dumps(english_first(), ensure_ascii=False), encoding="utf-8")
    doc = load_spec(path)  # 以前：没有 --lang 当中文读；--lang en 要求每句都有 en
    assert doc["style"]["lang"] == "en"
    assert load_spec(path, "en")["beats"] == doc["beats"]


def test_estimate_depends_on_language_and_pace():
    say = [{"id": "s1", "text": "x" * 117}]
    zh = timing.estimate(say, {})[0][0]
    en = timing.estimate(say, {"lang": "en"})[0][0]
    assert abs((zh["end"] - zh["start"]) - 117 * 0.22) < 1e-9
    assert abs((en["end"] - en["start"]) - 10.0) < 1e-9
    slow = timing.estimate([{**say[0], "pace": "slow"}], {"lang": "en"})[0][0]
    fast = timing.estimate(say, {"lang": "en", "voice": {"speed": 1.25}})[0][0]
    assert slow["end"] - slow["start"] > 10.0 > fast["end"] - fast["start"]


def test_estimate_gap_follows_pace():
    say = [{"id": "s1", "text": "x" * 50, "pace": "slow"}, {"id": "s2", "text": "y" * 50}]
    cues, _ = timing.estimate(say, {"lang": "en"})
    assert abs(cues[1]["start"] - cues[0]["end"] - timing.GAP["slow"]) < 1e-9


def test_chinese_estimate_unchanged_for_normal_pace():
    beat = {"id": "X", "say": [{"id": "s1", "text": "一二三四五"}, {"id": "s2", "text": "六七八九十"}]}
    t = timing.load(beat, None)
    assert abs(t.sentences[0].start - 0.3) < 1e-9 and abs(t.sentences[1].start - (0.3 + 1.1 + 0.35)) < 1e-9


def test_soft_subtitle_track_language():
    assert subtitles.track_language({"style": {"lang": "en"}}) == "eng"
    assert subtitles.track_language({}) == "chi"


def test_latin_font_for_english_text_only():
    try:
        mxtext.use_language("en")
        latin = mxtext.latin_font()
        if latin:
            assert mxtext.family_for("Peiyu AI · Boxi Yu") == latin
        assert mxtext.family_for("勾股定理") == mxtext.DEFAULT_FONT
        mxtext.use_language("zh")
        assert mxtext.family_for("Peiyu AI") == mxtext.DEFAULT_FONT
    finally:
        mxtext.use_language("zh")


def test_classroom_theme_uses_its_card_colour():
    t = theme.theme("classroom")
    assert t.is_light and t.CARD == "#E7EBE8" and t.ink("HL") == "#C27A1A"
