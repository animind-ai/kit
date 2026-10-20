"""type: clip（墨线后端）：视频和帧序列当底板，show 决定开始播，分镜按成片时刻取帧，扫描把底板当填充图形。"""

import shutil
import subprocess

import numpy as np
import pytest
import skia
import yaml
from moxian.clip import ClipNode
from moxian.core import Group
from moxian.render import SkiaRenderer

from animind_kit import lint, segments, theme, timing
from animind_kit.backends.manim_backend import Compiler as ManimCompiler
from animind_kit.backends.moxian_backend import MoxianCompiler
from animind_kit.cli import main
from animind_kit.mx import scan

T = theme.theme("paper")
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")

SPEC = """\
spec: "0.1"
meta: {{id: clip, title: 片段, line: worked, author: t, license: CC-BY-4.0}}
pedagogy: {{insight: 底板, status: method}}
style: {{theme: paper, subtitles: none}}
objects:
  plate:
    type: clip
    src: {src}
    kind: {kind}
    content_kind: plate
    explain: allow
{extra}  eq: {{type: math, tex: 'a+b=c', in: stage}}
beats:
  - id: One
    say:
      - {{id: s1, pace: normal, text: "看这里。"}}
    do:
      - {{at: 0, act: show, target: plate}}
      - {{at: s1, act: show, target: eq}}
  - id: Two
    say:
      - {{id: s1, pace: normal, text: "再看。"}}
    do:
      - {{at: s1, act: hide, target: eq}}
"""


def frames_dir(directory, colors, size=(64, 36)):
    """写一组纯色 PNG（1.png、2.png …），返回目录。"""
    directory.mkdir(parents=True, exist_ok=True)
    for i, (r, g, b) in enumerate(colors, start=1):
        surface = skia.Surface(*size)
        with surface as canvas:
            canvas.clear(skia.ColorSetARGB(255, r, g, b))
        surface.makeImageSnapshot().save(str(directory / f"{i}.png"), skia.EncodedImageFormat.kPNG)
    return directory


def write_spec(tmp_path, src, kind, fps=None, extra=""):
    if fps is not None:
        extra += f"    fps: {fps}\n"
    spec = tmp_path / "clip.scene.yaml"
    spec.write_text(SPEC.format(src=src, kind=kind, extra=extra), encoding="utf-8")
    return spec


def compiler_for(spec):
    doc = yaml.safe_load(spec.read_text(encoding="utf-8"))
    return MoxianCompiler(doc, T, None, log=lambda *_: None, quality="m", spec_dir=spec.parent)


def mean_rgb(compiler, t, beat_plan):
    """成片时刻 t（beat 内）的画面平均颜色。"""
    beat_plan.tl.seek(t)
    renderer = SkiaRenderer(160, 90, background=T.BG, camera=compiler.camera)
    renderer.time = t
    image = np.asarray(renderer.render(compiler.root).toarray())[:, :, :3].astype(float)
    return image.mean(axis=(0, 1))


@needs_ffmpeg
def test_video_clip_renders_and_inspects_with_exit_zero(tmp_path):
    video = tmp_path / "plate.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10",
                    "-t", "2", "-pix_fmt", "yuv420p", str(video)], check=True)
    spec = write_spec(tmp_path, "plate.mp4", "video")
    assert main(["inspect", str(spec), "--no-sheets", "--out", str(tmp_path / "ins")]) == 0
    assert main(["render", str(spec), "--out", str(tmp_path / "clip.mp4"), "--quality", "l"]) == 0
    assert (tmp_path / "clip.mp4").exists()


