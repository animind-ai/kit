"""软字幕：按 style.subtitles 导出 WebVTT / SRT，封装进 mp4（mov_text，可关），可选烧录一份副本。

- 文字用 say 的显示文本（场景文件里写的原文，不是给 TTS 的读法）：去掉书签 {@x}，
  $...$ 公式转成 Unicode 数学写法（a_n → aₙ，x^2 → x²，\\frac{2}{3} → ⅔，\\sqrt{x} → √x），不留 LaTeX 命令。
- 时间：每句的起止取 timing.json 的 cues（经过子步定格顺延），加上这一段在整片里的起点。
  长句按标点切成每条不超过 MAX_UNITS 个字宽（汉字算 1，西文、数字算 0.5）的小条；
  句内的时刻按书签插值（书签有 Whisper 对齐的真实时刻），书签之间按字数比例。
- 烧录（--burn-subtitles）：画面整体缩到 90%，底部让出一条字幕带，字幕画在带里，不压住 footer 的公式。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

MAX_UNITS = 18.0
MIN_SECONDS = 0.5
BREAK = set("，。！？；：、!?;")
DROP_TAIL = set("，。；：、,;:")
_BOOKMARK = re.compile(r"\{@([A-Za-z_][A-Za-z0-9_]*)\}")
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


@dataclass
class Cue:
    start: float
    end: float
    text: str


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
        if ch in BREAK:
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


def _clock(anchors: list[tuple[float, float]], pos: float) -> float:
    for (p0, t0), (p1, t1) in zip(anchors, anchors[1:]):
        if p0 <= pos <= p1:
            return t0 if p1 == p0 else t0 + (t1 - t0) * (pos - p0) / (p1 - p0)
    return anchors[-1][1]


def sentence_cues(text: str, start: float, end: float, marks: dict[str, float] | None = None) -> list[Cue]:
    shown, positions = _pieces(text)
    if not shown:
        return []
    anchors = [(0.0, start)]
    for pos, name in positions:  # 书签：真实时刻（只取单调的，Whisper 偶尔会有倒挂）
        t = (marks or {}).get(name)
        if t is not None and anchors[-1][0] <= pos and anchors[-1][1] <= t <= end:
            anchors.append((float(pos), t))
    anchors.append((float(len(shown)), end))
    out = []
    for a, b in chunks(shown):
        words = shown[a:b].strip()
        while words and words[-1] in DROP_TAIL:
            words = words[:-1]
        if not words:
            continue
        out.append(Cue(_clock(anchors, a), _clock(anchors, b), words))
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
            cues += sentence_cues(sentence["text"], offset + timing["start"], offset + timing["end"], marks)
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
        out += [str(n), f"{_stamp(c.start, '.')} --> {_stamp(c.end, '.')}", c.text, ""]
    return "\n".join(out)


def to_srt(cues: list[Cue]) -> str:
    out = []
    for n, c in enumerate(cues, 1):
        out += [str(n), f"{_stamp(c.start, ',')} --> {_stamp(c.end, ',')}", c.text, ""]
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


BAND = 0.1  # 烧录时底部字幕带占画面高度的比例


def burn(video: Path, cues: list[Cue], out: Path, background: str, ink: str, work: Path) -> Path:
    """烧录副本：画面缩到 (1-BAND)，底部让出字幕带，字幕用 Noto Sans CJK SC 画成 PNG 按时间叠上去。"""
    import skia
    from moxian.text.font import typeface

    w, h = (int(x) for x in subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(video)], check=True, capture_output=True, text=True).stdout.strip().split(","))
    band = int(round(h * BAND / 2)) * 2
    inner_h = h - band
    inner_w = int(round(w * inner_h / h / 2)) * 2
    work.mkdir(parents=True, exist_ok=True)
    font = skia.Font(typeface("Noto Sans CJK SC"), band * 0.5)
    paint = skia.Paint(Color=skia.Color(*_rgb(ink)), AntiAlias=True)

    def card(text: str, name: str) -> Path:
        surface = skia.Surface(w, band)
        with surface as canvas:
            canvas.clear(skia.Color(*_rgb(background)))
            if text:
                width = font.measureText(text)
                canvas.drawString(text, (w - width) / 2, band * 0.66, font, paint)
        path = work / f"{name}.png"
        surface.makeImageSnapshot().save(str(path), skia.kPNG)
        return path

    blank = card("", "blank")
    lines, t = [], 0.0
    for n, c in enumerate(cues):
        if c.start > t:
            lines += [f"file '{blank.resolve()}'", f"duration {c.start - t:.3f}"]
        lines += [f"file '{card(c.text, f'cue{n:04d}').resolve()}'", f"duration {c.end - c.start:.3f}"]
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


def _rgb(value: str) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), 255
