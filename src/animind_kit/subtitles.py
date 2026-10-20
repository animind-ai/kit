"""字幕：按 style.subtitles 导出 WebVTT / SRT，烧进画面（默认）或封装成可关的软字幕轨。

- 三种模式（style.subtitles，命令行 --subtitles 可覆盖）：
  burned（默认）成片 <名>.mp4 的画面里就有字幕（中英双语时上中下英），另出一份画面干净、带软字幕轨的 <名>.clean.mp4
  给自己会显示字幕的播放器（App、YouTube）；soft 成片只带软字幕轨（mov_text，默认轨，可关）；none 不出字幕。
- 双语：句子写了 en（英文字幕）时每条字幕两行，中文在上；英文按词切成和中文条数相当的几段，跟着中文走。

- 文字用 say 的显示文本（场景文件里写的原文，不是给 TTS 的读法）：去掉书签 {@x}，
  $...$ 公式转成 Unicode 数学写法（a_n → aₙ，x^2 → x²，\\frac{2}{3} → ⅔，\\sqrt{x} → √x），不留 LaTeX 命令。
- 时间：每句的起止取 timing.json 的 cues（经过子步定格顺延），加上这一段在整片里的起点。
  长句按标点切成每条不超过 MAX_UNITS 个字宽（汉字算 1，西文、数字算 0.5）的小条；
  句内的时刻按书签插值（书签有 Whisper 对齐的真实时刻），书签之间按字数比例。
- 烧录：画面整体缩到 1 - BAND，底部让出一条字幕带，字幕画在带里，不压住 footer 的公式。
  画面缩小以后字也跟着小，所以 burned 时 kit 的字号下限（lint.floors）按 1 / (1 - BAND) 抬高。
"""

from __future__ import annotations

import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

MODES = ("burned", "soft", "none")
DEFAULT = "burned"  # 2026-10-17 定：字幕默认开（烧进画面）
MAX_UNITS = 18.0
EN_MAX = 64  # 英文字幕每段最多几个字符
EN_LINE = 44  # 英文版（字幕只有英文一行、字号和中文一样）每条最多几个字符
MIN_SECONDS = 0.5
BREAK = set("，。！？；：、!?;")
DROP_TAIL = set("，。；：、,;:")
_BOOKMARK = re.compile(r"\{@([A-Za-z_][A-Za-z0-9_]*)\}")
_CJK = re.compile(r"[\u3400-\u9fff]")
_MATH = re.compile(r"(?<!\\)\$(.+?)(?<!\\)\$")

# ---------- LaTeX → Unicode ----------

SYMBOLS = {
    "times": "×", "div": "÷", "cdot": "·", "pm": "±", "mp": "∓", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥",
    "ne": "≠", "neq": "≠", "approx": "≈", "equiv": "≡", "sim": "∼", "infty": "∞", "to": "→", "rightarrow": "→",
    "Rightarrow": "⇒", "leftarrow": "←", "Leftrightarrow": "⇔", "in": "∈", "notin": "∉", "subset": "⊂",
    "subseteq": "⊆", "cup": "∪", "cap": "∩", "emptyset": "∅", "varnothing": "∅", "forall": "∀", "exists": "∃",
    "angle": "∠", "triangle": "△", "perp": "⊥", "parallel": "∥", "circ": "°", "degree": "°", "prime": "′",
    "cdots": "⋯", "ldots": "…", "dots": "…", "vdots": "⋮", "sum": "∑", "prod": "∏", "int": "∫", "partial": "∂",
    "nabla": "∇", "therefore": "∴", "because": "∵", "mid": "|", "lt": "<", "gt": ">",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "theta": "θ",
    "lambda": "λ", "mu": "μ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ",
    "omega": "ω", "Delta": "Δ", "Sigma": "Σ", "Omega": "Ω", "Pi": "Π", "Gamma": "Γ", "Theta": "Θ",
    "lbrace": "{", "rbrace": "}", "{": "{", "}": "}", "%": "%", "$": "$", "#": "#", "&": "&", "_": "_",
    "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉", "langle": "⟨", "rangle": "⟩", "vert": "|",
}
SPACES = {",": "", ";": " ", ":": " ", "!": "", " ": " ", "quad": " ", "qquad": "  "}
DROP = {"left", "right", "displaystyle", "textstyle", "big", "Big", "bigg", "Bigg", "limits", "nolimits"}
KEEP_ARG = {"text", "textbf", "textrm", "mathrm", "mathbf", "mathit", "mathsf", "operatorname", "boldsymbol",
            "underline", "hat", "bar", "widehat", "tilde"}
