"""时间门禁：CUE LATE 的 SS480 让 inspect / render 非零退出；timing-report.json 的格式过 scene-spec；regress 不受影响。"""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from scene_spec import pauses
from scene_spec.loader import load_file

from animind_kit import timing
from animind_kit.cli import main
from animind_kit.regress import end_frames
from animind_kit.render import inspect

HEAD = """\
spec: "0.1"
meta: {id: gate, title: 门禁, line: worked, author: t, license: CC-BY-4.0}
pedagogy: {insight: 门禁, status: method}
style: {theme: paper, subtitles: none}
objects:
  eq: {type: math, tex: 'a+b=c', in: stage}
"""

# 第二句的书签在 1.75 秒左右，前面一个 3 秒的停留把画面推到 3.3 秒：晚 1.5 秒
LATE = HEAD + """\
beats:
  - id: One
    say:
      - {id: s0, pace: normal, text: "好。"}
      - {id: s1, pace: normal, text: "再看。"}
    do:
      - {at: 0, act: hold, dur: 3}
      - {at: s1, act: show, target: eq}
"""

LATE_ALLOWED = HEAD + """\
beats:
  - id: One
    say:
      - {id: s0, pace: normal, text: "好。"}
      - {id: s1, pace: normal, text: "再看。"}
    do:
      - {at: 0, act: hold, dur: 3}
      - {at: s1, act: show, target: eq, allow_late: true}
"""

CLEAN = HEAD + """\
beats:
  - id: One
    say:
      - {id: s1, pace: normal, text: "看这里，"}
      - {id: s2, pace: normal, text: "这是一句话。"}
      - {id: s3, pace: normal, text: "最后一句！", hold: 0.5}
    do:
      - {at: s1, act: show, target: eq}
      - {at: s2, act: hold, dur: 0.5}
  - id: Two
    say:
      - {id: s1, pace: normal, text: "再看。"}
    do:
      - {at: s1, act: hide, target: eq}
"""


def write(tmp_path, name, text):
    spec = tmp_path / f"{name}.scene.yaml"
    spec.write_text(text, encoding="utf-8")
    return spec


def silent(_msg):
    pass


def test_late_cue_fails_inspect_with_ss480(tmp_path, capsys):
    spec = write(tmp_path, "late", LATE)
    assert main(["inspect", str(spec), "--no-sheets", "--out", str(tmp_path / "ins")]) == 1
    assert "SS480" in capsys.readouterr().out


def test_late_cue_fails_render_with_ss480_but_writes_video(tmp_path, capsys):
    spec = write(tmp_path, "late", LATE)
    out = tmp_path / "late.mp4"
    assert main(["render", str(spec), "--out", str(out), "--quality", "l"]) == 1
    assert "SS480" in capsys.readouterr().out
    assert out.exists()
    assert (tmp_path / "timing-report.json").exists()


def test_allow_late_waives_the_cue(tmp_path):
    spec = write(tmp_path, "allowed", LATE_ALLOWED)
    problems = inspect(spec, None, None, log=silent, report_dir=tmp_path)
    assert not [p for p in problems if "SS480" in p]


def test_timing_report_has_scene_spec_shape_and_passes(tmp_path):
    spec = write(tmp_path, "clean", CLEAN)
    assert main(["inspect", str(spec), "--no-sheets", "--out", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "timing-report.json").read_text(encoding="utf-8"))
    beats = report["beats"]
    assert set(beats) == {"One", "Two"}
    one = beats["One"]
    assert len(one["cues"]) == 3  # 每句一条：和 scene 里 say 的句数一样
    assert {"start", "end"} <= set(one["cues"][0])
    assert one["actions"] and set(one["actions"][0]) == {"at", "want", "start"}
    assert one["actions"][0]["at"] == "s1"
    doc = load_file(spec)
    assert timing.timing_issues(doc, report) == []


def test_estimated_cues_pass_ss487_by_construction(tmp_path):
    spec = write(tmp_path, "clean", CLEAN)
    doc = yaml.safe_load(spec.read_text(encoding="utf-8"))
    beat = doc["beats"][0]
    bt = timing.load(beat, None)
    for i in range(len(bt.sentences) - 1):
        gap = bt.sentences[i + 1].start - bt.sentences[i].end
        s = beat["say"][i]
        assert gap == pytest.approx(pauses.silence_after(s["text"], s.get("hold")), abs=1e-9)
    # 第三句有 hold: 0.5（叹号区间 0.30–0.95 之内），用的就是 0.5
    assert pauses.silence_after(beat["say"][1]["text"], None) == pytest.approx(0.45)


