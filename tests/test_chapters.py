"""章节文件：render 在成片旁边写 <名>.chapters.json，时间按各段实际时长累加。"""

import json
import shutil

import pytest

from animind_kit.render import chapters, chapters_path, probe_seconds, render

TINY = """\
spec: "0.1"
meta: {id: tiny, title: 小样, line: worked, author: t, license: CC-BY-4.0}
pedagogy: {insight: 小样, status: method}
style: {theme: paper, segmented: false}
objects:
  eq: {type: math, tex: 'a+b', in: stage}
beats:
  - id: One
    say:
      - {id: s1, pace: normal, text: "看这里。"}
    do:
      - {at: s1, act: show, target: eq}
  - id: Two
    pause: {type: think, prompt: 想一想, hold: 1.5, app_item: q1}
    say:
      - {id: s1, pace: normal, text: "看这里。"}
    do:
      - {at: s1, act: focus, target: eq}
  - id: Three
    say:
      - {id: s1, pace: normal, text: "看这里。"}
    do:
      - {at: s1, act: unfocus, target: eq}
"""


def test_chapters_accumulate_clip_times():
    doc = {
        "meta": {"id": "x", "title": "X"},
        "style": {"segmented": True},
        "beats": [
            {"id": "Hook", "shows": "misconception"},
            {"id": "Predict", "pause": {"type": "predict", "prompt": "猜一猜", "hold": 3}},
            {"id": "Landing", "shows": ["landing"], "pause": {"type": "segment"}},
        ],
    }
    ch = chapters(doc, [2.5, 6.0, 4.0], "x.mp4")
    assert ch["duration"] == 12.5 and ch["segmented"] is True and ch["video"] == "x.mp4"
    hook, predict, landing = ch["chapters"]
    assert (hook["start"], hook["end"], hook["title"], hook["shows"]) == (0.0, 2.5, "Hook", ["misconception"])
    assert (predict["start"], predict["end"]) == (2.5, 8.5)
    assert predict["pause"] == {"type": "predict", "prompt": "猜一猜", "time": 5.5, "hold": 3.0}
    assert "pause" not in hook and all(c["segment_end"] for c in ch["chapters"])
    assert landing["shows"] == ["landing"] and landing["pause"]["time"] == 10.5  # 默认 hold 2 秒


def test_chapters_count_must_match():
    with pytest.raises(ValueError):
        chapters({"beats": [{"id": "a"}]}, [1.0, 2.0])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_writes_chapters_next_to_video(tmp_path):
    spec = tmp_path / "tiny.scene.yaml"
    spec.write_text(TINY, encoding="utf-8")
    out = tmp_path / "out" / "tiny.mp4"
    summary = render(spec, out, None, "l", log=lambda *_: None, backend="moxian")
    path = chapters_path(out)
    assert path == tmp_path / "out" / "tiny.chapters.json" and summary["chapters"] == str(path)
    ch = json.loads(path.read_text(encoding="utf-8"))
    assert [c["id"] for c in ch["chapters"]] == ["One", "Two", "Three"]
    parts = tmp_path / "out" / ".build-tiny.scene-moxian" / "parts"
    t = 0.0
    for c in ch["chapters"]:
        seconds = probe_seconds(parts / f"{c['id']}.mp4")
        assert c["start"] == pytest.approx(t, abs=1e-3) and c["end"] == pytest.approx(t + seconds, abs=1e-3)
        t += seconds
    assert ch["duration"] == pytest.approx(probe_seconds(out), abs=0.1)
    two = ch["chapters"][1]
    assert two["pause"] == {"type": "think", "prompt": "想一想", "time": pytest.approx(two["end"] - 1.5, abs=1e-3),
                            "hold": 1.5, "app_item": "q1"}
    assert not any(c["segment_end"] for c in ch["chapters"])
