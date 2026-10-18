"""字幕：显示文本、公式转 Unicode、切条、时刻、双语、文件格式、烧录（默认）和软字幕轨。"""

import json
import shutil
import subprocess

import pytest

from animind_kit import subtitles as sub
from animind_kit.render import render


@pytest.mark.parametrize("tex, text", [
    (r"6\div\frac{2}{3}", "6÷⅔"),
    (r"6\times\frac{3}{2}=9", "6×3/2=9"),
    (r"7\frac{2}{5}", "7⅖"),
    (r"7\frac{2}{15}", "7 2/15"),
    (r"A_{n+1}-A_n", "Aₙ₊₁-Aₙ"),
    (r"b_n=A_{n+1}-A_n", "bₙ=Aₙ₊₁-Aₙ"),
    (r"x^2+y^{10}", "x²+y¹⁰"),
    (r"a^{2}+b^2=c^2", "a²+b²=c²"),
    (r"\sqrt{2}", "√2"),
    (r"\sqrt{a+b}", "√(a+b)"),
    (r"\sqrt[3]{x}", "∛x"),
    (r"\frac{a+b}{2}", "(a+b)/2"),
    (r"\{A_n\}", "{Aₙ}"),
    (r"2,5,11,\cdots", "2,5,11,⋯"),
    (r"\angle ABC=90^\circ", "∠ABC=90°"),
    (r"x\in\mathbb{R}", "x∈ℝ"),
    (r"\text{面积}=\left(a+b\right)^2", "面积=(a+b)²"),
    (r"f'(x)\le 0", "f'(x)≤0"),
    (r"x_{\max}", "xₘₐₓ"),
    (r"\sin x+\log_2 8", "sin x+log₂8"),
    (r"\overbrace{a+b}", "a+b"),  # 不认识的命令：丢掉命令名，参数照常输出
    (r"6\div\frac{3}{4}=\ ?", "6÷¾= ?"),
])
def test_tex_to_unicode(tex, text):
    assert sub.tex_to_unicode(tex) == text


def test_display_text_strips_bookmarks_and_converts_math():
    assert sub.display_text("{@q}六除以三分之二，{@m}比六大？") == "六除以三分之二，比六大？"
    assert sub.display_text("所以 {@k}$a^2+b^2=c^2$。") == "所以 a²+b²=c²。"


def test_chunks_respect_the_limit_and_punctuation():
    text = "先往下做差。按定义，后一项减前一项：五减二，得三，再看下一项，十一减五得六。"
    pieces = [text[a:b] for a, b in sub.chunks(text)]
    assert "".join(pieces) == text
    assert all(sub.units(p.rstrip("，。；：、")) <= sub.MAX_UNITS for p in pieces)
    assert pieces[0].endswith("。") or pieces[0].endswith("：")


def test_long_clause_without_punctuation_is_hard_split():
    text = "一二三四五六七八九十" * 4
    pieces = [text[a:b] for a, b in sub.chunks(text)]
    assert "".join(pieces) == text and all(sub.units(p) <= sub.MAX_UNITS for p in pieces)


def test_sentence_cues_follow_bookmarks():
    text = "很多人觉得，{@m}除法让数变小。还有人觉得，{@n}乘法一定让数变大，这两种想法都不对。"
    cues = sub.sentence_cues(text, 1.0, 9.0, {"m": 2.0, "n": 6.5})
    assert cues[0].start == 1.0 and cues[-1].end == pytest.approx(9.0)
    assert all(c.text[-1] not in "，。" for c in cues)
    second = next(c for c in cues if c.text.startswith("乘法") or "乘法" in c.text)
    assert second.start >= 5.5  # 书签 n 在 6.5 秒，后半句不会提早出现
    assert all(a.end <= b.start + 1e-6 for a, b in zip(cues, cues[1:]))


def test_files_format():
    cues = [sub.Cue(0.3, 2.0, "六除以三分之二"), sub.Cue(3661.5, 3662.25, "九个！")]
    vtt, srt = sub.to_vtt(cues), sub.to_srt(cues)
    assert vtt.startswith("WEBVTT\n\n1\n00:00:00.300 --> 00:00:02.000\n六除以三分之二\n")
    assert "01:01:01.500 --> 01:01:02.250" in vtt
    assert srt.startswith("1\n00:00:00,300 --> 00:00:02,000\n六除以三分之二\n\n2\n01:01:01,500")


