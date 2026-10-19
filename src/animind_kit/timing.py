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


def _mark_fractions(text: str) -> dict[str, float]:
    stripped = _BOOKMARK.sub("", text)
    total = max(1, len(stripped))
    fractions, consumed = {}, 0
    for m in _BOOKMARK.finditer(text):
        plain_before = len(_BOOKMARK.sub("", text[: m.start()]))
        fractions[m.group(1)] = plain_before / total
        consumed += 1
    return fractions


# 没有音频时的估算（和 animind-voice 一致：句前 0.3 秒，句间停顿按 pace 取 voice 的 GAP，都除以 voice.speed）
SECONDS_PER_CHAR = {"zh": 0.22, "en": 1 / 11.7}  # 中文每字约 0.22 秒；英文约每秒 11.7 个字符（≈150 词/分）
PACE_RATE = {"normal": 1.0, "brisk": 1.12, "slow": 0.85, "ask": 0.92}  # 语速倍数
GAP = {"normal": 0.35, "brisk": 0.25, "slow": 0.6, "ask": 0.55}  # 同 animind-voice synth.GAP
LEAD = 0.3


def estimate(say: list[dict], style: dict | None = None) -> tuple[list[dict], float]:
    """No audio: sentence cues from the text length, per language (style.lang), pace and style.voice.speed."""
    style = style or {}
    per_char = SECONDS_PER_CHAR.get(style.get("lang") or "zh", SECONDS_PER_CHAR["zh"])
    speed = float((style.get("voice") or {}).get("speed", 1.0)) or 1.0
    cues, cursor = [], LEAD / speed
    for k, s in enumerate(say):
        pace = s.get("pace", "normal")
        seconds = max(1.0, len(_BOOKMARK.sub("", s["text"])) * per_char / PACE_RATE.get(pace, 1.0) / speed)
        cues.append({"start": cursor, "end": cursor + seconds})
        cursor += seconds + (GAP.get(pace, GAP["normal"]) / speed if k + 1 < len(say) else 0.35)
    return cues, cursor


def load(beat: dict, audio_dir: Path | None, style: dict | None = None) -> BeatTiming:
    """style：场景的 style（lang、voice.speed），只用于没有音频时的估算。"""
    say = beat.get("say", [])
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
    if cues is None:  # 没有音频：按语言、pace、语速估
        cues, total = estimate(say, style)
    sentences = []
    for i, (s, cue) in enumerate(zip(say, cues)):
        span = cue["end"] - cue["start"]
        marks = {name: cue["start"] + frac * span for name, frac in _mark_fractions(s["text"]).items()}
        real = aligned.get(s.get("id") or str(i), {})
        marks.update({k: v for k, v in real.items() if k in marks})
        sentences.append(Sentence(s.get("id"), cue["start"], cue["end"], marks, words.get(s.get("id") or str(i))))
    if aligned:
        source = "aligned"
    hold = beat.get("pause", {}).get("hold", 0) if "pause" in beat else 0
    return BeatTiming(sentences, max(total, hold + 1.0), source)