def test_frame_clip_renders_and_inspects_with_exit_zero(tmp_path):
    src = frames_dir(tmp_path / "plate", [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
    spec = write_spec(tmp_path, src.name, "frames", fps=2)
    assert main(["inspect", str(spec), "--no-sheets", "--out", str(tmp_path / "ins")]) == 0
    assert main(["render", str(spec), "--out", str(tmp_path / "clip.mp4"), "--quality", "l"]) == 0


def test_samples_at_different_times_show_different_frames(tmp_path):
    src = frames_dir(tmp_path / "plate", [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
    spec = write_spec(tmp_path, src.name, "frames", fps=2)
    c = compiler_for(spec)
    plan = c._plan(c.doc["beats"][0])
    # 显示在 0.3 秒（第一句开头），fps 2：0.3–0.8 是第一帧（红），0.8–1.3 第二帧（绿），1.3 起第三帧（蓝）
    early = mean_rgb(c, 0.6, beat_plan=plan)
    middle = mean_rgb(c, 1.0, beat_plan=plan)
    late = mean_rgb(c, 1.5, beat_plan=plan)
    assert early[0] > early[1] and early[0] > early[2]  # 红
    assert middle[1] > middle[0] and middle[1] > middle[2]  # 绿
    assert late[2] > late[0] and late[2] > late[1]  # 蓝


@needs_ffmpeg
def test_video_samples_show_different_frames(tmp_path):
    video = tmp_path / "plate.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10",
                    "-t", "2", "-pix_fmt", "yuv420p", str(video)], check=True)
    spec = write_spec(tmp_path, "plate.mp4", "video")
    c = compiler_for(spec)
    plan = c._plan(c.doc["beats"][0])
    node = c.objects["plate"].node
    assert node.frame_index_at(0.5) != node.frame_index_at(1.2)
    a, b = mean_rgb(c, 0.5, beat_plan=plan), mean_rgb(c, 1.2, beat_plan=plan)
    assert np.abs(a - b).mean() > 1.0


def test_clip_starts_at_show_and_offsets_by_start(tmp_path):
    src = frames_dir(tmp_path / "plate", [(255, 0, 0), (0, 255, 0)])
    c = compiler_for(write_spec(tmp_path, src.name, "frames", fps=2))
    c._plan(c.doc["beats"][0])
    node = c.objects["plate"].node
    assert node.start == pytest.approx(0.3)  # at: 0 → 第一句开头，show 时刻
    assert node.frame_index_at(0.29) is None  # 还没开始不画
    assert node.frame_index_at(0.31) == 0

    c = compiler_for(write_spec(tmp_path, src.name, "frames", fps=2, extra="    start: 1.0\n"))
    c._plan(c.doc["beats"][0])
    assert c.objects["plate"].node.start == pytest.approx(1.3)  # show + start


def test_clip_keeps_playing_across_beats_without_restarting(tmp_path):
    src = frames_dir(tmp_path / "plate", [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
    c = compiler_for(write_spec(tmp_path, src.name, "frames", fps=2))
    plan_one = c._plan(c.doc["beats"][0])
    node = c.objects["plate"].node
    film_start = c.clip_t0["plate"]
    c.offset += round(plan_one.until * c.fps) / c.fps + 1 / c.fps  # 同 inspect：下一个 beat 从这里开始
    c._plan(c.doc["beats"][1])
    assert node.start == pytest.approx(film_start - c.offset)  # 同一个成片时刻，换成 beat 二的时钟
    # 片段开始得早、beat 二开头时已经播过了：取的是后面的帧（三帧、fps 2，播完停在最后一帧）
    assert node.frame_index_at(0.0) == 2


def test_muted_must_be_true(tmp_path):
    src = frames_dir(tmp_path / "plate", [(255, 0, 0)])
    spec = write_spec(tmp_path, src.name, "frames", fps=2, extra="    muted: false\n")
    with pytest.raises(ValueError, match="muted"):
        compiler_for(spec)


def test_manim_backend_rejects_clip_early(tmp_path):
    doc = {"spec": "0.1", "objects": {"plate": {"type": "clip", "src": "x", "kind": "video", "content_kind": "plate"}},
           "beats": []}
    with pytest.raises(NotImplementedError, match="moxian"):
        ManimCompiler(doc, T, None, log=lambda *_: None)


def _plate_rect(width, height):
    return np.array([[-width / 2, -height / 2], [width / 2, -height / 2], [width / 2, height / 2], [-width / 2, height / 2]])


def test_scan_sees_clip_bbox_and_flags_out_of_frame_plate(tmp_path):
    src = frames_dir(tmp_path / "wide", [(255, 0, 0)])
    root = Group(name="root")
    plate = ClipNode(str(src), kind="frames", fps=2, region=(-10.0, -1.0, 10.0, 1.0), fit="stretch", z=-1.0)
    root.add(plate)
    labels = {id(plate): scan.Label("plate", "plate")}
    things = scan.scan(root, labels)
    assert len(things) == 1
    assert things[0].act == "plate" and things[0].kind == "shape"
    assert things[0].box == pytest.approx((-10.0, -1.0, 10.0, 1.0))
    problems = lint.check(things)
    assert [p.kind for p in problems] == ["clip"]


def test_plate_covers_text_below_it_but_not_text_above_it():
    plate_pts = _plate_rect(8.0, 4.0)
    eq_box = (-1.0, -0.2, 1.0, 0.2)

    def text(order):
        return lint.Thing("text", "eq", "eq", eq_box, order, run=0, text="a+b", em=60.0)

    def plate(order):
        return lint.Thing("shape", "plate", "plate", (-4.0, -2.0, 4.0, 2.0), order, act="plate",
                          curves=[plate_pts], closed=[True], fill=1.0)

    above = lint.check([plate(0), text(1)])
    assert not [p for p in above if p.kind == "covered"]
    below = lint.check([text(0), plate(1)])
    assert [p.kind for p in below] == ["covered"]


def test_plate_edge_is_not_a_line_through_text():
    # 字的一半在底板里、一半在外面：底板的边不算线穿过字
    plate = lint.Thing("shape", "plate", "plate", (-4.0, -2.0, 4.0, 2.0), 0, act="plate",
                       curves=[_plate_rect(8.0, 4.0)], closed=[True], fill=1.0)
    straddle = lint.Thing("text", "eq", "eq", (3.6, -0.2, 4.6, 0.2), 1, run=0, text="ab", em=60.0)
    assert not [p for p in lint.check([plate, straddle]) if p.kind == "cross"]


# ---------- 未开始的片段不是遮挡物；按 fit 的实际画面范围；循环素材的真实帧 ----------

COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0)]  # 循环素材的四帧：红、绿、蓝、黑


def nearest_color(rgb):
    """把平均颜色对回素材的四种颜色之一（画面底色离它们都远）。"""
    distances = [np.abs(rgb - np.array(c, dtype=float)).sum() for c in COLORS]
    return int(np.argmin(distances))


def plate_things(compiler, plan, t):
    """时刻 t 的画面上，扫描到的底板。要先把时间线拨到 t（淡入淡出都在时间线上）。"""
    plan.tl.seek(t)
    return [th for th in scan.scan(compiler.root, compiler.labels(), compiler.camera, 0.15, t) if th.act == "plate"]


def problems_at(compiler, plan, t):
    plan.tl.seek(t)
    return compiler._lint_now(compiler.labels(), t)


def test_unstarted_clip_is_not_an_occluder(tmp_path):
    src = frames_dir(tmp_path / "plate", COLORS[:2])
    # 插图在 z=1（盖在字上面），延后 10 秒开始：淡入之后也还没画，不能当成盖住字的底板
    c = compiler_for(write_spec(tmp_path, src.name, "frames", fps=2, extra="    z: 1\n    start: 10.0\n"))
    plan = c._plan(c.doc["beats"][0])
    assert plate_things(c, plan, 1.5) == []
    assert "covered" not in [p.kind for p in problems_at(c, plan, 1.5)]
    assert len(plate_things(c, plan, 11.0)) == 1
    assert "covered" in [p.kind for p in problems_at(c, plan, 11.0)]


@pytest.mark.parametrize("fit,expected", [
    ("contain", (-2.0, -2.0, 2.0, 2.0)),  # 正方形素材放进 8×4 槽位：只占中间一块
    ("cover", (-4.0, -2.0, 4.0, 2.0)),
    ("stretch", (-4.0, -2.0, 4.0, 2.0)),
])
def test_plate_geometry_follows_fit(tmp_path, fit, expected):
    src = frames_dir(tmp_path / "square", [(255, 0, 0)], size=(64, 64))
    node = ClipNode(str(src), kind="frames", fps=2, region=(-4.0, -2.0, 4.0, 2.0), fit=fit)
    root = Group(name="root")
    root.add(node)
    things = scan.scan(root, {id(node): scan.Label("plate", "plate")})
    assert things[0].box == pytest.approx(expected)


@pytest.mark.parametrize("fit,covered", [("contain", False), ("cover", True)])
def test_text_in_contain_margin_is_not_covered(tmp_path, fit, covered):
    src = frames_dir(tmp_path / "square", [(255, 0, 0)], size=(64, 64))
    node = ClipNode(str(src), kind="frames", fps=2, region=(-4.0, -2.0, 4.0, 2.0), fit=fit)
    root = Group(name="root")
    root.add(node)
    plate = scan.scan(root, {id(node): scan.Label("plate", "plate")})[0]
    plate.order = 1  # 底板画在字的上面：字在它下面，看它有没有盖住字
    eq = lint.Thing("text", "eq", "eq", (2.5, -0.2, 3.5, 0.2), 0, run=0, text="a", em=60.0)
    kinds = [p.kind for p in lint.check([eq, plate])]
    assert ("covered" in kinds) is covered


def write_loop_spec(tmp_path, src, segmented, actions, text_long="这一句稍微长一点。"):
    spec = tmp_path / "loop.scene.yaml"
    spec.write_text(LOOP_SPEC.format(src=src, segmented=segmented, actions=actions, long=text_long), encoding="utf-8")
    return spec


LOOP_SPEC = """\
spec: "0.1"
meta: {{id: loop, title: 循环, line: worked, author: t, license: CC-BY-4.0}}
pedagogy: {{insight: 循环, status: method}}
style: {{theme: paper, subtitles: none, segmented: {segmented}}}
objects:
  plate:
    type: clip
    src: {src}
    kind: frames
    fps: 2
    end: loop
    content_kind: plate
    explain: allow
beats:
  - id: One
    say:
      - {{id: s1, pace: normal, text: "一。"}}
      - {{id: s2, pace: normal, text: "{long}"}}
      - {{id: s3, pace: normal, text: "三。"}}
    do:
{actions}  - id: Two
    say:
      - {{id: s1, pace: normal, text: "四。"}}
    do:
      - {{at: 0, act: hold, dur: 0.5}}
"""

SHOW_HIDE_SHOW = """\
      - {at: 0, act: show, target: plate}
      - {at: s2, act: hide, target: plate}
      - {at: s3, act: show, target: plate}
"""


def test_loop_clip_shows_expected_frames_across_hide_reshow_and_beats(tmp_path):
    src = frames_dir(tmp_path / "loop", COLORS)  # 四帧，fps 2：一圈两秒，循环播
    spec = write_loop_spec(tmp_path, src.name, "false", SHOW_HIDE_SHOW)
    c = compiler_for(spec)
    beat_one, beat_two = c.doc["beats"]
    timeline = timing.load(beat_one, None)
    t0, s2, s3 = (timeline.sentences[i].start for i in range(3))  # 片段第一次 show 的时刻
    fps = 2.0

    def expected(film):  # 连续播放：从第一次 show 起按 fps 走，循环
        return int(np.floor((film - t0) * fps + 1e-9)) % len(COLORS)

    plan_one = c._plan(beat_one)
    node = c.objects["plate"].node
    assert node.start == pytest.approx(t0)
    # 同一 beat 内：show → hide → show。淡入之后的画面要和连续播放一致
    for local in (t0 + 1.0, t0 + 1.6):
        assert nearest_color(mean_rgb(c, local, beat_plan=plan_one)) == expected(local)
    assert plate_things(c, plan_one, s2 + 0.5) == []  # 隐藏期间不画，也不参与检查
    # 重新显示后的采样点：挑一个「接着播」和「从头播」结果不同的时刻，测试才分得出两者
    offsets = [d for d in (0.8, 1.0, 1.2, 1.4, 1.6, 1.8) if expected(s3 + d) != int(np.floor(d * fps)) % len(COLORS)]
    resume = s3 + offsets[0]
    assert nearest_color(mean_rgb(c, resume, beat_plan=plan_one)) == expected(resume)

    # 跨 beat：第二个 beat 开头继续在播（没有重新 show，画面也没有隐藏）
    c.offset += round(plan_one.until * c.fps) / c.fps + 1 / c.fps
    plan_two = c._plan(beat_two)
    for local in (0.05, 0.6):
        film = c.offset + local
        assert nearest_color(mean_rgb(c, local, beat_plan=plan_two)) == expected(film)
    assert node.start == pytest.approx(t0 - c.offset)


FREEZE_BODY = """\
      - {at: 0, act: show, target: plate}
      - {at: s2, act: step, args: {hold: 2}}
"""


def test_clip_keeps_playing_during_a_step_freeze(tmp_path):
    # 定格期间：矢量内容停住，但成片时钟还在走，片段照常播（定格算进成片时间）
    src = frames_dir(tmp_path / "loop", COLORS)
    spec = write_loop_spec(tmp_path, src.name, "true", FREEZE_BODY)
    c = compiler_for(spec)
    plan = c._plan(c.doc["beats"][0])
    assert len(plan.tl.freezes) == 1
    frozen_from = segments.freeze_starts(plan.tl.freezes)[0]
    early, late = frozen_from + 0.2, frozen_from + 1.8  # 都在两秒的定格里
    fps = 2.0
    t0 = c.clip_t0["plate"]
    assert nearest_color(mean_rgb(c, early, beat_plan=plan)) == int(np.floor((early - t0) * fps + 1e-9)) % len(COLORS)
    assert nearest_color(mean_rgb(c, late, beat_plan=plan)) == int(np.floor((late - t0) * fps + 1e-9)) % len(COLORS)
    assert nearest_color(mean_rgb(c, early, beat_plan=plan)) != nearest_color(mean_rgb(c, late, beat_plan=plan))


def sized_frames(directory, specs):
    """specs：[(宽, 高, 颜色)]，每帧一个尺寸。返回目录（文件名 1.png、2.png …）。"""
    directory.mkdir(parents=True, exist_ok=True)
    for i, (w, h, (r, g, b)) in enumerate(specs, start=1):
        surface = skia.Surface(w, h)
        with surface as canvas:
            canvas.clear(skia.ColorSetARGB(255, r, g, b))
        surface.makeImageSnapshot().save(str(directory / f"{i}.png"), skia.EncodedImageFormat.kPNG)
    return directory


def plate_box_at(node, t):
    root = Group(name="root")
    root.add(node)
    things = scan.scan(root, {id(node): scan.Label("plate", "plate")}, None, 0.15, t)
    return things[0].box if things else None


def test_frame_sequence_scan_uses_the_current_frames_size(tmp_path):
    # 第一帧正方形（contain 留白），第二帧 2:1（刚好占满 8×4 槽位）：扫描要跟着当前这一帧走
    src = sized_frames(tmp_path / "mixed", [(64, 64, (255, 0, 0)), (128, 64, (0, 255, 0))])
    node = ClipNode(str(src), kind="frames", fps=2, region=(-4.0, -2.0, 4.0, 2.0), fit="contain")
    assert plate_box_at(node, 0.1) == pytest.approx((-2.0, -2.0, 2.0, 2.0))
    assert plate_box_at(node, 0.6) == pytest.approx((-4.0, -2.0, 4.0, 2.0))


def test_frame_sequence_scan_honours_nonzero_trim(tmp_path):
    # trim.in = 0.5 秒（fps 2，即从第二帧开始）：第一帧正方形从来不画，扫描也不能按它算
    src = sized_frames(tmp_path / "mixed", [(64, 64, (255, 0, 0)), (128, 64, (0, 255, 0))])
    node = ClipNode(str(src), kind="frames", fps=2, t_in=0.5, region=(-4.0, -2.0, 4.0, 2.0), fit="contain")
    assert node.frame_index_at(0.1) == 1
    assert plate_box_at(node, 0.1) == pytest.approx((-4.0, -2.0, 4.0, 2.0))
    assert plate_box_at(node, 0.9) == pytest.approx((-4.0, -2.0, 4.0, 2.0))  # 循环之外，hold 在最后一帧


def test_text_in_margin_is_covered_only_when_the_current_frame_fills_the_slot(tmp_path):
    src = sized_frames(tmp_path / "mixed", [(64, 64, (255, 0, 0)), (128, 64, (0, 255, 0))])
    node = ClipNode(str(src), kind="frames", fps=2, region=(-4.0, -2.0, 4.0, 2.0), fit="contain")
    root = Group(name="root")
    root.add(node)
    eq = lint.Thing("text", "eq", "eq", (2.5, -0.2, 3.5, 0.2), 0, run=0, text="a", em=60.0)
    for t, covered in ((0.1, False), (0.6, True)):
        plate = scan.scan(root, {id(node): scan.Label("plate", "plate")}, None, 0.15, t)[0]
        plate.order = 1
        assert ("covered" in [p.kind for p in lint.check([eq, plate])]) is covered


def frame_mean(video, t, workdir):
    """从编码好的 mp4 抽出时刻 t 的一帧，返回平均颜色（RGB）。"""
    png = workdir / f"f{t:.3f}.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-ss", f"{t:.3f}", "-frames:v", "1", str(png)],
                   check=True)
    image = skia.Image.open(str(png)).convert(colorType=skia.kRGBA_8888_ColorType,
                                              alphaType=skia.kUnpremul_AlphaType).toarray()[:, :, :3]
    return image.reshape(-1, 3).mean(axis=0).astype(float)


