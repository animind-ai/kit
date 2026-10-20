"""分段播放（墨线后端）：子步定格的时间轴映射、旁白插静音、残影快照、常驻对象。"""

import json
import shutil
import wave

import numpy as np
import pytest
from moxian.core import Group, PathNode, Style
from moxian.path import rect

from animind_kit import segments as seg
from animind_kit.render import probe_seconds, render
from animind_kit.theme import theme

FREEZES = [seg.Freeze(2.0, 1.0), seg.Freeze(5.0, 0.5)]


def test_time_mapping_round_trip():
    assert seg.to_video(1.0, FREEZES) == 1.0
    assert seg.to_video(2.0, FREEZES) == 3.0  # 定格时刻本身的事件排在定格之后
    assert seg.to_video(6.0, FREEZES) == 7.5
    assert seg.freeze_starts(FREEZES) == [2.0, 6.0]
    for v, t in [(1.0, 1.0), (2.0, 2.0), (2.7, 2.0), (3.0, 2.0), (5.9, 4.9), (6.2, 5.0), (7.0, 5.5)]:
        assert seg.to_logical(v, FREEZES) == pytest.approx(t)


def test_warped_timeline_holds_still_during_freeze():
    class Probe:
        end = 4.0

        def __init__(self):
            self.seen = []

        def seek(self, t):
            self.seen.append(t)
            return True

    inner = Probe()
    tl = seg.Warped(inner, [seg.Freeze(1.0, 2.0), seg.Freeze(3.0, 0.0)])
    assert tl.end == 6.0 and len(tl.freezes) == 1
    for v in (0.5, 1.0, 2.0, 2.9, 3.5):
        tl.seek(v)
    assert inner.seen == pytest.approx([0.5, 1.0, 1.0, 1.0, 1.5])
    assert len(list(tl.frames(10))) == 61