def test_english_pieces_are_balanced_and_complete():
    en = "Plenty of people say bigger, for sure, because multiplying makes things bigger."
    assert sub.en_pieces(en, 1) == [en]
    pieces = sub.en_pieces(en, 3)
    assert " ".join(pieces) == en and 1 < len(pieces) <= 3
    assert pieces[0].endswith(",")  # 优先在标点后切
    long = " ".join(["word"] * 40)
    assert all(len(p) <= sub.EN_MAX for p in sub.en_pieces(long, 5))


def test_bilingual_cues_carry_english_under_each_chinese_line():
    text = "很多人觉得一定更大，{@m}因为乘法会让数变大。"
    en = "Plenty of people say bigger, for sure, because multiplying makes things bigger."
    cues = sub.sentence_cues(text, 0.0, 4.0, {"m": 1.8}, en)
    assert len(cues) == 2 and all(c.en for c in cues)
    assert " ".join(dict.fromkeys(c.en for c in cues)) == en  # 英文不丢字、按顺序
    assert sub.to_srt(cues).split("\n")[2:4] == [cues[0].text, cues[0].en]


def test_mode_defaults_to_burned():
    assert sub.mode({}) == "burned" and sub.mode({"style": {"subtitles": "soft"}}) == "soft"
    assert sub.mode({"style": {"subtitles": "soft"}}, "none") == "none"
    assert sub.picture_scale("burned") == pytest.approx(1 - sub.BAND) and sub.picture_scale("soft") == 1.0
    with pytest.raises(ValueError):
        sub.mode({"style": {"subtitles": "loud"}})


def test_build_offsets_each_beat():
    doc = {"beats": [{"id": "A", "say": [{"text": "第一句。"}]}, {"id": "B", "say": [{"text": "第二句。"}]}]}
    info = {"A": {"cues": [{"start": 0.3, "end": 1.0, "marks": {}}]}, "B": {"cues": [{"start": 0.3, "end": 1.2, "marks": {}}]}}
    cues = sub.build(doc, [0.0, 5.0], info)
    assert [(c.start, c.end, c.text) for c in cues] == [(0.3, 1.0, "第一句"), (5.3, 6.2, "第二句")]


TINY = """\
spec: "0.1"
meta: {id: tiny, title: 小样, line: worked, author: t, license: CC-BY-4.0}
pedagogy: {insight: 小样, status: method}
style: {theme: paper, segmented: true, step_hold: 1.5, subtitles: SUBS}
objects:
  eq: {type: math, tex: 'a+b', in: stage}
beats:
  - id: One
    say:
      - {id: s1, pace: normal, text: "看这里，{@a}$a+b$。"}
      - {id: s2, pace: normal, text: "再看一遍。"}
    do:
      - {at: s1@a, act: show, target: eq}
      - {at: s2, act: step}
"""