OPERATORS = {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "lg", "exp", "lim", "max", "min", "gcd", "det"}
BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ"}
SUPER = dict(zip("0123456789+-=()nixyabk", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱˣʸᵃᵇᵏ"))
SUB = dict(zip("0123456789+-=()aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ"))
VULGAR = {(1, 2): "½", (1, 3): "⅓", (2, 3): "⅔", (1, 4): "¼", (3, 4): "¾", (1, 5): "⅕", (2, 5): "⅖",
          (3, 5): "⅗", (4, 5): "⅘", (1, 6): "⅙", (5, 6): "⅚", (1, 8): "⅛", (3, 8): "⅜", (5, 8): "⅝", (7, 8): "⅞"}


class _Tex:
    def __init__(self, src: str):
        self.s, self.i = src, 0

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def command(self) -> str:
        self.i += 1  # 反斜杠
        start = self.i
        if self.i < len(self.s) and self.s[self.i].isalpha():
            while self.i < len(self.s) and self.s[self.i].isalpha():
                self.i += 1
        else:
            self.i += 1
        return self.s[start:self.i]

    def skip_spaces(self) -> None:
        while self.peek() == " ":
            self.i += 1

    def arg(self) -> str:
        """一个参数：{...} 整组，或者单个记号。"""
        self.skip_spaces()
        ch = self.peek()
        if ch == "{":
            self.i += 1
            out = self.seq("}")
            self.i += 1
            return out
        if ch == "\\":
            return self.atom()
        self.i += 1
        return ch

    def optional(self) -> str | None:
        self.skip_spaces()
        if self.peek() != "[":
            return None
        end = self.s.index("]", self.i)
        out, self.i = self.s[self.i + 1:end], end + 1
        return out

    def atom(self) -> str:
        name = self.command()
        if name in ("frac", "dfrac", "tfrac"):
            return fraction(self.arg(), self.arg())
        if name == "sqrt":
            n = self.optional()
            body = self.arg()
            root = {None: "√", "2": "√", "3": "∛", "4": "∜"}.get(n, f"{script(n or '', SUPER, '^')}√")
            return root + (body if _simple(body) else f"({body})")
        if name in SPACES:
            return SPACES[name]
        if name in DROP:
            return ""
        if name in SYMBOLS:
            return SYMBOLS[name]
        if name == "mathbb":
            body = self.arg()
            return "".join(BLACKBOARD.get(c, c) for c in body)
        if name in ("vec", "overrightarrow"):
            return self.arg() + "⃗"
        if name == "overline":
            return "".join(c + "̅" for c in self.arg())
        if name in KEEP_ARG:
            return self.arg()
        if name in OPERATORS:
            return name
        return ""  # 不认识的命令：不把命令名留在字幕里

    def seq(self, stop: str = "") -> str:
        out = ""
        while self.i < len(self.s) and self.peek() != stop:
            ch = self.peek()
            if ch == "\\":
                out += self.atom()
            elif ch == "{":
                self.i += 1
                out += self.seq("}")
                self.i += 1
            elif ch in "^_":
                self.i += 1
                body = self.arg()
                if ch == "^" and body in ("°", "′"):
                    out += body
                else:
                    out += script(body, SUPER if ch == "^" else SUB, ch)
            elif ch == " ":
                self.i += 1
            elif ch == "~":
                out += " "
                self.i += 1
            else:
                out += ch
                self.i += 1
        return out


def _simple(text: str) -> bool:
    return len(text) <= 1 or text.isalnum()


def script(body: str, table: dict[str, str], mark: str) -> str:
    if body and all(c in table for c in body):
        return "".join(table[c] for c in body)
    return f"{mark}{body}" if _simple(body) else f"{mark}({body})"


def fraction(num: str, den: str) -> str:
    if num.isdigit() and den.isdigit() and (int(num), int(den)) in VULGAR:
        return VULGAR[(int(num), int(den))]
    a = num if _simple(num) else f"({num})"
    b = den if _simple(den) else f"({den})"
    return f"{a}/{b}"


def tex_to_unicode(tex: str) -> str:
    out = _Tex(tex).seq()
    # 带分数：7 后面紧跟 2/5 时隔开一点，免得读成 72/5；½ 这类单字分数直接贴着
    out = re.sub(r"(\d)(\d+/\d+)", r"\1 \2", out)
    return re.sub(r"\b(sin|cos|tan|cot|log|ln|lg|exp)([A-Za-z(])", r"\1 \2", out)  # sin x，不是 sinx


def display_text(text: str) -> str:
    """say 的原文 → 字幕文字：去书签，公式转 Unicode。"""
    text = _BOOKMARK.sub("", text)
    return _MATH.sub(lambda m: tex_to_unicode(m.group(1)), text).replace("\\$", "$").strip()


# ---------- 切条与时刻 ----------

def units(text: str) -> float:
    return sum(0.5 if (c.isascii() or c in "²³¹⁰⁴⁵⁶⁷⁸⁹ₙ½⅓⅔¼¾") else 1.0 for c in text)


def mode(doc: dict, override: str | None = None) -> str:
    """字幕模式：命令行覆盖 > style.subtitles > 默认（burned）。"""
    value = override or doc.get("style", {}).get("subtitles") or DEFAULT
    if value not in MODES:
        raise ValueError(f"style.subtitles 只能是 {' / '.join(MODES)}：{value!r}")
    return value


def picture_scale(subtitle_mode: str, one_line: bool = False) -> float:
    """成片里画面内容相对渲染画面的缩放：烧录字幕时让出字幕带，其余模式不缩。
    one_line（v2 版式）：字幕压成一行叠在画面底部预留的字幕带里，画面不缩。"""
    if one_line:
        return 1.0
    return 1.0 - BAND if subtitle_mode == "burned" else 1.0


def one_line(doc: dict) -> bool:
    """v2 版式（style.layout: v2）烧录一行字幕。"""
    return doc.get("style", {}).get("layout") == "v2"


@dataclass
class Cue:
    start: float
    end: float
    text: str
    en: str = ""  # 英文（双语字幕的第二行）

    @property
    def lines(self) -> str:
        return f"{self.text}\n{self.en}" if self.en else self.text


def _pieces(text: str) -> tuple[str, list[tuple[int, str]]]:
    """显示文本，以及每个书签落在显示文本的第几个字符。"""
    out, marks = "", []
    for part in re.split(r"(\{@[A-Za-z_][A-Za-z0-9_]*\})", text):
        m = _BOOKMARK.fullmatch(part)
        if m:
            marks.append((len(out), m.group(1)))
            continue
        out += _MATH.sub(lambda x: tex_to_unicode(x.group(1)), part)
    lead = len(out) - len(out.lstrip())
    return out.strip(), [(max(0, p - lead), name) for p, name in marks]


def chunks(text: str, limit: float = MAX_UNITS) -> list[tuple[int, int]]:
    """按标点切成小句，再贪心合并，每条不超过 limit 个字宽。返回 [起, 止) 字符下标。"""
    clauses, start = [], 0
    for i, ch in enumerate(text):
        if ch in BREAK or (ch in ",." and text[i + 1:i + 2] == " "):  # 英文的逗号、句号后面跟空格才算断句
            clauses.append((start, i + 1))
            start = i + 1
    if start < len(text):
        clauses.append((start, len(text)))
    pieces: list[tuple[int, int]] = []
    for a, b in clauses:  # 太长、没有标点的小句硬切
        while units(text[a:b].rstrip("".join(DROP_TAIL))) > limit:
            cut, width = a, 0.0
            while cut < b and width + units(text[cut]) <= limit:
                width += units(text[cut])
                cut += 1
            while cut > a + 1 and text[cut - 1].isascii() and cut < b and text[cut].isascii() and text[cut] != " ":
                cut -= 1  # 不把一个西文 / 数字记号从中间切开
            pieces.append((a, cut))
            a = cut
        if a < b:
            pieces.append((a, b))
    merged: list[tuple[int, int]] = []
    for a, b in pieces:
        if merged and units(text[merged[-1][0]:b].strip().rstrip("".join(DROP_TAIL))) <= limit:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def en_chunks(text: str, limit: int = EN_LINE) -> list[tuple[int, int]]:
    """英文版（一行英文）的切条：先在标点后切成小句，再贪心合并到 limit 个字符；太长的小句按词均分，不留一两个词的尾巴。"""
    clauses, start = [], 0
    for m in re.finditer(r"[,.;:?!](?=\s)", text):
        clauses.append((start, m.end()))
        start = m.end()
    if start < len(text):
        clauses.append((start, len(text)))
    pieces: list[tuple[int, int]] = []
    for a, b in clauses:
        words = [(m.start() + a, m.end() + a) for m in re.finditer(r"\S+", text[a:b])]
        size = text[a:b].strip()
        k = -(-len(size) // limit)
        if k <= 1 or not words:
            pieces.append((a, b))
            continue
        target, cur = len(size) / k, words[0][0]
        for i, (_, w1) in enumerate(words[:-1]):
            if w1 - cur >= target and len(words) - i - 1 >= 2:
                pieces.append((cur, w1))
                cur = words[i + 1][0]
        pieces.append((cur, b))
    merged: list[tuple[int, int]] = []
    for a, b in pieces:  # 不跨句号、问号合并：两句之间 TTS 会停顿，并成一条字幕会在停顿里挂着
        prev = text[merged[-1][0]:merged[-1][1]].rstrip() if merged else ""
        if prev and prev[-1] not in ".?!" and len(text[merged[-1][0]:b].strip()) <= limit:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def _clock(anchors: list[tuple[float, float]], pos: float) -> float:
    for (p0, t0), (p1, t1) in zip(anchors, anchors[1:]):
        if p0 <= pos <= p1:
            return t0 if p1 == p0 else t0 + (t1 - t0) * (pos - p0) / (p1 - p0)
    return anchors[-1][1]


def en_pieces(text: str, n: int, limit: int = EN_MAX) -> list[str]:
    """英文字幕切成几段：段数尽量等于中文条数 n（词太少时少切），每段尽量不超过 limit 个字符，优先在标点后切，各段长度尽量均匀。"""
    words = text.split()
    if not words:
        return []
    # 中文切成几条，英文就尽量切成几段（每段至少三个词）：不然短句的整句英文会挂在每一条中文下面，导出的字幕里重复
    k = max(1, min(n, max(-(-len(text) // limit), len(words) // 3)))
    while k <= len(words):
        target = len(text) / k
        pieces, cur = [], []
        for i, w in enumerate(words):
            cur.append(w)
            left = len(words) - i - 1
            room = k - len(pieces) - 1  # 还要切出的段数
            size = len(" ".join(cur))
            if room and left >= room and (size >= target or (size >= target * 0.6 and w[-1] in ",.;:?!")):
                pieces.append(" ".join(cur))
                cur = []
        if cur:
            pieces.append(" ".join(cur))
        if all(len(p) <= limit for p in pieces) or k >= n:
            return pieces
        k += 1
    return [" ".join(words)]


def _attach_en(cues: list[Cue], shown: str, spans: list[tuple[int, int]], en: str) -> None:
    """把英文的几段分给中文的各条：按中文条的中点落在全句的哪个比例位置，取对应比例位置的英文段。"""
    pieces = en_pieces(en, len(cues))
    if not pieces:
        return
    total = sum(len(p) for p in pieces)
    bounds, acc = [], 0
    for p in pieces:
        acc += len(p)
        bounds.append(acc / total)
    picked = []
    for a, b in spans:
        mid = (a + b) / 2 / max(1, len(shown))
        picked.append(next(i for i, edge in enumerate(bounds) if mid <= edge + 1e-9))
    if len(pieces) == len(cues):
        # 段数和中文条数一样：一条一段按顺序配。按中点比例配时，长短悬殊的两条会落到同一段，整句英文就挂了两遍
        picked = list(range(len(cues)))
    # 没被任何中文条取到的英文段，并进前一段（或后一段），不丢字
    used = sorted(set(picked))
    merged = {}
    for i, p in enumerate(pieces):
        owner = max((u for u in used if u <= i), default=used[0])
        merged[owner] = (merged.get(owner, "") + " " + p).strip()
    for cue, i in zip(cues, picked):
        cue.en = merged[i]


def _content(ch: str) -> bool:
    """和 animind-voice 对齐时一样的"字"：去掉空白、标点、符号。"""
    return not ch.isspace() and unicodedata.category(ch)[0] not in "PSZ"


def sentence_cues(text: str, start: float, end: float, marks: dict[str, float] | None = None,
                  en: str | None = None, words: dict | None = None) -> list[Cue]:
    """words：align 给的词首真实时刻（见 timing.py）。显示文本的字数和念的稿子对得上时（英文版、不含公式的中文句），
    每条字幕的起点取那个词的真实时刻，而不是按字数比例估。"""
    shown, positions = _pieces(text)
    if not shown:
        return []
    spans_all = chunks(shown) if _CJK.search(shown) else en_chunks(shown)
    points = [(float(pos), (marks or {}).get(name)) for pos, name in positions]
    if words and sum(map(_content, shown)) == words.get("n"):
        at = dict((int(i), t) for i, t in words["at"])
        points += [(float(a), at.get(sum(map(_content, shown[:a])))) for a, _ in spans_all[1:]]
    anchors = [(0.0, start)]
    for pos, t in sorted((p, t) for p, t in points if t is not None):  # 只取单调的，Whisper 偶尔会有倒挂
        if anchors[-1][0] <= pos and anchors[-1][1] <= t <= end:
            anchors.append((pos, t))
    anchors.append((float(len(shown)), end))
    out, spans = [], []
    for a, b in spans_all:
        words = shown[a:b].strip()
        while words and words[-1] in DROP_TAIL:
            words = words[:-1]
        if not words:
            continue
        out.append(Cue(_clock(anchors, a), _clock(anchors, b), words))
        spans.append((a, b))
    if en and out:
        _attach_en(out, shown, spans, " ".join(_BOOKMARK.sub("", en).split()))  # 英文版的书签写在 en 里
    for cue, nxt in zip(out, out[1:]):  # 太短的条往后借一点，不压到下一条
        cue.end = max(cue.end, min(cue.start + MIN_SECONDS, nxt.start))
    if out:
        out[-1].end = max(out[-1].end, out[-1].start + MIN_SECONDS)
    return out


def build(doc: dict, beat_starts: list[float], beat_info: dict[str, dict]) -> list[Cue]:
    """整片的字幕条。beat_info[beat] = {"cues": [{"start", "end", "marks"}]}（段内成片时刻）。"""
    cues: list[Cue] = []
    for beat, offset in zip(doc["beats"], beat_starts, strict=True):
        info = beat_info.get(beat["id"], {})
        for sentence, timing in zip(beat.get("say", []), info.get("cues", [])):
            marks = {k: offset + v for k, v in timing.get("marks", {}).items()}
            words = timing.get("words")
            if words:
                words = {"n": words["n"], "at": [[i, offset + t] for i, t in words["at"]]}
            cues += sentence_cues(sentence["text"], offset + timing["start"], offset + timing["end"], marks,
                                  sentence.get("en"), words)
    return cues


# ---------- 文件 ----------

def _stamp(t: float, sep: str) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_vtt(cues: list[Cue]) -> str:
    out = ["WEBVTT", ""]
    for n, c in enumerate(cues, 1):
        out += [str(n), f"{_stamp(c.start, '.')} --> {_stamp(c.end, '.')}", c.lines, ""]
    return "\n".join(out)


def to_srt(cues: list[Cue]) -> str:
    out = []
    for n, c in enumerate(cues, 1):
        out += [str(n), f"{_stamp(c.start, ',')} --> {_stamp(c.end, ',')}", c.lines, ""]
    return "\n".join(out)


def write(cues: list[Cue], out: Path) -> tuple[Path, Path]:
    vtt, srt = out.with_suffix(".vtt"), out.with_suffix(".srt")
    vtt.write_text(to_vtt(cues), encoding="utf-8")
    srt.write_text(to_srt(cues), encoding="utf-8")
    return vtt, srt


def mux_soft(video: Path, srt: Path, language: str = "chi") -> Path:
    """把字幕作为 mov_text 轨道封装进 mp4（软字幕：默认显示，播放器里可关；画面本身不变）。"""
    tmp = video.with_suffix(".subs.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(srt),
        "-map", "0:v", "-map", "0:a", "-map", "1:0", "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
        "-metadata:s:s:0", f"language={language}", "-metadata:s:s:0", "handler_name=Subtitles",
        "-disposition:s:0", "default", str(tmp),
    ], check=True)
    tmp.replace(video)
    return video


BAND = 0.12  # 烧录时底部字幕带占画面高度的比例（双语两行：720p 下中文约 34px、英文约 24px）
TEXT_WIDTH = 0.86  # 字幕最宽占画面宽度的比例（右下角还要留给品牌角标）


def burn(video: Path, cues: list[Cue], out: Path, background: str, ink: str, work: Path) -> Path:
    """烧录：画面缩到 (1-BAND)，底部让出字幕带，字幕用 Noto Sans CJK SC 画成 PNG 按时间叠上去。

    有英文时两行（中文在上、英文在下、英文字小一号、颜色淡一点），没有时一行居中。太宽的行按比例缩小字号。
    """
    import skia
    from moxian.text.font import typeface

    w, h = (int(x) for x in subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(video)], check=True, capture_output=True, text=True).stdout.strip().split(","))
    band = int(round(h * BAND / 2)) * 2
    inner_h = h - band
    inner_w = int(round(w * inner_h / h / 2)) * 2
    work.mkdir(parents=True, exist_ok=True)
    face = typeface("Noto Sans CJK SC")
    bilingual = any(c.en for c in cues)
    zh_size, en_size = (band * 0.40, band * 0.28) if bilingual else (band * 0.46, 0.0)
    r, g, b, _ = _rgb(ink)
    br, bg_, bb, _ = _rgb(background)
    soft = skia.Color(int(r * 0.75 + br * 0.25), int(g * 0.75 + bg_ * 0.25), int(b * 0.75 + bb * 0.25), 255)
    zh_paint = skia.Paint(Color=skia.Color(r, g, b, 255), AntiAlias=True)
    en_paint = skia.Paint(Color=soft, AntiAlias=True)

    def draw(canvas, text: str, size: float, paint, baseline: float) -> None:
        font = skia.Font(face, size)
        width = font.measureText(text)
        if width > w * TEXT_WIDTH:
            font = skia.Font(face, size * w * TEXT_WIDTH / width)
            width = font.measureText(text)
        canvas.drawString(text, (w - width) / 2, baseline, font, paint)

    def card(cue: Cue | None, name: str) -> Path:
        surface = skia.Surface(w, band)
        with surface as canvas:
            canvas.clear(skia.Color(*_rgb(background)))
            if cue is not None and bilingual:
                draw(canvas, cue.text, zh_size, zh_paint, band * 0.46)
                if cue.en:
                    draw(canvas, cue.en, en_size, en_paint, band * 0.84)
            elif cue is not None:
                draw(canvas, cue.text, zh_size, zh_paint, band * 0.66)
        path = work / f"{name}.png"
        surface.makeImageSnapshot().save(str(path), skia.kPNG)
        return path

    blank = card(None, "blank")
    lines, t = [], 0.0
    for n, c in enumerate(cues):
        if c.start > t:
            lines += [f"file '{blank.resolve()}'", f"duration {c.start - t:.3f}"]
        lines += [f"file '{card(c, f'cue{n:04d}').resolve()}'", f"duration {c.end - c.start:.3f}"]
        t = c.end
    lines += [f"file '{blank.resolve()}'", "duration 1.000", f"file '{blank.resolve()}'"]
    playlist = work / "bands.txt"
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    graph = (f"[0:v]scale={inner_w}:{inner_h},pad={w}:{h}:{(w - inner_w) // 2}:0:color={background}[v];"
             f"[1:v]format=yuv420p[s];[v][s]overlay=0:{inner_h}:eof_action=repeat[o]")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(video), "-f", "concat", "-safe", "0", "-i", str(playlist),
        "-filter_complex", graph, "-map", "[o]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", str(out),
    ], check=True)
    return out


ONE_LINE_BAND = 0.075  # v2：一行字幕带占画面高度的比例（720p 下 54px，= regions.SUBTITLE_STRIP["v2"] 0.6 单位）
ONE_LINE_ZH, ONE_LINE_EN = 0.56, 0.52  # 中文 / 英文字号占带高的比例（720p 约 30px / 28px，英文比原来的 24px 大）
ONE_LINE_FLOOR_720 = 27.0  # 任何字幕字号不低于 font_size 22 对应的 720p 像素


def _split_cue(c: Cue) -> list[Cue]:
    """一行放不下：中文在最靠中间的标点（没有就正中）切开，英文在最靠中间的空格切开，时间按中文字数比例分。"""
    t = c.text
    mid = len(t) / 2
    cands = [i + 1 for i, ch in enumerate(t[:-1]) if ch in BREAK or ch in "，、 "] or [len(t) // 2]
    cut = min(cands, key=lambda i: abs(i - mid))
    cut = max(1, min(len(t) - 1, cut))
    a, b = t[:cut].strip().rstrip("".join(DROP_TAIL)), t[cut:].strip()
    words = c.en.split()
    if len(words) >= 2:
        k = max(1, min(len(words) - 1, round(len(words) * cut / max(1, len(t)))))
        ea, eb = " ".join(words[:k]), " ".join(words[k:])
    else:
        ea, eb = c.en, ""
    tm = c.start + (c.end - c.start) * cut / max(1, len(t))
    return [Cue(c.start, tm, a or t, ea), Cue(tm, c.end, b or t, eb or ea)]


def burn_one_line(video: Path, cues: list[Cue], out: Path, background: str, ink: str, work: Path) -> Path:
    """v2 烧录：画面不缩，底部 ONE_LINE_BAND 高的一条字幕带盖在画面上（版式已经给它留了空），
    中文和英文排在同一行（中文在前、英文字号略小、颜色是 FG 75% 混 BG）。一行放不下就把这一条按时间切成两条。"""
    import skia
    from moxian.text.font import typeface

    w, h = (int(x) for x in subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(video)], check=True, capture_output=True, text=True).stdout.strip().split(","))
    band = int(round(h * ONE_LINE_BAND / 2)) * 2
    work.mkdir(parents=True, exist_ok=True)
    face = typeface("Noto Sans CJK SC")
    zh_size, en_size = band * ONE_LINE_ZH, band * ONE_LINE_EN
    floor = ONE_LINE_FLOOR_720 * h / 720
    max_w = w * TEXT_WIDTH
    r, g, b, _ = _rgb(ink)
    br, bg_, bb, _ = _rgb(background)
    soft = skia.Color(int(r * 0.75 + br * 0.25), int(g * 0.75 + bg_ * 0.25), int(b * 0.75 + bb * 0.25), 255)
    zh_paint = skia.Paint(Color=skia.Color(r, g, b, 255), AntiAlias=True)
    en_paint = skia.Paint(Color=soft, AntiAlias=True)

    def widths(c: Cue, k: float) -> tuple[float, float, float]:
        zw = skia.Font(face, zh_size * k).measureText(c.text)
        ew = skia.Font(face, en_size * k).measureText(c.en) if c.en else 0.0
        gap = zh_size * k * 1.2 if c.en else 0.0
        return zw, gap, ew

    def scale_for(c: Cue) -> float | None:
        zw, gap, ew = widths(c, 1.0)
        total = zw + gap + ew
        k = min(1.0, max_w / total) if total else 1.0
        return k if min(zh_size, en_size if c.en else zh_size) * k >= floor - 1e-6 else None

    fitted: list[Cue] = []
    todo = list(cues)
    while todo:
        c = todo.pop(0)
        if scale_for(c) is None and len(c.text) > 1:
            todo[:0] = _split_cue(c)
        else:
            fitted.append(c)

    def card(cue: Cue | None, name: str) -> Path:
        surface = skia.Surface(w, band)
        with surface as canvas:
            canvas.clear(skia.Color(*_rgb(background)))
            if cue is not None:
                k = scale_for(cue) or floor / min(zh_size, en_size)
                zw, gap, ew = widths(cue, k)
                x = (w - (zw + gap + ew)) / 2
                base = band * 0.70
                canvas.drawString(cue.text, x, base, skia.Font(face, zh_size * k), zh_paint)
                if cue.en:
                    canvas.drawString(cue.en, x + zw + gap, base, skia.Font(face, en_size * k), en_paint)
        path = work / f"{name}.png"
        surface.makeImageSnapshot().save(str(path), skia.kPNG)
        return path

    blank = card(None, "blank")
    lines, t = [], 0.0
    for n, c in enumerate(fitted):
        if c.start > t:
            lines += [f"file '{blank.resolve()}'", f"duration {c.start - t:.3f}"]
        lines += [f"file '{card(c, f'cue{n:04d}').resolve()}'", f"duration {c.end - c.start:.3f}"]
        t = c.end
    lines += [f"file '{blank.resolve()}'", "duration 1.000", f"file '{blank.resolve()}'"]
    playlist = work / "bands.txt"
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    graph = f"[1:v]format=yuv420p[s];[0:v][s]overlay=0:{h - band}:eof_action=repeat[o]"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(video), "-f", "concat", "-safe", "0", "-i", str(playlist),
        "-filter_complex", graph, "-map", "[o]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", str(out),
    ], check=True)
    return out


def _rgb(value: str) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), 255
