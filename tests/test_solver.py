"""布局求解器的单元测试：只用包围盒，不渲染。"""

from moxian.path import rounded_rect

from animind_kit.mx import layout, solver
from animind_kit.mx.solver import Box, Item, solve
from animind_kit.regions import Region

STAGE = Region("stage", -6.0, 6.0, -2.5, 1.5)
R = {"stage": STAGE}


def boxes(result, items):
    """把调整套回原包围盒，得到求解后的位置。"""
    adj = {a.id: a for a in result.adjustments}
    out = {}
    for it in items:
        a = adj.get(it.id)
        s, dx, dy = (a.scale, a.dx, a.dy) if a else (1.0, 0.0, 0.0)
        b = it.box
        out[it.id] = Box(s * b.x0 + dx, s * b.y0 + dy, s * b.x1 + dx, s * b.y1 + dy)
    return out


def apart(a: Box, b: Box) -> bool:
    ox, oy = a.overlap(b)
    return ox <= solver.TOL or oy <= solver.TOL


def inside(b: Box, r: Region) -> bool:
    return b.x0 >= r.x0 - 1e-6 and b.x1 <= r.x1 + 1e-6 and b.y0 >= r.y0 - 1e-6 and b.y1 <= r.y1 + 1e-6


def test_no_conflict_no_adjustment():
    items = [Item("a", Box(-2, 0, 0, 1), "stage"), Item("b", Box(1, 0, 3, 1), "stage")]
    r = solve(items, R)
    assert r.adjustments == [] and r.problems == []


def test_right_of_sibling_moves_right():
    # b 声明 right_of a，但摆完和 a 叠在一起：沿声明方向（往右）挪开，纵向不动
    items = [Item("a", Box(-4, -0.5, 0, 0.5), "stage"),
             Item("b", Box(-1, -0.4, 1, 0.4), "stage", anchor="a", side="right_of", gap=0.4)]
    r = solve(items, R)
    out = boxes(r, items)
    assert r.problems == []
    assert apart(out["a"], out["b"])
    assert abs(out["b"].x0 - (0 + 0.4)) < 1e-6 and out["b"].y0 == -0.4
    assert out["a"] == items[0].box  # 锚不动


def test_below_overlap_moves_down():
    items = [Item("a", Box(-2, -0.2, 2, 1.0), "stage"),
             Item("x", Box(2.2, -1.0, 4, 1.4), "stage"),  # 不相关、先出现的对象：挡住了往右挪
             Item("b", Box(-1, 0.0, 1, 0.6), "stage", anchor="a", side="below", gap=0.4)]
    r = solve(items, R)
    out = boxes(r, items)
    assert r.problems == []
    for p, q in (("a", "b"), ("a", "x"), ("b", "x")):
        if frozenset((p, q)) != frozenset(("a", "x")):
            assert apart(out[p], out[q]), (p, q)
    assert out["b"].y1 < out["a"].y0  # 往下挪了


def test_dependents_move_with_anchor():
    # p 和 q 都 in: stage（q 后出现），p 下面挂着 p2，p2 右边挂着 p3：p 被挪时整串一起动
    items = [Item("q", Box(-1, -0.5, 1, 0.5), "stage", fixed=True),
             Item("p", Box(-1.5, -0.3, 1.5, 0.3), "stage"),
             Item("p2", Box(-1, -1.0, 1, -0.6), "stage", anchor="p", side="below", gap=0.3),
             Item("p3", Box(1.2, -1.0, 2, -0.6), "stage", anchor="p2", side="right_of", gap=0.2)]
    r = solve(items, R)
    out = boxes(r, items)
    assert r.problems == []
    moves = {a.id: (a.scale, round(a.dx, 9), round(a.dy, 9)) for a in r.adjustments}
    assert "q" not in moves
    assert moves["p"] == moves["p2"] == moves["p3"]  # 同一个变换
    for k in ("p", "p2", "p3"):
        assert apart(out["q"], out[k]) and inside(out[k], STAGE)


def test_region_overflow_shrinks_with_floor():
    # 太宽的对象：缩到区域宽度
    items = [Item("w", Box(-8, 0, 8, 1), "stage")]
    r = solve(items, R)
    out = boxes(r, items)
    assert r.problems == [] and inside(out["w"], STAGE)
    assert abs(r.adjustments[0].scale - 12 / 16) < 1e-6
    # 宽到要缩过下限：停在下限，报出来
    items = [Item("w", Box(-15, 0, 15, 1), "stage")]
    r = solve(items, R, min_scale=0.6)
    assert abs(r.adjustments[0].scale - 0.6) < 1e-6
    assert any("w 超出区域 stage" in p for p in r.problems)


