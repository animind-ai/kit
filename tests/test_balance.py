"""天平（解方程就是天平 #19）：不渲染视频，只看梁的角度、砝码的位置和透明度。"""

import pytest

from animind_kit import theme
from animind_kit.mx import widgets
from animind_kit.mx.balance import TILT, Balance

T = theme.theme("paper")


def run(anims):
    for a in anims:
        a.begin()
        a.finish()


def scale(**extra) -> Balance:
    d = {"widget": "balance", "left": [1] * 7, "right": ["box", 1, 1, 1, 1, 1], "box": 2, **extra}
    return Balance("b", d, T, {})


def test_registered_and_level_when_equal():
    b = scale()
    assert widgets.lookup("balance") is Balance
    assert b.weight("left") == b.weight("right") == 7 and b.shown_angle == 0.0


def test_remove_from_both_keeps_level_and_leaves_the_box():
    b = scale()
    gone = b.slots["right"][-1]
    anims = b.action("remove", {"side": "both", "count": 5})
    assert b.items["right"] == ["box"] and len(b.items["left"]) == 2
    assert gone.style.opacity == 1.0  # 建动作时还在
    run(anims)
    assert gone.style.opacity == 0.0 and b.shown_angle == 0.0


def test_one_side_only_tilts_then_add_back_levels():
    b = scale()
    run(b.action("remove", {"side": "left", "count": 2}))
    assert b.shown_angle == pytest.approx(-TILT)  # 右边重，右端往下
    right_end = b.pans["right"].matrix[1, 2]
    assert right_end < 0
    run(b.action("add", {"side": "left", "count": 2, "role": "C"}))
    assert b.shown_angle == 0.0 and len(b.slots["left"]) == 7
    assert b.slots["left"][-1].style.opacity == 1.0


def test_label_swaps_the_box_text_at_playback():
    b = scale(box_label="?")
    old = b.box_text
    anims = b.action("label", {"text": "2"})
    assert old.style is None or old.style.opacity == 1.0
    run(anims)
    assert old.style.opacity == 0.0 and b.box_text.style.opacity == 1.0