def _streams(path) -> list[str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return [f"{s['codec_type']}:{s['codec_name']}" for s in json.loads(out)["streams"]]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_exports_soft_subtitles(tmp_path):
    spec = tmp_path / "tiny.scene.yaml"
    spec.write_text(TINY.replace("SUBS", "soft"), encoding="utf-8")
    out = tmp_path / "tiny.mp4"
    summary = render(spec, out, None, "l", log=lambda *_: None, backend="moxian", burn_subtitles=True)
    vtt = (tmp_path / "tiny.vtt").read_text(encoding="utf-8")
    srt = (tmp_path / "tiny.srt").read_text(encoding="utf-8")
    assert "看这里，a+b" in vtt and "{@" not in vtt and "$" not in vtt and "再看一遍" in srt
    assert "subtitle:mov_text" in _streams(out)
    burned = tmp_path / "tiny.burned.mp4"
    assert burned.exists() and "subtitle:mov_text" not in _streams(burned)
    ch = json.loads((tmp_path / "tiny.chapters.json").read_text(encoding="utf-8"))
    assert ch["subtitles"] == {"mode": "soft", "vtt": "tiny.vtt", "srt": "tiny.srt", "embedded": "mov_text", "cues": 2,
                               "bilingual": False, "burned": "tiny.burned.mp4"}
    assert summary["subtitles"]["cues"] == 2
    # 第二句的字幕在定格之后才出现
    step = ch["chapters"][0]["steps"][0]
    second = vtt.split("\n\n")[2].splitlines()[1].split(" --> ")[0]
    h, m, s = second.split(":")
    assert float(s) + 60 * int(m) >= step["resume"] - 1e-3


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_subtitles_none_exports_nothing(tmp_path):
    spec = tmp_path / "tiny.scene.yaml"
    spec.write_text(TINY.replace("SUBS", "none"), encoding="utf-8")
    out = tmp_path / "tiny.mp4"
    summary = render(spec, out, None, "l", log=lambda *_: None, backend="moxian")
    assert summary["subtitles"] == {} and not (tmp_path / "tiny.vtt").exists()
    assert "subtitle:mov_text" not in _streams(out)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_default_burns_bilingual_subtitles_and_keeps_a_clean_copy(tmp_path):
    spec = tmp_path / "tiny.scene.yaml"
    body = TINY.replace(", subtitles: SUBS", "").replace('text: "再看一遍。"}', 'text: "再看一遍。", en: "Once more."}')
    spec.write_text(body, encoding="utf-8")
    out = tmp_path / "tiny.mp4"
    summary = render(spec, out, None, "l", log=lambda *_: None, backend="moxian")
    clean = tmp_path / "tiny.clean.mp4"
    assert "subtitle:mov_text" not in _streams(out)  # 字幕在画面里，不再叠一条软字幕
    assert "subtitle:mov_text" in _streams(clean)
    info = summary["subtitles"]
    assert info["mode"] == "burned" and info["bilingual"] and info["clean"] == "tiny.clean.mp4"
    assert "再看一遍\nOnce more." in (tmp_path / "tiny.srt").read_text(encoding="utf-8")


def test_short_english_is_split_like_the_chinese_cues():
    """中文切成两条时，短的英文句子也分成两段，导出的字幕里不重复整句（#15 结尾那句）。"""
    from animind_kit.subtitles import en_pieces
    assert en_pieces("Half a turn is times negative one. What would a quarter turn be?", 2) == [
        "Half a turn is times negative one.", "What would a quarter turn be?"]
    assert en_pieces("Yes, really.", 2) == ["Yes, really."]  # 词太少就不切


def test_equal_counts_pair_in_order():
    """中文切成一长一短两条、英文也是两段时，按顺序一条一段，不会两条都挂整句（#15 第 3 版 Landing）。"""
    from animind_kit.subtitles import Cue, _attach_en
    shown = "四行放在一起看，放进现金和拿走欠条，余额都往上涨"
    cut = shown.index("余额")
    cues = [Cue(0.0, 1.0, shown[:cut]), Cue(1.0, 2.0, shown[cut:])]
    en = "Put the four lines together: putting in cash and taking away IOUs both raise the balance."
    _attach_en(cues, shown, [(0, cut), (cut, len(shown))], en)
    assert cues[0].en != cues[1].en and (cues[0].en + " " + cues[1].en).split() == en.split()


def test_english_version_splits_on_clauses_without_orphans():
    text = "A builder leans a ladder against a wall: its foot is a from the wall, and its top must reach a height of b."
    lines = [text[a:b].strip() for a, b in sub.en_chunks(text)]
    assert lines == ["A builder leans a ladder against a wall:", "its foot is a from the wall,",
                     "and its top must reach a height of b."]
    cues = sub.sentence_cues(text, 0.0, 6.0)
    assert [c.text for c in cues][-1] == "and its top must reach a height of b." and all(not c.en for c in cues)


def test_bilingual_line_drops_english_bookmarks():
    cues = sub.sentence_cues("{@a}直角边 a 是二。", 0.0, 2.0, en="In this triangle, {@a}side a is two.")
    assert cues[0].en == "In this triangle, side a is two."


def test_word_times_from_align_place_cue_starts():
    text = "The ladder is c long. How is c related to a and b?"
    # 两句之间停了一秒：按字数比例估会把第二条的起点放早；词首的真实时刻把它放准
    n = sum(map(sub._content, text))
    second = sum(map(sub._content, "The ladder is c long."))
    cues = sub.sentence_cues(text, 0.0, 5.0, words={"n": n, "at": [[0, 0.0], [second, 2.5]]})
    assert [round(c.start, 2) for c in cues] == [0.0, 2.5]
    wrong_count = sub.sentence_cues(text, 0.0, 5.0, words={"n": n + 1, "at": [[second, 2.5]]})
    assert round(wrong_count[1].start, 2) != 2.5  # 字数对不上（比如中文稿展开了公式）就不用


def test_english_cues_do_not_merge_across_sentences():
    text = "Take two of those parts. Where do we stop?"
    assert [text[a:b].strip() for a, b in sub.en_chunks(text)] == ["Take two of those parts.", "Where do we stop?"]
