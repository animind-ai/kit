"""折线曲线（墨线实现）：graph 的数据曲线 {data}、plane 的 curves（数据点列或参数方程）和沿曲线走的点。

几何都交给 scene_spec.curves（和检查器同一套代码）：数据点列按顺序连成折线，参数曲线在 domain 上等分取样；
尺子（along：length 按弧长，param 按序号 / t）把 [0, 1] 映到曲线上。progress 画到尺子上的哪里，at 是点在哪里——
两个写同一个参数时，点就在轨迹的头上。参数一变（progress、at、参数曲线里引用的参数），宿主组件的 updater 重画。
"""

from __future__ import annotations

import math
from collections.abc import Callable

from moxian.anim import Param
from moxian.path import Path
from scene_spec import curves as C
from scene_spec.expr import evaluate, free_names, parse


def scalar(value, params: dict[str, Param]) -> tuple[Callable[[], float], set[str]]:
    """数或参数表达式 → (取当前值的函数, 用到的参数名)。"""
    if not isinstance(value, str):
        v = float(value)
        return (lambda: v), set()
    tree = parse(value, set(params), allow_x=False)
    names = free_names(tree)
    return (lambda: evaluate(tree, math.nan, {n: params[n].value for n in names})), names


class Track:
    """一条曲线：当前的点列（数学坐标）、尺子、progress。"""

    def __init__(self, spec: dict, data: dict, params: dict[str, Param]):
        self.params = params
        self.along = spec.get("along", "length")
        self.progress, self.names = scalar(spec.get("progress", 1), params)
        self.trees = None
        if "data" in spec:
            self._pts = C.points(data[spec["data"]], f"data.{spec['data']} ")
        else:
            self.trees = C.parse_param(spec["x"], set(params)), C.parse_param(spec["y"], set(params))
            self.domain = spec["domain"]
            self.curve_names = (free_names(self.trees[0]) | free_names(self.trees[1])) - {C.CURVE_VAR}
            self.names |= self.curve_names
            self._pts = None
        self._key: tuple | None = None
        self._cum: list[float] = []

    def points(self) -> list[C.Point]:
        """当前的整条折线（参数曲线按参数的当前值重新取样；参数不变时用上次的）。"""
        if self.trees is None:
            key = ()
        else:
            key = tuple(self.params[n].value for n in sorted(self.curve_names))
        if key != self._key or self._pts is None:
            if self.trees is not None:
                env = {n: self.params[n].value for n in self.curve_names}
                self._pts = C.sample(self.trees[0], self.trees[1], self.domain, env)
            self._cum = C.ruler(self._pts, self.along)
            self._key = key
        return self._pts

    def ruler(self) -> list[float]:
        self.points()
        return self._cum

    def drawn(self) -> list[C.Point]:
        """按 progress 画出来的那一段。"""
        return C.prefix(self.points(), self.ruler(), self.progress())

    def at(self, s: float) -> C.Point:
        return C.point_at(self.points(), self.ruler(), s)

    def end(self) -> C.Point:
        return self.points()[-1]


def polyline(pts: list[C.Point], unit: float = 1.0) -> Path:
    """点列 → 折线路径（画面坐标 = 数学坐标 × unit）；少于两个点时是空路径。每次新建：渲染器按路径对象缓存。"""
    path = Path()
    if len(pts) < 2:
        return path
    path.move_to(pts[0][0] * unit, pts[0][1] * unit)
    for x, y in pts[1:]:
        path.line_to(x * unit, y * unit)
    return path


def length(pts: list[C.Point]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
