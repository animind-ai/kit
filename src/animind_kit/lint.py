"""字形级版式检查（和后端无关）：输入某一刻画面上的字形和图形，输出看得见的版式问题。

顶层包围盒的出框 / 重叠检查（regions.check_boxes）只看整块对象；这里看到每个字：
- small：字号（1080p 下的像素）低于下限；
- cross：一条线、或一块填充的边穿过某个字（同一对象内部也算，例如数轴刻度穿过标签）；
- covered：不透明的填充画在字上面，盖住了一大块；
- overlap：两段文字的字形互相压住；
- clip：内容伸出画面边缘被裁掉。

后端（mx/scan.py）在每批动作结束时把对象树拍成一组 Thing 交给 check()；Tracker 把同一个问题在
各个时刻的出现合并成一条，记下时间范围和来源（beat、第几个动作、挂在哪句旁白 / 书签上）。

思路参考了 manimgx 的 `inspect`（MIT），这里是按墨线的对象树重新实现的。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .regions import FRAME_HEIGHT, FRAME_WIDTH

Box = tuple[float, float, float, float]  # x0, y0, x1, y1（画面单位，原点在中心，y 向上）
PX = FRAME_HEIGHT / 1080  # 1080p 下一个像素的画面长度
FRAME: Box = (-FRAME_WIDTH / 2, -FRAME_HEIGHT / 2, FRAME_WIDTH / 2, FRAME_HEIGHT / 2)
KINDS = ("small", "cross", "covered", "overlap", "clip")

# 几类文字的字号下限，按成片 720p 的像素写（2026-10-17 定的默认值：手机上看得清）。
# 组件和区域在节点上标 font_floor = 类别名，scan 把它带到字形上，small 检查按 max(min_font, 下限) 判。
FLOORS_720 = {
    "label": 36.0,  # 数轴上的数字和分数标注（整数刻度、分数刻度、点的标注）
    "side": 26.0,  # 右侧栏里的文字
    "side_note": 22.0,  # 右侧栏里 level: caption 的注释：比正文小一档，仍然看得清
    "practice": 26.0,  # 「练一练」/ 想一想框（think_box）
}


def floors(scale: float = 1.0) -> dict[str, float]:
    """各类文字的字号下限，换成渲染坐标（1080p 像素）。scale：成片里画面的缩放（烧录字幕时画面缩小，下限相应抬高）。"""
    return {k: v * 1.5 / scale for k, v in FLOORS_720.items()}


@dataclass
class Config:
    min_font: float = 16.0  # 字号下限，1080p 像素（style.min_font 可覆盖）；16px 的字，数字约 11px 高
    touch: float = 0.0  # 线离字形包围盒多近算"穿过"（1080p 像素；负数表示要伸进字里这么深才算）
    overlap: float = 0.15  # 两个字形的重叠面积占较小者的比例超过它才算压住
    clip: float = 2.0  # 伸出画面多少像素算裁掉
    min_opacity: float = 0.15  # 更淡的东西看不清，不检查
    hide_fill: float = 0.9  # 不透明度达到它的填充会把画在它下面的线挡住
    floors: dict[str, float] = field(default_factory=dict)  # font_floor 类别 → 字号下限（1080p 像素），见 floors()


@dataclass
class Thing:
    """画面上的一个叶子：一个字形（kind="text"）或一个图形（kind="shape"）。坐标都是画面坐标（已含镜头）。"""

    kind: str
    owner: str  # 报告里的名字：对象 id、obj#片段、focus(obj)、underline(选择器)、obj~ghost1 …
    obj: str | None  # 所属的 scene-spec 对象（allow / over / within 按它算）；装饰取它的目标对象
    box: Box
    order: int  # 绘制顺序，小的先画
    run: int = -1  # 文字：所在文字段（一个 Text / Tex 节点）的编号
    text: str = ""  # 文字段原文
    em: float | None = None  # 文字段字号（1080p 像素）；残影等拷贝出来的字形没有
    act: str | None = None  # 装饰：focus / mark / underline / circle / cross / ghost
    curves: list[np.ndarray] = field(default_factory=list)  # 图形：折线，每条 (k, 2)
    closed: list[bool] = field(default_factory=list)
    stroked: bool = False
    fill: float = 0.0  # 填充的实际不透明度
    follows: bool = True  # 跟着镜头走（不是钉住的题卡、底部胶囊）
    floor: str | None = None  # 文字：字号下限的类别（FLOORS_720 的键），节点上的 font_floor
    slot: tuple | None = None  # 文字：文字段在所属节点下的位置（子节点下标路径），文字内容变了也不变（读数）
    color: str = ""  # 画出来的颜色（文字、填充取 fill，只描边的取 stroke），小写 #rrggbb
    alpha: float = 1.0  # 实际不透明度（含各层 opacity）


@dataclass
class Problem:
    kind: str
    subject: str  # 报告里的主语（文字所在对象 / 出框的对象）
    other: str | None
    detail: str
    box: Box  # 在分镜图上圈出的范围
    other_box: Box | None = None
    text: str = ""
    slot: tuple | None = None  # 涉及的文字段的位置（Thing.slot；overlap 是两段的）；不知道时为 None

    def key(self) -> tuple:
        return (self.kind, self.subject, self.other, self.text)

    def ident(self) -> tuple:
        """按文字段的位置认问题，不按文字内容：读数在变时，同一段字的同一个问题还是同一个。不知道位置时退回 key()。"""
        return (self.kind, self.subject, self.other, self.text if self.slot is None else self.slot)


@dataclass
class Moment:
    beat: str
    time: float  # beat 内的成片时刻（秒）
    source: str  # 例如 do[4] at=s2.n3
    absolute: float | None = None  # 整支片子里的时刻（知道前面各段时长时）


@dataclass
class Finding:
    number: int
    problem: Problem
    moments: list[Moment] = field(default_factory=list)
    prefix: str = ""  # 编号前缀：中途采样的问题另编一套号（"mid"），不和分镜图上的编号混

    def line(self) -> str:
        p, first, last = self.problem, self.moments[0], self.moments[-1]
        if first.beat != last.beat:
            when = f"t={first.time:.1f}s … {last.beat} t={last.time:.1f}s"
        elif first.time == last.time:
            when = f"t={first.time:.1f}s"
        else:
            when = f"t={first.time:.1f}–{last.time:.1f}s"
        if first.absolute is not None:
            when += f"（片中 {first.absolute:.1f}s）"
        who = p.subject if p.other is None else f"{p.subject} × {p.other}"
        return f"[{self.prefix}{self.number}] {first.beat} {when} {first.source}  {p.kind}  {who}：{p.detail}"


# ---------- 几何 ----------

def _area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _common(a: Box, b: Box) -> Box:
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _meet(a: Box, b: Box) -> bool:
    x0, y0, x1, y1 = _common(a, b)
    return x0 <= x1 and y0 <= y1


def _grow(b: Box, by: float) -> Box:
    return (b[0] - by, b[1] - by, b[2] + by, b[3] + by)


def union(boxes) -> Box:
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def segments_of(curves: list[np.ndarray], closed: list[bool]) -> tuple[np.ndarray, np.ndarray]:
    """折线 → 所有线段的起点、终点（闭合的补上最后一段）。"""
    starts, ends = [], []
    for pts, c in zip(curves, closed):
        if len(pts) < 2:
            continue
        if c and not np.allclose(pts[0], pts[-1]):
            pts = np.vstack([pts, pts[:1]])
        starts.append(pts[:-1])
        ends.append(pts[1:])
    if not starts:
        return np.zeros((0, 2)), np.zeros((0, 2))
    return np.vstack(starts), np.vstack(ends)


def crosses(a: np.ndarray, b: np.ndarray, box: Box) -> bool:
    """线段 a→b（各 (n, 2)）有没有一段穿进 box（Liang–Barsky，一次算完）。"""
    if len(a) == 0:
        return False
    d = b - a
    x0, y0, x1, y1 = box
    enter, leave = np.zeros(len(a)), np.ones(len(a))
    ok = np.ones(len(a), bool)
    for p, q in ((-d[:, 0], a[:, 0] - x0), (d[:, 0], x1 - a[:, 0]), (-d[:, 1], a[:, 1] - y0), (d[:, 1], y1 - a[:, 1])):
        flat = p == 0
        ok &= ~(flat & (q < 0))
        r = np.divide(q, p, out=np.zeros_like(q), where=~flat)
        enter = np.where(~flat & (p < 0), np.maximum(enter, r), enter)
        leave = np.where(~flat & (p > 0), np.minimum(leave, r), leave)
    return bool(np.any(ok & (enter <= leave)))


def covers(a: np.ndarray, b: np.ndarray, points: np.ndarray) -> np.ndarray:
    """闭合折线围成的区域（奇偶规则）盖住了哪些点。"""
    px, py = points[:, 0:1], points[:, 1:2]
    straddle = (a[:, 1] > py) != (b[:, 1] > py)
    with np.errstate(divide="ignore", invalid="ignore"):
        x = a[:, 0] + (py - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
    return np.sum(straddle & (px < x), axis=1) % 2 == 1


def _grid(box: Box, n: int) -> np.ndarray:
    xs, ys = np.linspace(box[0], box[2], n), np.linspace(box[1], box[3], n)
    return np.array([(x, y) for x in xs for y in ys])


# ---------- 例外 ----------

@dataclass
class Allow:
    """scene-spec 对象的 allow 字段：有意为之、不必报的版式情况。

    allow: {cross: true | [对象…], overlap: true | [对象…], small: true, clip: true}
    cross / overlap 是对称的：任何一方允许另一方就不报。
    """

    rules: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_doc(cls, doc: dict) -> Allow:
        return cls({k: d["allow"] for k, d in doc.get("objects", {}).items() if isinstance(d.get("allow"), dict)})

    def flag(self, obj: str | None, kind: str) -> bool:
        return obj is not None and self.rules.get(obj, {}).get(kind) is True

    def pair(self, a: str | None, b: str | None, kind: str) -> bool:
        for x, y in ((a, b), (b, a)):
            v = self.rules.get(x, {}).get(kind) if x is not None else None
            if v is True or (isinstance(v, list) and y in v):
                return True
        return False


# ---------- 检查 ----------

def _snippet(text: str, limit: int = 24) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _runs(texts: list[Thing]) -> dict[int, list[Thing]]:
    out: dict[int, list[Thing]] = {}
    for t in texts:
        out.setdefault(t.run, []).append(t)
    return out


def check(things: list[Thing], config: Config | None = None, allow: Allow | None = None,
          ignore_pairs: set[frozenset[str]] = frozenset(), framed: bool = False) -> list[Problem]:
    """某一刻画面上的问题。framed：镜头推近了（画面只露出一部分是预期的，非文字的出框不报）。"""
    cfg = config or Config()
    allow = allow or Allow()
    texts = [t for t in things if t.kind == "text"]
    shapes = [t for t in things if t.kind == "shape"]
    runs = _runs(texts)
    out: list[Problem] = []

    # small：按文字段看字号
    for glyphs in runs.values():
        g = glyphs[0]
        limit = max(cfg.min_font, cfg.floors.get(g.floor, 0.0)) if g.floor else cfg.min_font
        if g.em is None or g.em >= limit - 0.05 or allow.flag(g.obj, "small"):
            continue
        kind = f"（{g.floor} 下限）" if g.floor and limit > cfg.min_font else ""
        out.append(Problem("small", g.owner, None,
                           f"文字「{_snippet(g.text)}」字号 {g.em:.1f}px（1080p），低于 {limit:.4g}px{kind}",
                           union(t.box for t in glyphs), text=g.text, slot=g.slot))

    # clip：按所属对象合并
    tol = cfg.clip * PX
    per_owner: dict[str, list[Thing]] = {}
    for t in things:
        per_owner.setdefault(t.owner, []).append(t)
    for owner, parts in per_owner.items():
        if allow.flag(parts[0].obj, "clip"):
            continue
        if framed:  # 镜头推近：跟镜头走的东西只露出一部分是预期的
            parts = [p for p in parts if not p.follows]
        if not parts:
            continue
        box = union(p.box for p in parts)
        if box[2] < FRAME[0] or box[0] > FRAME[2] or box[3] < FRAME[1] or box[1] > FRAME[3]:
            continue  # 整个在画面外：看不见，不算裁掉
        past = [(FRAME[0] - box[0], "左"), (box[2] - FRAME[2], "右"), (box[3] - FRAME[3], "上"), (FRAME[1] - box[1], "下")]
        sides = [f"{side}边 {d / PX:.0f}px" for d, side in past if d > tol]
        if sides:
            out.append(Problem("clip", owner, None, "伸出画面：" + "，".join(sides), box))

    # cross / covered：线、填充边 × 字形
    touch = cfg.touch * PX
    seg_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def segs(s: Thing) -> tuple[np.ndarray, np.ndarray]:
        if id(s) not in seg_cache:
            seg_cache[id(s)] = segments_of(s.curves, s.closed)
        return seg_cache[id(s)]

    backdrops = [s for s in shapes if s.fill >= cfg.hide_fill]
    hits: dict[tuple, tuple[list[Thing], list[Box]]] = {}
    for g in texts:
        near = _grow(g.box, touch)
        if near[0] > near[2] or near[1] > near[3]:
            continue
        for s in shapes:
            if not _meet(near, s.box):
                continue
            if s.act == "cross" and s.obj == g.obj:
                continue  # 划掉自己的目标：本意如此
            if s.act == "mark":
                continue  # 标记底色画在字后面，故意比目标宽一点，压到邻字边缘不算
            if allow.pair(s.obj, g.obj, "cross"):
                continue
            # 片段底板（act="plate"）的边是画面的边界，不算线穿过字；字在底板下面的 covered 照常查
            edged = (s.stroked or s.fill >= 0.2) and s.act != "plate"
            a, b = segs(s)
            if edged and crosses(a, b, near):
                if s.order < g.order and any(
                    s.order < d.order < g.order and covers(*segs(d), _grid(near, 2)).all() for d in backdrops if d is not s
                ):
                    continue  # 被字下面的不透明底挡住了
                what = "线" if s.stroked else "填充边"
                _hit(hits, ("cross", s.owner, g.owner, g.run, what), g, s)
            elif s.fill >= 0.5 and s.order > g.order and s.closed and any(s.closed):
                share = float(np.mean(covers(a, b, _grid(g.box, 5))))
                if share > 0.3:
                    _hit(hits, ("covered", s.owner, g.owner, g.run, "填充"), g, s)
    for (kind, shape, owner, _run, what), (glyphs, sboxes) in hits.items():
        g = glyphs[0]
        sbox = union(sboxes)
        if kind == "cross":
            detail = f"{shape} 的{what}穿过文字「{_snippet(g.text)}」"
        else:
            detail = f"{shape} 的填充盖在文字「{_snippet(g.text)}」上"
        out.append(Problem(kind, owner, shape, detail, union(t.box for t in glyphs), sbox, text=g.text, slot=g.slot))

    # overlap：不同文字段的字形互相压住
    pairs: dict[tuple[int, int], list[tuple[Thing, Thing]]] = {}
    order = sorted(texts, key=lambda t: t.box[0])
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if b.box[0] > a.box[2]:
                break
            if a.run == b.run or not _meet(a.box, b.box):
                continue
            if a.obj is not None and b.obj is not None and frozenset((a.obj, b.obj)) in ignore_pairs and a.obj != b.obj:
                continue
            if allow.pair(a.obj, b.obj, "overlap"):
                continue
            common = _area(_common(a.box, b.box))
            if common > (2 * PX) ** 2 and common > cfg.overlap * min(_area(a.box), _area(b.box)):
                key = (min(a.run, b.run), max(a.run, b.run))
                pairs.setdefault(key, []).append((a, b) if a.run < b.run else (b, a))
    for glyphs in pairs.values():
        a, b = glyphs[0]
        if max(abs(u - v) for u, v in zip(union(x.box for x, _ in glyphs), union(y.box for _, y in glyphs))) < PX:
            continue  # 同一处的两份同样的字（变形途中的拷贝）
        out.append(Problem("overlap", a.owner, b.owner, f"文字「{_snippet(a.text)}」和「{_snippet(b.text)}」压在一起",
                           union(x.box for x, _ in glyphs), union(y.box for _, y in glyphs), text=a.text + "|" + b.text,
                           slot=None if a.slot is None or b.slot is None else (a.slot, b.slot)))
    return out


# ---------- v2 风格提示：一个镜头一个重点、同一时刻一种强调色（只提示，不算版式问题） ----------

EMPHASIS_ACTS = ("focus", "mark", "circle", "underline")


@dataclass
class Style:
    """accents：颜色（小写）→ 强调色角色（A/B/C/HL/NO；HL_INK、mark 底色都算 HL）。
    quiet：墨色、灰（FG、DIM、DIM_INK、BG、CARD）——不算强调，也算"已压暗"。full：不透明度到这么多才算"全强调"。"""

    accents: dict[str, str]
    quiet: set[str]
    full: float = 0.9


def style_hints(things: list[Thing], cfg: Style) -> list[Problem]:
    """一个时刻画面上违反 §10 的地方（v2 才查，只当提示）：

    - accents：全不透明的东西里同时用了两种以上强调色；
    - emphasis：同时有两个以上对象被 focus / mark / circle / underline 着；
    - undimmed：只强调一个对象时，别的对象里还有全不透明、非墨非灰的东西（没压暗）。
    """
    out: list[Problem] = []
    full = [t for t in things if t.alpha >= cfg.full and t.act != "ghost"]
    roles: dict[str, list[Thing]] = {}
    for t in full:
        role = cfg.accents.get(t.color)
        if role:
            roles.setdefault(role, []).append(t)
    if len(roles) > 1:
        names = "、".join(sorted(roles))
        who = "、".join(f"{r}（{'、'.join(sorted({t.owner for t in roles[r]}))}）" for r in sorted(roles))
        out.append(Problem("accents", "画面", None, f"同时有 {len(roles)} 种强调色：{who}（§10 一种强调色）",
                           union(t.box for ts in roles.values() for t in ts), text=names))
    hot: dict[str, list[Thing]] = {}
    for t in full:
        if t.act in EMPHASIS_ACTS and t.obj:
            hot.setdefault(t.obj, []).append(t)
    if len(hot) > 1:
        names = "、".join(sorted(hot))
        out.append(Problem("emphasis", names, None, f"同时强调了 {len(hot)} 个对象（§10 一个镜头一个重点）",
                           union(t.box for ts in hot.values() for t in ts), text=names))
    elif len(hot) == 1:
        (focus,) = hot
        loud = sorted({t.obj for t in full if t.obj and t.obj != focus and t.act is None
                       and t.color not in cfg.quiet and t.color})
        if loud:
            names = "、".join(loud)
            out.append(Problem("undimmed", focus, None, f"强调 {focus} 时，{names} 没有压暗（§10 其余压暗）",
                               union(t.box for t in full if t.obj in loud), text=names))
    return out


def _hit(hits: dict, key: tuple, glyph: Thing, shape: Thing) -> None:
    glyphs, boxes = hits.setdefault(key, ([], []))
    if glyph not in glyphs:
        glyphs.append(glyph)
    if shape.box not in boxes:
        boxes.append(shape.box)


class Tracker:
    """整支片子各个时刻的问题：同一个问题（同类、同样的对象和文字）只记一条，带上它出现的所有时刻
    （可能跨好几个 beat，例如一直在画面上的图）；按首次出现编号。"""

    def __init__(self, prefix: str = "", by_slot: bool = False) -> None:
        self.found: dict[tuple, Finding] = {}
        self.prefix = prefix
        self.by_slot = by_slot  # True：按 Problem.ident() 合并（文字段的位置），否则按 key()（文字内容）

    def see(self, moment: Moment, problems: list[Problem]) -> list[tuple[int, Problem]]:
        out = []
        for p in problems:
            key = p.ident() if self.by_slot else p.key()
            f = self.found.get(key)
            if f is None:
                f = self.found[key] = Finding(len(self.found) + 1, p, prefix=self.prefix)
            f.moments.append(moment)
            out.append((f.number, p))
        return out

    @property
    def findings(self) -> list[Finding]:
        return list(self.found.values())

    def lines(self) -> list[str]:
        return [f.line() for f in self.findings]
