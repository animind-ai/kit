"""无音频估算（inspect）的书签位置和句长。

真值来自 tests/fixtures/timing_truth.json：每个念出来的字一个固定的时间戳（字符表和书签位置是手写的，
voice 的 tests/fixtures/align/timing_truth.json 是同一份文件，voice 的 align 用这些时间戳跑过，书签时刻和它一致）。
这里只用时间戳算比例，不调用生产代码的计数规则。
"""

import json
import re
from pathlib import Path

import pytest
import yaml

from animind_kit import render, theme, timing
from animind_kit.backends import manim_backend, moxian_backend

T = theme.theme("paper")
TRUTH = json.loads((Path(__file__).parent / "fixtures" / "timing_truth.json").read_text(encoding="utf-8"))


def _beat(text: str) -> dict:
    return {"id": "B", "say": [{"id": "s1", "pace": "normal", "text": text}]}


def _estimated(text: str, lang: str = "zh") -> dict[str, float]:
    sentence = timing.load(_beat(text), None, lang).sentences[0]
    span = sentence.end - sentence.start
    return {name: (t - sentence.start) / span for name, t in sentence.marks.items()}


def _truth(case) -> dict[str, float]:
    """真值：书签那个字的开始时刻，在整句（第一个字开始到最后一个字结束）里的比例。"""
    chars = case["chars"]
    start, end = chars[0][1], chars[-1][2]
    return {name: (chars[i][1] - start) / (end - start) for name, i in case["marks"].items()}


@pytest.mark.parametrize("case", TRUTH, ids=[c["text"][:12] for c in TRUTH])
def test_fixture_is_the_spoken_text(case):
    """固定时间戳的字符表和 normalize 的读法一致（去掉空白和书签）。"""
    spoken = re.sub(r"\{@[A-Za-z_][A-Za-z0-9_]*\}", "", timing.spoken(case["text"]))
    assert "".join(spoken.split()) == "".join(ch for ch, _, _ in case["chars"])


@pytest.mark.parametrize("case", TRUTH, ids=[c["text"][:12] for c in TRUTH])
def test_estimated_fractions_match_fixed_timestamps(case):
    """无音频估算的书签相对句首比例，和固定时间戳上的真值差不到 1%（标点计一个字长的停顿，和固定时间戳一致）。"""
    est, truth = _estimated(case["text"]), _truth(case)
    assert est.keys() == truth.keys()
    for name in truth:
        assert abs(est[name] - truth[name]) < 0.01, (name, est[name], truth[name])


def test_comma_pause_is_one_character_long():
    """复现 4：“$x$，{@a}代入$1$”：逗号是一个字长的停顿。真值：x、逗号、代、入、一，各 0.22 秒，书签在“代”前 0.44/1.10 = 0.4。
    （早先把逗号当成零时长算出的 0.25，不是固定时间戳的结果，已经去掉。）"""
    case = TRUTH[0]
    assert _truth(case)["a"] == pytest.approx(0.4)
    assert _estimated(case["text"])["a"] == pytest.approx(0.4)


def test_before_after_fraction_deviation():
    """改之前（数 LaTeX 源码和标点）和改之后各自离固定时间戳的真值多远。"""
    import re

    def old(text):
        total = max(1, len(re.sub(r"\{@[^}]*\}", "", text)))
        return {m.group(1): len(re.sub(r"\{@[^}]*\}", "", text[: m.start()])) / total
                for m in re.finditer(r"\{@([A-Za-z_][A-Za-z0-9_]*)\}", text)}

    before, after = [], []
    for case in TRUTH:
        truth, new = _truth(case), _estimated(case["text"])
        for name in truth:
            before.append(abs(old(case["text"])[name] - truth[name]))
            after.append(abs(new[name] - truth[name]))
    print(f"书签相对句首比例偏差（对固定时间戳真值）：改之前最大 {max(before):.1%}，改之后最大 {max(after):.1%}")
    assert max(before) > 0.10
    assert max(after) < 0.01


def test_sentence_length_counts_every_non_space_character():
    case = TRUTH[1]
    sentence = timing.load(_beat(case["text"]), None).sentences[0]
    assert sentence.end - sentence.start == pytest.approx(len(case["chars"]) * 0.22)


def test_english_estimate_keeps_raw_counting():
    text = "one {@a}two three."
    sentence = timing.load(_beat(text), None, "en").sentences[0]
    assert sentence.end - sentence.start == pytest.approx(max(1.0, len("one two three.") * 0.22))
    assert sentence.marks["a"] == pytest.approx(sentence.start + (sentence.end - sentence.start) * 4 / 14)  # "one " 之后


def test_aligned_marks_still_win_over_estimate(tmp_path):
    """有音频时书签用 timing.json 里的实测时刻；估算只用来补没有命中的书签。"""
    (tmp_path / "B.timing.json").write_text(json.dumps({
        "cues": [{"start": 0.3, "end": 2.3}], "total": 2.6, "marks": {"s1": {"a": 1.1}}}), encoding="utf-8")
    sentence = timing.load(_beat("先看{@a}$\\frac{1}{2}$。"), tmp_path).sentences[0]
    assert sentence.marks["a"] == 1.1


# ---------- 后端入口：场景语言必须一路传到 timing.load ----------

EN_DOC = yaml.safe_load("""
spec: "0.1"
meta: {id: en, title: en, line: worked, author: t, license: CC-BY-4.0}
pedagogy: {insight: en, status: method}
style: {theme: paper, subtitles: none, lang: en}
objects:
  eq: {type: math, tex: 'a+b=c', in: stage}
beats:
  - id: One
    say:
      - {id: s1, pace: normal, text: "Look {@a}at $\\\\frac{1}{2}$ here."}
    do:
      - {at: s1, act: show, target: eq}
""")
ZH_DOC = yaml.safe_load(yaml.safe_dump(EN_DOC).replace("lang: en", "lang: zh"))


@pytest.fixture
def lang_spy(monkeypatch):
    seen = []
    real = timing.load

    def spy(beat, audio_dir, lang="zh"):
        seen.append(lang)
        return real(beat, audio_dir, lang)

    monkeypatch.setattr(timing, "load", spy)
    return seen


def test_moxian_backend_passes_scene_lang(lang_spy):
    compiler = moxian_backend.MoxianCompiler(EN_DOC, T, None, log=lambda *_: None)
    compiler._plan(EN_DOC["beats"][0])
    assert lang_spy == ["en"]


def test_manim_backend_passes_scene_lang(lang_spy):
    compiler = manim_backend.Compiler(EN_DOC, T, None, log=lambda *_: None)
    compiler.scene_class(EN_DOC["beats"][0])
    assert lang_spy == ["en"]


def test_render_estimate_passes_scene_lang(lang_spy):
    render.beat_info_estimated(EN_DOC, None)
    assert lang_spy == ["en"]


def test_zh_scene_still_reads_formulas(lang_spy):
    """中文场景照旧：公式展开成读法，书签按读法定位。"""
    beat = ZH_DOC["beats"][0]
    beat["say"][0]["text"] = "看{@a}$\\frac{1}{2}$。"
    render.beat_info_estimated(ZH_DOC, None)
    assert lang_spy == ["zh"]
    sentence = timing.load(beat, None, "zh").sentences[0]
    assert sentence.marks["a"] == pytest.approx(sentence.start + (sentence.end - sentence.start) * 1 / 6)  # 看 | 二分之一。（句号计一个字）
