"""钱包和余额条（负负得正 #15）：不渲染视频，只看卡片、余额和读数。"""

from animind_kit import theme
from animind_kit.mx import widgets
from animind_kit.mx.wallet import Wallet

T = theme.theme("paper")


def run(anims):
    for a in anims:
        a.begin()
        a.finish()


def wallet(**extra) -> Wallet:
    return Wallet("w", {"widget": "wallet", "cash": 2, "iou": 2, **extra}, T, {})


def opacity(node) -> float:
    return node.style.opacity if node.style else 1.0


def test_registered_and_starts_at_zero():
    w = wallet()
    assert widgets.lookup("wallet") is Wallet
    assert w.total == 0 and w.shown == 0


def test_four_cases_from_zero():
    for act, kind, total in (("put", "cash", 6), ("put", "iou", -6), ("take", "cash", -6), ("take", "iou", 6)):
        w = wallet()
        anims = w.action(act, {"kind": kind, "count": 2})
        assert w.total == total and w.shown == 0  # 建动作时余额条还没动
        run(anims)
        assert w.shown == total and opacity(w.readout) == 1.0


def test_torn_iou_falls_apart_at_playback():
    w = wallet()
    card = w.rows["iou"][-1][0]
    anims = w.action("take", {"kind": "iou", "count": 1})
    halves = [n for n in w.cards["iou"].children if n is not card and opacity(n) == 0.0]
    assert len(halves) >= 2 and opacity(card) == 1.0
    run(anims)
    assert opacity(card) == 0.0 and all(opacity(h) == 0.0 for h in halves[-2:])  # 落下去淡出


def test_put_cards_with_their_own_value():
    w = wallet(cash=0, iou=0)
    run(w.action("put", {"kind": "iou", "value": 2}) + w.action("put", {"kind": "iou", "value": 3}))
    assert w.total == -5 and w.shown == -5


def test_quiet_take_just_fades():
    w = wallet()
    run(w.action("put", {"kind": "iou", "count": 2}))
    before = len(w.cards["iou"].children)
    run(w.action("take", {"kind": "iou", "count": 2, "quiet": True}))
    assert len(w.cards["iou"].children) == before and w.total == 0  # 没有撕开的两半
