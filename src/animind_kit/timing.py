"""每个 beat 的旁白时间：句子起止，以及书签的时刻。

时间来自 animind-voice 写出的 <beat>.timing.json：
    {"cues": [{"start": 0.3, "end": 6.7}, ...], "total": 27.03, "marks": {"s1": {"d0": 5.01}}}
有 marks（animind-voice align 的 Whisper 对齐结果）就用真实书签时刻；没有就按字符位置比例估算。
align 还写 "words": {"s1": {"n": 42, "at": [[字序号, 时刻], ...]}}（词首的真实时刻），字幕切条用它定时。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from scene_spec import check_timing, pauses
from zh_math_reading import normalize

_BOOKMARK = re.compile(r"\{@([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class Sentence:
    id: str | None
    start: float
    end: float
    marks: dict[str, float] = field(default_factory=dict)
    words: dict | None = None  # {"n": 稿子参与对齐的字数, "at": [[第几个字, 时刻], ...]}


@dataclass
class BeatTiming:
    sentences: list[Sentence]
    total: float
    source: str  # "audio" 或 "estimated"

    def resolve(self, ref: int | str) -> float:
        if isinstance(ref, int):
            return self._sentence(str(ref)).start
        body, _, offset = ref.partition("+")
        sid, _, mark = body.partition("@")
        sentence = self._sentence(sid)
        t = sentence.marks[mark] if mark else sentence.start
        return t + (float(offset) if offset else 0.0)

    def _sentence(self, ref: str) -> Sentence:
        if not self.sentences and ref == "0":  # 没有旁白的 beat：at: 0 就是 beat 开始
            return Sentence(None, 0.0, 0.0, {})
        if ref.isdigit():
            return self.sentences[int(ref)]
        return next(s for s in self.sentences if s.id == ref)


def lang_of(doc: dict) -> str:
    """场景的旁白语言：style.lang，和 voice 的 script.lang 一样；没写就是中文。"""
    return doc.get("style", {}).get("lang", "zh")


def spoken(text: str, lang: str = "zh") -> str:
    """旁白真正念的文本（书签保留，位置对得上）。中文版和 voice 一样过 zh-math-reading 的 normalize：
    `$...$` 换成读法，所以公式的字数是它念出来的字数，不是 LaTeX 源码的字符数。英文版原样（英文稿本来就把公式写成词）。"""
    return normalize(text) if lang == "zh" else text


def _weight(text: str, lang: str) -> int:
    """估算时一段文字占的字数（每个字 0.22 秒）。

    中文版：每个非空白字计 1，包括标点——句中的标点是一个短停顿，记作一个字的长度（假设，见 tests/fixtures/timing_truth.json）。
    空白不计：normalize 为公式读法加的空格、作者排版的空格都不念。句间的停顿另由 silence_after 给。
    英文版照旧数字符（英文的空格是词与词之间的停顿，本来就算在内）。书签不算。
    """
    plain = _BOOKMARK.sub("", text)
    if lang != "zh":
        return len(plain)
    return len("".join(plain.split()))


def _mark_fractions(text: str, lang: str = "zh") -> dict[str, float]:
    """书签在句子里的相对位置（0–1）：书签前面的字数 / 整句的字数。text 应该已经是 spoken() 的结果。"""
    total = max(1, _weight(text, lang))
    return {m.group(1): _weight(text[: m.start()], lang) / total for m in _BOOKMARK.finditer(text)}


def load(beat: dict, audio_dir: Path | None, lang: str = "zh") -> BeatTiming:
    say = beat.get("say", [])
    texts = [spoken(s["text"], lang) for s in say]  # 估算用的是念出来的字数；书签位置也在这个文本上
    cues, total, source = None, None, "estimated"
    aligned: dict[str, dict[str, float]] = {}
    words: dict[str, dict] = {}
    if audio_dir is not None:
        path = audio_dir / f"{beat['id']}.timing.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            cues, total, source = data["cues"], data["total"], "audio"
            aligned = data.get("marks", {})  # animind-voice align 写入的真实书签时刻
            words = data.get("words", {})
            if len(cues) != len(say):
                raise ValueError(f"{path.name} 有 {len(cues)} 句，scene 里 {beat['id']} 有 {len(say)} 句")
    if cues is None:  # 没有音频：按每个汉字 0.22 秒估；句间静音和 voice 合成的一样，按句末标点（scene-spec 的 silence_after）
        cues, cursor = [], 0.3
        for i, (s, text) in enumerate(zip(say, texts)):
            seconds = max(1.0, _weight(text, lang) * 0.22)
            cues.append({"start": cursor, "end": cursor + seconds})
            cursor += seconds
            if i < len(say) - 1:  # 最后一句之后不插静音，和 voice 一致
                cursor += pauses.silence_after(s["text"], s.get("hold"))
        total = cursor
    sentences = []
    for i, (s, text, cue) in enumerate(zip(say, texts, cues)):
        span = cue["end"] - cue["start"]
        marks = {name: cue["start"] + frac * span for name, frac in _mark_fractions(text, lang).items()}
        real = aligned.get(s.get("id") or str(i), {})
        marks.update({k: v for k, v in real.items() if k in marks})
        sentences.append(Sentence(s.get("id"), cue["start"], cue["end"], marks, words.get(s.get("id") or str(i))))
    if aligned:
        source = "aligned"
    hold = beat.get("pause", {}).get("hold", 0) if "pause" in beat else 0
    return BeatTiming(sentences, max(total, hold + 1.0), source)


def timing_issues(doc: dict, report: dict) -> list[str]:
    """时间报告过 scene-spec 的 check_timing：SS480（画面晚于书签）、SS481（格式）、SS487（句间静音）。

    返回错误的文字，没有就是空列表。allow_late、style.quality.cue_late、豁免名单都由 scene-spec 按文档处理。
    """
    return [str(issue) for issue in check_timing(doc, report).errors]


def write_report(path: Path, beats: dict) -> Path:
    """timing-report.json：{"beats": {<beat id>: {"cues": [{start, end}...], "actions": [{at, want, start}...]}}}。
    时刻是旁白的音频时钟（秒，相对 beat 开头），和 CUE LATE 的计算一样：start 是画面实际开始，want 是书签。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"beats": beats}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