def test_gate_does_not_fire_on_a_clean_scene(tmp_path):
    spec = write(tmp_path, "clean", CLEAN)
    assert main(["render", str(spec), "--out", str(tmp_path / "clean.mp4"), "--quality", "l"]) == 0


def test_regress_frames_are_not_gated(tmp_path):
    # regress 只调 render() 取末帧，不走 CLI 的门禁：有 SS480 的场景照样出截图
    spec = write(tmp_path, "late", LATE)
    frames = end_frames(spec, tmp_path / "reg", silent)
    assert set(frames) == {"One"} and frames["One"].exists()


EXAMPLE = Path(__file__).resolve().parents[2] / "scene-spec" / "examples" / "clip-hook.scene.yaml"


@pytest.mark.skipif(shutil.which("ffmpeg") is None or not EXAMPLE.exists(), reason="需要 ffmpeg 和 scene-spec 示例")
def test_clip_hook_example_passes_both_commands(tmp_path):
    assert main(["inspect", str(EXAMPLE), "--no-sheets", "--out", str(tmp_path / "ins")]) == 0
    assert main(["render", str(EXAMPLE), "--out", str(tmp_path / "hook.mp4"), "--quality", "l"]) == 0


FREEZE = HEAD.replace("style: {theme: paper, subtitles: none}", "style: {theme: paper, subtitles: none, segmented: true}") + """\
beats:
  - id: One
    say:
      - {id: s1, pace: normal, text: "一。"}
      - {id: s2, pace: normal, text: "二。"}
      - {id: s3, pace: normal, text: "三。"}
    do:
      - {at: s1, act: show, target: eq}
      - {at: s2, act: step, args: {hold: 2}}
      - {at: s3, act: hide, target: eq}
"""


def test_freeze_scene_report_stays_on_the_logical_clock(tmp_path):
    # 定格只把视频拉长：报告里的 cues 和 actions 仍是冻结前的旁白时钟，和 CUE LATE 的算法一致
    spec = write(tmp_path, "freeze", FREEZE)
    problems = inspect(spec, None, None, log=silent, report_dir=tmp_path)
    assert [p for p in problems if "SS4" in p] == []
    one = json.loads((tmp_path / "timing-report.json").read_text(encoding="utf-8"))["beats"]["One"]
    doc = yaml.safe_load(spec.read_text(encoding="utf-8"))
    bt = timing.load(doc["beats"][0], None)
    assert [c["start"] for c in one["cues"]] == [round(s.start, 3) for s in bt.sentences]
    at_s3 = next(a for a in one["actions"] if a["at"] == "s3")
    assert at_s3["start"] == pytest.approx(bt.resolve("s3"), abs=1e-3)  # 逻辑时刻，不含定格
    # 同一个动作在成片里晚两秒（定格）：报告不能把这两秒算成迟到
    from animind_kit import segments
    from animind_kit.backends.moxian_backend import MoxianCompiler
    from animind_kit.theme import theme as load_theme
    c = MoxianCompiler(doc, load_theme("paper"), None, log=silent, spec_dir=tmp_path)
    plan = c._plan(c.doc["beats"][0])
    assert segments.to_video(bt.resolve("s3"), plan.tl.freezes) == pytest.approx(bt.resolve("s3") + 2.0, abs=1e-6)


AUDIO_BAD_GAP = HEAD + """\
beats:
  - id: One
    say:
      - {id: s1, pace: normal, text: "一。"}
      - {id: s2, pace: normal, text: "二。"}
    do:
      - {at: s1, act: show, target: eq}
"""


def test_real_audio_gap_violation_fails_inspect_with_ss487(tmp_path, capsys):
    # 真实音频：句间静音 0.1 秒，句号应是 0.45 秒（silence_after）→ SS487，inspect 非零退出
    spec = write(tmp_path, "audio", AUDIO_BAD_GAP)
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "One.timing.json").write_text(json.dumps({
        "cues": [{"start": 0.3, "end": 1.3}, {"start": 1.4, "end": 2.4}], "total": 2.5}), encoding="utf-8")
    assert main(["inspect", str(spec), "--audio", str(audio), "--no-sheets", "--out", str(tmp_path / "ins")]) == 1
    assert "SS487" in capsys.readouterr().out
