"""每个 beat 的旁白时间：句子起止，以及书签的时刻。

0.1 还没有 voice 仓库，时间来自 voice 流水线写出的 <beat>.timing.json（§6.7 的 narrate.py 格式）：
    {"cues": [{"start": 0.3, "end": 6.7}, ...], "total": 27.03}
书签的时刻按它在句子里的字符位置比例估算，等 voice 接上 Whisper 后换成真实时间戳。
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


def load(beat: dict, audio_dir: Path | None) -> BeatTiming:
    say = beat.get("say", [])
    cues, total, source = None, None, "estimated"
    if audio_dir is not None:
        path = audio_dir / f"{beat['id']}.timing.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            cues, total, source = data["cues"], data["total"], "audio"
            if len(cues) != len(say):
                raise ValueError(f"{path.name} 有 {len(cues)} 句，scene 里 {beat['id']} 有 {len(say)} 句")
    if cues is None:  # 没有音频：按每个汉字 0.22 秒估
        cues, cursor = [], 0.3
        for s in say:
            seconds = max(1.0, len(_BOOKMARK.sub("", s["text"])) * 0.22)
            cues.append({"start": cursor, "end": cursor + seconds})
            cursor += seconds + 0.35
        total = cursor
    sentences = []
    for s, cue in zip(say, cues):
        span = cue["end"] - cue["start"]
        marks = {name: cue["start"] + frac * span for name, frac in _mark_fractions(s["text"]).items()}
        sentences.append(Sentence(s.get("id"), cue["start"], cue["end"], marks))
    hold = beat.get("pause", {}).get("hold", 0) if "pause" in beat else 0
    return BeatTiming(sentences, max(total, hold + 1.0), source)