@needs_ffmpeg
def test_encoded_cross_beat_show_hide_show_through_render(tmp_path):
    # 走 render() 的真实路径：每个 beat 编码、用探测到的时长累计、拼成一支片子，再从成片里抽帧
    from animind_kit.render import probe_seconds, render

    src = frames_dir(tmp_path / "loop", COLORS)
    spec = write_loop_spec(tmp_path, src.name, "false", SHOW_HIDE_SHOW)
    out = tmp_path / "loop.mp4"
    render(spec, out, None, "l", log=lambda *_: None, backend="moxian")
    parts = out.parent / f".build-{spec.stem}-moxian" / "parts"
    beat_one = probe_seconds(parts / "One.mp4")
    timeline = timing.load(yaml.safe_load(spec.read_text(encoding="utf-8"))["beats"][0], None)
    t0, s2, s3 = (timeline.sentences[i].start for i in range(3))
    fps = 2.0

    def centre(film):  # 每一帧的中点：离帧边界远，编码误差不影响判断
        k = int(np.floor((film - t0) * fps + 1e-9))
        return t0 + (k + 0.5) / fps

    def expected(film):
        return int(np.floor((film - t0) * fps + 1e-9)) % len(COLORS)

    def colour_at(film, video, local):
        return nearest_color(frame_mean(video, local, tmp_path))

    one, two = parts / "One.mp4", parts / "Two.mp4"
    for film in (t0 + 0.75, t0 + 1.25):  # 第一次 show 之后、隐藏之前
        local = centre(film)
        assert colour_at(local, one, local) == expected(local)
    resume_offsets = [d for d in (0.8, 1.0, 1.2, 1.4) if expected(centre(s3 + d)) != int(np.floor(d * fps)) % len(COLORS)]
    resume = centre(s3 + resume_offsets[0])
    assert colour_at(resume, one, resume) == expected(resume)  # 重新 show：接着播，不是从头
    # 第二个 beat：片子里的时刻是 beat 一的时长加上 beat 二的局部时刻
    for local in (0.05, 0.4):
        film = beat_one + local
        target = centre(film)
        assert colour_at(film, two, target - beat_one) == expected(target)
