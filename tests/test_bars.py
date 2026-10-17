"""积木条形图（平均数削峰填谷）：不渲染视频，只看节点的位置和样式。"""

import numpy as np
import pytest

from animind_kit import theme
from animind_kit.mx import widgets
from animind_kit.mx.bars import BlockBars

T = theme.theme("paper")


def run(anims):
    for a in anims:
        a.begin()
        a.finish()


def xy(node) -> tuple[float, float]:
    return round(float(node.matrix[0, 2]), 6), round(float(node.matrix[1, 2]), 6)


def opacity(node) -> float:
    return node.style.opacity if node.style else 1.0


def bars(**extra) -> BlockBars:
    return BlockBars("b", {"widget": "block_bars", "heights": [5, 2, 6, 3], **extra}, T, {})


def test_registered():
    assert widgets.lookup("block_bars") is BlockBars


def test_columns_start_on_the_base_line():
    b = bars()
    assert [len(c) for c in b.cols] == [5, 2, 6, 3]
    assert xy(b.cols[0][0]) == tuple(np.round(b.pos(0, 0), 6))
    assert all(opacity(x) == 1.0 for c in b.cols for x in c)


def test_leveling_moves_top_blocks_at_playback():
    b = bars(numbers=True)
    top = b.cols[0][-1]
    before = xy(top)
    anims = b.action("transfer", {"from": 0, "to": 1}) + b.action("transfer", {"from": 2, "to": 1}) + b.action("transfer", {"from": 2, "to": 3})
    assert [len(c) for c in b.cols] == [4, 4, 4, 4]
    assert xy(top) == before  # 建动作时还没动
    run(anims)
    assert xy(top) == tuple(np.round(b.pos(1, 2), 6))  # 第一块落在第二列第三层
    shown = [n for n in b.marks.children if opacity(n) == 1.0]
    assert len(shown) == 4 and all(n is b.num_nodes[k] for k, n in enumerate(sorted(shown, key=lambda n: n.bbox().x0)))


def test_add_grows_a_new_column_in_the_reserved_slot():
    b = bars(cols=5, room=14)
    anims = b.action("add", {"height": 14, "label": "小郑"})
    assert len(b.cols[4]) == 14 and all(opacity(x) == 0.0 for x in b.cols[4])
    run(anims)
    assert all(opacity(x) == 1.0 for x in b.cols[4])
    assert all(x.style.fill == T.B for x in b.cols[4])


def test_level_line_replaces_the_old_one_and_recolor_waits():
    b = bars()
    run(b.action("level", {"at": 3, "label": "平均？", "role": "NO"}))
    first = list(b.line.children)
    anims = b.action("level", {"at": 4, "label": "平均 $4$"}) + b.action("recolor", {"col": 2, "role": "NO"})
    assert all(x.style.fill == T.A for x in b.cols[2])
    run(anims)
    assert all(opacity(n) == 0.0 for n in first)
    assert all(x.style.fill == T.NO for x in b.cols[2])
    run(b.action("unlevel", {}))
    assert all(opacity(n) == 0.0 for n in b.line.children)


def test_reserve_does_not_change_with_actions():
    b = bars(cols=5, room=14, numbers=True, labels=["甲", "乙", "丙", "丁", "戊"])
    before = b.node.bbox()
    run(b.action("add", {"height": 14}) + b.action("transfer", {"from": 4, "to": 1, "count": 2}))
    after = b.node.bbox()
    assert (after.x0, after.x1, after.y0) == pytest.approx((before.x0, before.x1, before.y0))