def test_insert_silence(tmp_path):
    src, dst = tmp_path / "a.wav", tmp_path / "b.wav"
    rate = 8000
    samples = (np.arange(rate * 2) % 100 + 1).astype(np.int16)  # 2 秒，没有一个零
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    seg.insert_silence(src, dst, [seg.Freeze(0.5, 1.0)])
    with wave.open(str(dst)) as r:
        out = np.frombuffer(r.readframes(r.getnframes()), dtype=np.int16)
    assert len(out) == rate * 3
    assert (out[: rate // 2] == samples[: rate // 2]).all()
    assert (out[rate // 2 : rate // 2 + rate] == 0).all()
    assert (out[rate // 2 + rate :] == samples[rate // 2 :]).all()


def test_ghost_snapshot_is_dim_and_frozen():
    t = theme("paper")
    root = Group(name="root")
    box = PathNode(path=rect(2.0, 1.0), style=Style(fill=t.A, stroke=t.FG))
    pad = PathNode(path=rect(2.2, 1.2), style=Style(fill=t.MARK))  # mark 垫色不拷
    obj = Group(pad, box, name="obj")
    obj.shift([1.0, 0.0])
    root.add(obj)
    state = seg.SegmentState()
    name, holder, anim = state.ghost("obj", obj, root, t, {"place": "above", "gap": "md", "scale": 0.5})
    assert name == "obj~ghost1" and holder in root.children and holder.z < 0
    anim.begin()
    anim.finish()
    assert len(holder.children) == 1
    leaf = holder.children[0]
    assert leaf.style.fill == t.DIM and leaf.style.stroke == t.DIM
    assert holder.style.opacity == pytest.approx(seg.GHOST_OPACITY)
    g, o = holder.bbox(), obj.bbox()
    assert g.width == pytest.approx(o.width * 0.5 * 2.0 / 2.2, rel=0.05)  # 只拷了 2 宽的方块，缩一半
    assert g.y0 == pytest.approx(box.bbox(obj.matrix).y1 + 0.4, abs=1e-6)
    obj.shift([3.0, 0.0])  # 原对象接着动，残影不动
    assert holder.bbox().x0 == pytest.approx(g.x0)
    names, fades = state.drop("obj", 0.3)
    assert names == ["obj~ghost1"] and len(fades) == 1 and state.ghosts == {}


SPEC = """\
spec: "0.1"
meta: {id: seg, title: 分段, line: worked, author: t, license: CC-BY-4.0}
pedagogy: {insight: 小样, status: method}
style: {theme: paper, segmented: SEG, subtitles: none}
objects:
  bar: {widget: summary, in: stage, rows: [{tag: 一, text: '$a+b$'}]}
  law: {type: math, tex: 'a+b=c', in: footer}
beats:
  - id: One
    say:
      - {id: s1, pace: normal, text: "看这里。"}
      - {id: s2, pace: normal, text: "再看。"}
    do:
      - {at: s1, act: show, target: bar}
      - {with: prev, act: show, target: law}
      - {after: prev, act: underline, target: law}
      - {after: prev, act: pin, target: law}
      - {at: s2, act: step, args: {hold: 2}}
      - {with: prev, act: ghost, target: bar, args: {place: above}}
  - id: Two
    say:
      - {id: s1, pace: normal, text: "收掉。"}
    do:
      - {at: s1, act: clear, target: law}
      - {with: prev, act: unghost, target: bar}
"""


def _end_frame(path, out):
    import subprocess

    import skia

    # 按帧定位：取最后一帧（不是结尾前的某个时刻，那样可能漏掉结束瞬间的问题）
    count = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                            "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{int(count) - 1})",
                    "-vsync", "0", "-frames:v", "1", str(out)], check=True)
    image = skia.Image.open(str(out))
    return image.convert(colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kUnpremul_AlphaType).toarray()[:, :, :3]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_steps_ghosts_and_pins(tmp_path):
    results = {}
    for segmented in ("true", "false"):
        spec = tmp_path / f"seg-{segmented}.scene.yaml"
        spec.write_text(SPEC.replace("SEG", segmented), encoding="utf-8")
        out = tmp_path / f"seg-{segmented}.mp4"
        summary = render(spec, out, None, "l", log=lambda *_: None, backend="moxian")
        assert summary["layout_problems"] == []
        parts = tmp_path / f".build-seg-{segmented}.scene-moxian" / "parts"
        results[segmented] = (summary, json.loads((tmp_path / f"seg-{segmented}.chapters.json").read_text()), parts)
    (_, ch_on, parts_on), (_, ch_off, parts_off) = results["true"], results["false"]
    # 分段：第一段多出 2 秒定格；不分段：step 只记下位置，hold 为 0
    one_on, one_off = probe_seconds(parts_on / "One.mp4"), probe_seconds(parts_off / "One.mp4")
    assert one_on - one_off == pytest.approx(2.0, abs=0.1)
    step_on, step_off = ch_on["chapters"][0]["steps"][0], ch_off["chapters"][0]["steps"][0]
    assert step_on["hold"] == 2.0 and step_on["resume"] == pytest.approx(step_on["time"] + 2.0)
    assert step_off["hold"] == 0.0 and step_on["at"] == "s2"
    assert [s["kind"] for s in ch_on["stops"]] == ["step", "segment_end", "segment_end"]
    # 残影在第一段末帧里（灰色），第二段 unghost 之后没了；law 的下划线 pin 之前就有，clear 之后还在
    one = _end_frame(parts_on / "One.mp4", tmp_path / "one.png")
    two = _end_frame(parts_on / "Two.mp4", tmp_path / "two.png")
    diff = np.abs(one.astype(int) - two.astype(int)).max(axis=2) > 24
    half = one.shape[0] // 2
    ghost_rows = diff[: half - 60].sum(axis=1)  # 残影在 bar 上方
    assert ghost_rows.max() > 20
    assert diff[half:].mean() < 0.001  # 底部的公式和下划线没动（只有压缩噪点）


def test_chapters_list_steps_and_stops():
    from animind_kit.render import chapters

    doc = {"meta": {"id": "x"}, "style": {"segmented": True}, "beats": [
        {"id": "Hook", "pause": {"type": "predict", "prompt": "猜", "hold": 1, "app_item": "q-1"}},
        {"id": "Core"},
    ]}
    info = {"Core": {"steps": [{"time": 2.0, "hold": 1.0, "at": "s2"}, {"time": 5.0, "hold": 1.0, "at": "s3"}]}}
    ch = chapters(doc, [4.0, 8.0], "x.mp4", info)
    assert ch["chapters"][0]["steps"] == []
    assert ch["chapters"][1]["steps"] == [{"time": 6.0, "hold": 1.0, "resume": 7.0, "at": "s2"},
                                          {"time": 9.0, "hold": 1.0, "resume": 10.0, "at": "s3"}]
    assert [(s["time"], s["kind"]) for s in ch["stops"]] == [
        (3.0, "predict"), (4.0, "segment_end"), (6.0, "step"), (9.0, "step"), (12.0, "segment_end")]
    assert ch["stops"][0] == {"time": 3.0, "kind": "predict", "beat": "Hook", "hold": 1.0, "resume": 4.0,
                              "prompt": "猜", "app_item": "q-1"}
