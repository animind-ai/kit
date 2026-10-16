"""布局求解器（scene-spec §6.4 的规则 1–3），纯几何，不碰渲染器。

输入是同一时刻可见的对象的包围盒和它们的布局约束（相对谁摆、往哪个方向、间距、是否钉住），
输出是每个对象要做的调整：新坐标 = scale·旧坐标 + (dx, dy)。

做法（简单、确定）：
1. 先把出了区域的对象推回 / 缩回区域。
2. 找第一对重叠的对象（按出现顺序），列出几种分开的办法，逐个试算、打分，取最好的一个：
   - 后放的（或相对别人摆的）那个沿自己声明的方向挪开（below 往下、right_of 往右……）；
   - 沿四个方向之一挪开（挪得少的优先）；
   - 挪另一个；
   - 两个各让一半。
   挪动的是"整串"：对象连同挂在它下面 / 旁边 / 上面的对象一起动。挪出区域就绕着贴近对方的那条边缩小，
   但不低于下限（min_scale）；缩的是整串（连同它的锚），所以串里的间距一起等比缩，不会重新叠上。打分顺序：仍出框 > 剩下的重叠数 > 缩得多少 > 是否按声明方向 > 挪动距离。
3. 重复直到没有冲突或达到迭代上限；剩下的冲突带着对象名报出来，不悄悄放过。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..regions import Region

SIDES = {"below": (0.0, -1.0), "above": (0.0, 1.0), "left_of": (-1.0, 0.0), "right_of": (1.0, 0.0)}
OPPOSITE = {(0.0, -1.0): (0.0, 1.0), (0.0, 1.0): (0.0, -1.0), (-1.0, 0.0): (1.0, 0.0), (1.0, 0.0): (-1.0, 0.0)}
DIRS = [(0.0, -1.0), (0.0, 1.0), (1.0, 0.0), (-1.0, 0.0)]  # 下、上、右、左：同分时按这个顺序
TOL = 0.05  # 和 regions.check_boxes 一致：两边都重叠超过 0.05 才算重叠
EPS = 1e-6
DEFAULT_GAP = 0.2  # 没有声明间距时分开后留的空
MIN_SCALE = 0.6  # 和 layout.MIN_SHRINK 一致
MAX_ITER = 60


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def union(self, other: Box) -> Box:
        return Box(min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1), max(self.y1, other.y1))

    def overlap(self, other: Box) -> tuple[float, float]:
        return (min(self.x1, other.x1) - max(self.x0, other.x0), min(self.y1, other.y1) - max(self.y0, other.y0))


@dataclass
class Item:
    id: str
    box: Box
    region: str | None = None  # region_map 的键（区域名，或对象名：各对象的区域可以不同）；None 表示不限区域
    anchor: str | None = None  # 相对谁摆（below / above / left_of / right_of / over / within 引用的对象）
    side: str | None = None  # below / above / left_of / right_of；over / within 为 None
    gap: float = DEFAULT_GAP
    fixed: bool = False  # 钉住的、已经在画面上的、写了绝对坐标的：不动
    floor: float | None = None  # 最多缩到原大小的几倍；None 用 solve 的 min_scale


@dataclass(frozen=True)
class Adjustment:
    """新坐标 = scale·旧坐标 + (dx, dy)（世界坐标）。"""

    id: str
    scale: float
    dx: float
    dy: float


@dataclass
class Result:
    adjustments: list[Adjustment] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


# ---------- 状态：每个对象一个 (s, tx, ty) ----------


class _State:
    def __init__(self, items: list[Item]):
        self.items = {it.id: it for it in items}
        self.t: dict[str, tuple[float, float, float]] = {it.id: (1.0, 0.0, 0.0) for it in items}

    def copy(self) -> _State:
        other = _State.__new__(_State)
        other.items, other.t = self.items, dict(self.t)
        return other

    def box(self, k: str) -> Box:
        s, tx, ty = self.t[k]
        b = self.items[k].box
        return Box(s * b.x0 + tx, s * b.y0 + ty, s * b.x1 + tx, s * b.y1 + ty)

    def group_box(self, ids: list[str]) -> Box:
        out = self.box(ids[0])
        for k in ids[1:]:
            out = out.union(self.box(k))
        return out

    def shift(self, ids: list[str], dx: float, dy: float) -> None:
        for k in ids:
            s, tx, ty = self.t[k]
            self.t[k] = (s, tx + dx, ty + dy)

    def scale(self, ids: list[str], f: float, about: tuple[float, float]) -> None:
        ax, ay = about
        for k in ids:
            s, tx, ty = self.t[k]
            self.t[k] = (f * s, f * (tx - ax) + ax, f * (ty - ay) + ay)


# ---------- 求解 ----------


class _Solver:
    def __init__(self, items: list[Item], region_map: dict[str, Region], ignore_pairs: set[frozenset[str]],
                 min_scale: float):
        self.order = [it.id for it in items]
        self.rank = {k: i for i, k in enumerate(self.order)}
        self.items = {it.id: it for it in items}
        self.regions = region_map
        self.min_scale = min_scale
        self.ignore = set(ignore_pairs)
        self.children: dict[str, list[str]] = {k: [] for k in self.order}
        for it in items:
            if it.anchor in self.items and it.anchor != it.id:
                self.children[it.anchor].append(it.id)
                if it.side is None:  # over / within：本来就叠着
                    self.ignore.add(frozenset((it.id, it.anchor)))
        self.state = _State(items)
        self.stuck: set[frozenset[str]] = set()

    # 结构

    def group(self, k: str) -> list[str]:
        """k 和挂在它上面的所有对象（按出现顺序）。"""
        seen, todo = {k}, [k]
        while todo:
            for c in self.children[todo.pop()]:
                if c not in seen:
                    seen.add(c)
                    todo.append(c)
        return [x for x in self.order if x in seen]

    def movable(self, ids: list[str]) -> bool:
        return not any(self.items[k].fixed for k in ids)

    def unit(self, k: str) -> list[str] | None:
        """出框时整体处理的那一串：往上找到最高的、整串都能动的锚。"""
        best = None
        cur, seen = k, set()
        while cur is not None and cur in self.items and cur not in seen:
            seen.add(cur)
            g = self.group(cur)
            if not self.movable(g):
                break
            best = g
            cur = self.items[cur].anchor
        return best

    def floor_factor(self, st: _State, ids: list[str]) -> float:
        """这一串最多还能再缩多少（各成员下限里最紧的那个）。"""
        f = 0.0
        for k in ids:
            floor = self.items[k].floor if self.items[k].floor is not None else self.min_scale
            f = max(f, floor / st.t[k][0])
        return min(1.0, f)

    # 度量

    def region_excess(self, st: _State, k: str) -> float:
        it = self.items[k]
        r = self.regions.get(it.region) if it.region else None
        if r is None or it.fixed:
            return 0.0
        b = st.box(k)
        return max(0.0, r.x0 - b.x0) + max(0.0, b.x1 - r.x1) + max(0.0, r.y0 - b.y0) + max(0.0, b.y1 - r.y1)

    def overlapping(self, st: _State, a: str, b: str) -> tuple[float, float] | None:
        if frozenset((a, b)) in self.ignore:
            return None
        ox, oy = st.box(a).overlap(st.box(b))
        return (ox, oy) if ox > TOL and oy > TOL else None

    def conflicts(self, st: _State) -> list[tuple[str, str]]:
        out = []
        for i, a in enumerate(self.order):
            for b in self.order[i + 1:]:
                if self.items[a].fixed and self.items[b].fixed:
                    continue
                if self.overlapping(st, a, b):
                    out.append((a, b))
        return out

    # 动作

    def fit_region(self, st: _State, ids: list[str]) -> bool:
        """把一串推回 / 缩回根对象的区域：往哪边出框，就绕着对边缩（不低于下限），再推回。返回是否缩过。"""
        root = self.items[ids[0]]
        r = self.regions.get(root.region) if root.region else None
        if r is None:
            return False
        g = st.group_box(ids)
        over = (r.x0 - g.x0 > EPS, g.x1 - r.x1 > EPS, r.y0 - g.y0 > EPS, g.y1 - r.y1 > EPS)  # 左右下上
        if not any(over):
            return False
        # 缩放中心：往哪边出框，就绕着对边缩（往下挤出去就绕上沿缩，贴着锚的那边不动）；两边都出框绕中心
        px = g.x0 if over[1] and not over[0] else g.x1 if over[0] and not over[1] else (g.x0 + g.x1) / 2
        py = g.y1 if over[2] and not over[3] else g.y0 if over[3] and not over[2] else (g.y0 + g.y1) / 2
        px, py = min(max(px, r.x0), r.x1), min(max(py, r.y0), r.y1)
        f = 1.0
        for need, have in ((px - r.x0, px - g.x0), (r.x1 - px, g.x1 - px), (py - r.y0, py - g.y0), (r.y1 - py, g.y1 - py)):
            if have > EPS:
                f = min(f, need / have)
        f = max(f, self.floor_factor(st, ids))
        scaled = f < 1.0 - EPS
        if scaled:
            st.scale(ids, f, (px, py))
            g = st.group_box(ids)
        # 缩到下限还比区域大：居中（横向）/ 贴上沿（纵向）；否则推回区域
        dx = (r.x0 + r.x1 - g.x0 - g.x1) / 2 if g.width > r.width else max(0.0, r.x0 - g.x0) - max(0.0, g.x1 - r.x1)
        dy = (r.y1 - g.y1) if g.height > r.height else max(0.0, r.y0 - g.y0) - max(0.0, g.y1 - r.y1)
        if abs(dx) > EPS or abs(dy) > EPS:
            st.shift(ids, dx, dy)
        return scaled

    @staticmethod
    def clearance(mover: Box, other: Box, d: tuple[float, float], gap: float) -> float:
        """mover 沿 d 方向挪多远才和 other 隔开 gap。"""
        if d == (0.0, -1.0):
            return mover.y1 - (other.y0 - gap)
        if d == (0.0, 1.0):
            return (other.y1 + gap) - mover.y0
        if d == (1.0, 0.0):
            return (other.x1 + gap) - mover.x0
        return mover.x1 - (other.x0 - gap)

    def candidates(self, a: str, b: str) -> list[tuple[int, list[tuple[list[str], float, float, tuple[float, float]]]]]:
        """分开 a、b 的办法：(优先级, [(一串, dx, dy, 方向)])。"""
        st = self.state
        ia, ib = self.items[a], self.items[b]
        # 谁先让：钉住的不动；相对别人摆的（挂在对方下面）先动；否则后出现的先动
        if ia.fixed or b in self.group(a)[1:]:
            m, o = b, a
        elif ib.fixed or a in self.group(b)[1:]:
            m, o = a, b
        else:
            m, o = (b, a) if self.rank[b] > self.rank[a] else (a, b)
        gm, go = self.group(m), self.group(o)
        gap = max(self.items[m].gap, DEFAULT_GAP)
        out = []
        if self.movable(gm) and o not in gm:
            side = self.items[m].side
            if side:
                d = SIDES[side]
                out.append((0, [(gm, d[0] * self.clearance(st.box(m), st.box(o), d, gap),
                                 d[1] * self.clearance(st.box(m), st.box(o), d, gap), d)]))
            for d in DIRS:
                dist = self.clearance(st.box(m), st.box(o), d, gap)
                out.append((1, [(gm, d[0] * dist, d[1] * dist, d)]))
        if self.movable(go) and m not in go:
            for d in DIRS:
                dist = self.clearance(st.box(o), st.box(m), d, gap)
                out.append((2, [(go, d[0] * dist, d[1] * dist, d)]))
        if self.movable(gm) and self.movable(go) and not set(gm) & set(go):
            for d in DIRS:
                dist = self.clearance(st.box(m), st.box(o), d, gap) / 2
                back = OPPOSITE[d]
                out.append((1, [(gm, d[0] * dist, d[1] * dist, d), (go, back[0] * dist, back[1] * dist, back)]))
        return out

    def try_plan(self, plan) -> tuple[tuple, _State]:
        st = self.state.copy()
        moved = 0.0
        for ids, dx, dy, _d in plan:
            st.shift(ids, dx, dy)
            moved += abs(dx) + abs(dy)
        # 出框时整串（连同它的锚）一起缩：同一中心的等比缩放不会让串里原本分开的对象重新叠上
        for ids, _, _, _d in plan:
            self.fit_region(st, self.unit(ids[0]) or ids)
        shrunk = round(max(1.0 - st.t[k][0] / self.state.t[k][0] for k in self.order), 3)  # 缩得最狠的那个缩了多少
        excess = sum(self.region_excess(st, k) for k in self.order)
        new_conflicts = len([p for p in self.conflicts(st) if frozenset(p) not in self.stuck])
        return (excess > 0.01, new_conflicts, shrunk, moved), st

    def run(self, max_iter: int = MAX_ITER) -> Result:
        for k in self.order:  # 1. 出框的先推回区域
            if self.region_excess(self.state, k) > EPS:
                u = self.unit(k)
                if u:
                    self.fit_region(self.state, u)
        for _ in range(max_iter):  # 2. 一对一对地分开
            pairs = [p for p in self.conflicts(self.state) if frozenset(p) not in self.stuck]
            if not pairs:
                break
            a, b = pairs[0]
            scored = []
            for i, (prio, plan) in enumerate(self.candidates(a, b)):
                (bad, n, shrunk, moved), st = self.try_plan(plan)
                scored.append(((bad, n, shrunk, prio, round(moved, 6), i), st))
            if not scored:
                self.stuck.add(frozenset((a, b)))
                continue
            scored.sort(key=lambda x: x[0])
            best, st = scored[0]
            was_bad = sum(self.region_excess(self.state, k) for k in self.order) > 0.01
            solved = self.overlapping(st, a, b) is None
            # 只接受不变坏的办法：不新增出框，冲突数减少，或者持平但这一对分开了（换一对接着解）
            if (best[0] and not was_bad) or best[1] > len(pairs) or (best[1] == len(pairs) and not solved):
                self.stuck.add(frozenset((a, b)))
                continue
            self.state = st
            if not solved:
                self.stuck.add(frozenset((a, b)))
        return Result(self.adjustments(), self.problems())

    def adjustments(self) -> list[Adjustment]:
        out = []
        for k in self.order:
            s, tx, ty = self.state.t[k]
            if abs(s - 1.0) > EPS or abs(tx) > EPS or abs(ty) > EPS:
                out.append(Adjustment(k, s, tx, ty))
        return out

    def problems(self) -> list[str]:
        out = []
        st = self.state
        for k in self.order:
            if self.region_excess(st, k) > 0.01:
                b = st.box(k)
                out.append(f"{k} 超出区域 {self.regions[self.items[k].region].name}（x {b.x0:.2f}..{b.x1:.2f}，y {b.y0:.2f}..{b.y1:.2f}），"
                           f"已缩到下限仍放不下")
        for a, b in self.conflicts(st):
            ox, oy = st.box(a).overlap(st.box(b))
            out.append(f"{a} 与 {b} 重叠（{ox:.2f}×{oy:.2f}），求解器分不开")
        return out


def solve(items: list[Item], region_map: dict[str, Region], ignore_pairs: set[frozenset[str]] = frozenset(),
          min_scale: float = MIN_SCALE, max_iter: int = MAX_ITER) -> Result:
    """items 按出现（摆放）顺序给出。返回各对象的调整和解不开的冲突。"""
    return _Solver(items, region_map, ignore_pairs, min_scale).run(max_iter)
