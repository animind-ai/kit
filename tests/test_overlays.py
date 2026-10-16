"""叠加层（强调框、标记、圈、划线、叉）跟着目标走：几何在入场时才算，之后目标移动也跟着。不渲染视频。"""

import numpy as np
from moxian.anim import together

from animind_kit import theme
from animind_kit.backends.moxian_backend import MoxianCompiler, _FollowTimeline

T = theme.theme("paper")

DOC = {
    "spec": "0.1",
    "meta": {"id": "t", "title": "t"},
    "style": {"theme": "paper"},
    "objects": {
        "pyth": {"widget": "area_model", "kind": "right_triangles", "count": 4, "legs": [2, 3],
                 "layout": "four_around_c", "role": "A", "in": "stage"},
        "eq": {"type": "math", "tex": "a^2+b^2=c^2", "in": "side"},
    },
    "beats": [{"id": "b1", "do": [{"act": "show", "target": "pyth"}, {"act": "show", "target": "eq"}]}],
}


def compiler() -> MoxianCompiler:
    c = MoxianCompiler(DOC, T, None, log=lambda *_: None)
    for obj in ("pyth", "eq"):
        play(c, {"act": "show", "target": obj})
    return c


def play(c: MoxianCompiler, action: dict) -> None:
    """把一个动作从头播到尾（和时间线一样：先 begin，再落到终态，再让叠加层跟上）。"""
    anims, dur = c.animations(action)
    tl = _FollowTimeline(follow=c._follow)
    tl.add(0.0, together(*anims))
    tl.seek(0.0)
    tl.seek(dur + 1.0)


def centered_on(c: MoxianCompiler, overlay, target: str) -> bool:
    box, own = c.wbox(c.resolve(target)), overlay.world_bbox(c.root)
    return bool(np.allclose(own.center, box.center, atol=1e-6)) and own.width > box.width


def test_focus_frame_follows_slide():
    c = compiler()
    play(c, {"act": "focus", "target": "pyth.tri[0]"})
    frame = c.focus_boxes["pyth"]
    assert centered_on(c, frame, "pyth.tri[0]")
    before = c.wbox(c.resolve("pyth.tri[0]")).center
    play(c, {"act": "slide", "target": "pyth", "args": {"layout": "two_rectangles"}})
    after = c.wbox(c.resolve("pyth.tri[0]")).center
    assert not np.allclose(before, after)  # 这一块确实挪了
    assert centered_on(c, frame, "pyth.tri[0]")


def test_overlays_follow_move():
    c = compiler()
    for act in ("mark", "circle", "underline", "cross"):
        play(c, {"act": act, "target": "eq"})
    pad = c.marks["eq"]
    decos = {act: deco for act, _sel, deco in c.decorations}
    old_pad, old_line = pad.world_bbox(c.root), decos["underline"].world_bbox(c.root)
    play(c, {"act": "move", "target": "eq", "args": {"at": [-3.0, -2.5]}})
    box = c.wbox(c.objects["eq"].node)
    delta = pad.world_bbox(c.root).center - old_pad.center
    assert np.linalg.norm(delta) > 0.5
    assert np.allclose(pad.world_bbox(c.root).center, box.center, atol=1e-6)
    assert centered_on(c, decos["circle"], "eq")
    assert np.allclose(decos["underline"].world_bbox(c.root).center, old_line.center + delta, atol=1e-6)
    assert np.allclose(decos["cross"].world_bbox(c.root).center, box.center, atol=1e-6)


def test_geometry_is_measured_when_the_overlay_begins():
    """编译时目标还在旧位置；同一批里先挪目标，框在入场那一刻量，结束时正好框住新位置。"""
    c = compiler()
    anims, _ = c.animations({"act": "focus", "target": "eq"})
    frame = c.focus_boxes["eq"]
    assert frame.world_bbox(c.root) is None  # 还没入场，没有几何
    c.objects["eq"].node.shift([1.0, -0.5])  # 例如布局求解器在这之后才把它挪开
    tl = _FollowTimeline(follow=c._follow)
    tl.add(0.0, together(*anims))
    tl.seek(0.0)
    tl.seek(5.0)
    assert centered_on(c, frame, "eq")


def test_unfocus_stops_following():
    c = compiler()
    play(c, {"act": "focus", "target": "eq"})
    frame = c.focus_boxes["eq"]
    play(c, {"act": "unfocus", "target": "eq"})
    assert id(frame) not in c.followers
    assert frame.style.opacity == 0.0
