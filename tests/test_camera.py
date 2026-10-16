"""墨线后端的 camera 动作：放大到目标、填满、复位；钉住的和 top / footer 的对象不跟镜头走。"""

from pathlib import Path

import numpy as np
import pytest
from moxian.anim import CameraMove
from moxian.core import FRAME_HEIGHT
from moxian.render import SkiaRenderer
from scene_spec.loader import load_file

from animind_kit import theme
from animind_kit.backends.moxian_backend import DEFAULT_DUR, MoxianCompiler

EXAMPLE = Path(__file__).resolve().parents[2] / "scene-spec" / "examples" / "s67-p01.scene.yaml"
T = theme.theme("paper")


@pytest.fixture
def compiler() -> MoxianCompiler:
    return MoxianCompiler(load_file(EXAMPLE), T, None, log=lambda *_: None)


def run(anims) -> None:
    for a in anims:
        a.begin()
        a.finish()


def test_camera_starts_as_identity(compiler):
    assert compiler.camera.is_identity()


def test_zoom_to_target_then_reset(compiler):
    compiler.place("table")
    box = compiler.wbox(compiler.resolve("table"))
    anims, dur = compiler.animations({"act": "camera", "target": "table", "args": {"zoom": 3}})
    assert dur == DEFAULT_DUR["camera"] and len(anims) == 1 and isinstance(anims[0], CameraMove)
    run(anims)
    assert compiler.camera.zoom == 3.0 and np.allclose(compiler.camera.center, box.center)
    anims, _ = compiler.animations({"act": "camera", "args": {"reset": True}})
    run(anims)
    assert compiler.camera.is_identity()


def test_fit_fills_frame_with_margin(compiler):
    compiler.place("table")
    box = compiler.wbox(compiler.resolve("table"))
    run(compiler.animations({"act": "camera", "target": "table", "args": {"fit": True, "margin": 0.2}})[0])
    view = compiler.camera.view_box()
    assert view.x0 <= box.x0 and view.x1 >= box.x1 and view.y0 <= box.y0 and view.y1 >= box.y1
    # 至少一个方向刚好贴到留白
    assert min(box.x0 - view.x0, box.y0 - view.y0) * compiler.camera.zoom == pytest.approx(0.2, abs=1e-6)


def test_zoom_without_target_keeps_center(compiler):
    run(compiler.animations({"act": "camera", "args": {"zoom": 2}})[0])
    assert compiler.camera.zoom == 2.0 and np.allclose(compiler.camera.center, [0, 0])


def test_pinned_and_footer_objects_are_exempt(compiler):
    compiler._camera_exempt()
    assert compiler.objects["card"].node.fixed  # pin: top
    assert compiler.objects["plan"].node.fixed  # in: footer
    assert not compiler.objects["table"].node.fixed
    assert not compiler.objects["title"].node.fixed


def test_pinned_card_stays_put_in_rendered_frame(compiler):
    for k in ("card", "table"):
        compiler.place(k)
        compiler.objects[k].node.set_style(opacity=1.0)
    compiler._camera_exempt()
    w, h = 480, 270
    before = np.asarray(SkiaRenderer(w, h, background=T.BG, camera=compiler.camera).render(compiler.root).toarray())
    run(compiler.animations({"act": "camera", "target": "table", "args": {"zoom": 2.5}})[0])
    after = np.asarray(SkiaRenderer(w, h, background=T.BG, camera=compiler.camera).render(compiler.root).toarray())
    card = compiler.wbox(compiler.objects["card"].node)
    top_rows = int(round((FRAME_HEIGHT / 2 - card.y0) * h / FRAME_HEIGHT)) - 2
    # 题卡那一条：放大后不变（舞台被放大的部分可能盖进来，只比卡片本身的范围）
    x0 = int(round(w / 2 + card.x0 * h / FRAME_HEIGHT)) + 2
    x1 = int(round(w / 2 + card.x1 * h / FRAME_HEIGHT)) - 2
    assert np.array_equal(before[2:top_rows, x0:x1], after[2:top_rows, x0:x1])
    # 舞台变了
    assert not np.array_equal(before[top_rows:], after[top_rows:])