def test_push_out_of_region_then_shrink():
    # b 在 a 下面，往下挪会出区域：绕上沿缩小，留在区域内
    items = [Item("a", Box(-3, -1.5, 3, 1.5), "stage", fixed=True),
             Item("b", Box(-2, -2.0, 2, -1.0), "stage", anchor="a", side="below", gap=0.2)]
    r = solve(items, R)
    out = boxes(r, items)
    assert r.problems == []
    assert apart(out["a"], out["b"]) and inside(out["b"], STAGE)
    assert abs(r.adjustments[0].scale - 0.8) < 1e-6
    assert abs(out["b"].y1 - (-1.5 - 0.2)) < 1e-6  # 上沿贴着间距


def test_two_free_objects_split():
    # 两个对象都 in: stage，叠在中心：各让一半，都不缩
    items = [Item("a", Box(-2, -0.9, 2, -0.1), "stage"), Item("b", Box(-1.5, -0.9, 1.5, -0.1), "stage")]
    r = solve(items, R)
    out = boxes(r, items)
    assert r.problems == []
    assert apart(out["a"], out["b"]) and inside(out["a"], STAGE) and inside(out["b"], STAGE)
    assert all(a.scale == 1.0 for a in r.adjustments)


def test_unresolvable_is_reported():
    # 两个钉住的大对象互相盖住，另一个也没地方去：报冲突，不悄悄放过
    items = [Item("a", Box(-6, -2.5, 6, 1.5), "stage", fixed=True),
             Item("b", Box(-5, -2, 5, 1), "stage", fixed=True),
             Item("c", Box(-1, -1, 1, 1), "stage")]
    r = solve(items, R, min_scale=0.9)
    assert r.adjustments == []
    assert r.problems == ["a 与 c 重叠（2.00×2.00），求解器分不开", "b 与 c 重叠（2.00×2.00），求解器分不开"]
    # 两个都钉住的对象之间不归求解器管（交给 layout.check 报）


def test_over_and_within_left_alone():
    items = [Item("eq", Box(-2, -0.4, 2, 0.4), "stage"),
             Item("fill", Box(-0.5, -0.3, 0.5, 0.3), "stage", anchor="eq"),  # over: eq
             Item("tag", Box(1.0, -0.2, 1.8, 0.2), "stage")]
    r = solve(items, R, ignore_pairs={frozenset(("tag", "eq"))})
    assert r.adjustments == [] and r.problems == []


def test_deterministic():
    items = [Item(f"o{i}", Box(-1 + 0.1 * i, -0.5 + 0.05 * i, 1 + 0.1 * i, 0.5 + 0.05 * i), "stage") for i in range(4)]
    first = solve(items, R)
    for _ in range(3):
        again = solve(items, R)
        assert again.adjustments == first.adjustments and again.problems == first.problems
    out = boxes(first, items)
    assert first.problems == []
    ids = list(out)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            assert apart(out[a], out[b])


def test_resolve_applies_to_nodes():
    from moxian.core import Group, PathNode, Style

    root = Group(name="root")
    a = PathNode(path=rounded_rect(4, 1, 0.1), style=Style(fill="#000")).move_to((0, 0))
    b = PathNode(path=rounded_rect(3, 1, 0.1), style=Style(fill="#000")).move_to((0.5, 0.2))
    c = PathNode(path=rounded_rect(1, 0.5, 0.1), style=Style(fill="#000")).move_to((0, -0.8))
    for n in (a, b, c):
        root.add(n)
    visible = {"a": a, "b": b, "c": c}
    defs = {"a": {"in": "stage"}, "b": {"in": "stage"}, "c": {"below": "b", "gap": "sm"}}
    problems = layout.resolve(visible, defs, {k: STAGE for k in visible}, fixed={"a"})
    assert problems == []
    assert layout.check(visible, set()) == []
    assert abs(a.center()[0]) < 1e-9 and abs(a.center()[1]) < 1e-9  # 固定的不动
    assert b.bbox().y0 - c.bbox().y1 > 0.19  # c 跟着 b 走，间距还在
