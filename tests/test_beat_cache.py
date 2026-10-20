"""beat 级增量渲染缓存（墨线后端）：命中的 beat 不调 render_beat；成片和全量重渲逐帧一致；改动会让该失效的 beat 失效。"""

import json
import shutil
import subprocess

import pytest
import skia

from animind_kit import beat_cache
from animind_kit.backends.moxian_backend import MoxianCompiler
from animind_kit.cli import main
from animind_kit.render import render

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")

SIX = """\
spec: "0.1"
meta: {id: six, title: 六段, line: worked, author: t, license: CC-BY-4.0}
pedagogy: {insight: 增量, status: method}
style: {theme: paper, subtitles: none}
objects:
  eq: {type: math, tex: 'a+b=c', in: stage}
  note: {type: text, text: 一句说明, below: eq}
  side: {type: math, tex: 'c=a+b', in: side}
beats:
  - id: b1
    say: [{id: s1, pace: normal, text: "开始。"}]
    do: [{at: s1, act: show, target: eq}]
  - id: b2
    say: [{id: s1, pace: normal, text: "看公式。"}]
    do: [{at: s1, act: circle, target: eq}]
  - id: b3
    say: [{id: s1, pace: normal, text: "加一句说明。"}]
    do: [{at: s1, act: show, target: note}]
  - id: b4
    say: [{id: s1, pace: normal, text: "再看旁边。"}]
    do: [{at: s1, act: show, target: side}]
  - id: b5
    say: [{id: s1, pace: normal, text: "收起说明。"}]
    do: [{at: s1, act: hide, target: note}]
  - id: b6
    say: [{id: s1, pace: normal, text: "最后一句。"}]
    do: [{at: s1, act: underline, target: side}]
"""
IDS = ["b1", "b2", "b3", "b4", "b5", "b6"]


@pytest.fixture
def calls(monkeypatch):
    """记下每次 render_beat 渲染的 beat id。"""
    seen: list[str] = []
    original = MoxianCompiler.render_beat

    def spy(self, beat, clip, plan=None):
        seen.append(beat["id"])
        return original(self, beat, clip, plan)

    monkeypatch.setattr(MoxianCompiler, "render_beat", spy)
    return seen


def write(directory, text, name="six"):
    directory.mkdir(parents=True, exist_ok=True)
    spec = directory / f"{name}.scene.yaml"
    spec.write_text(text, encoding="utf-8")
    return spec


def run(spec, out, **kw):
    return render(spec, out, kw.pop("audio", None), kw.pop("quality", "l"), log=lambda *_: None, backend="moxian", **kw)


def framemd5(video, stream="v"):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-map", f"0:{stream}", "-f", "framemd5", "-"],
                         check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line and not line.startswith("#")]


def same_video(a, b):
    return framemd5(a) == framemd5(b) and framemd5(a, "a") == framemd5(b, "a")


def test_unchanged_scene_is_all_cache_hits(tmp_path, calls):
    spec = write(tmp_path, SIX)
    first = run(spec, tmp_path / "six.mp4")
    assert calls == IDS and first["cache"] == {"cached": [], "rendered": IDS}
    calls.clear()
    second = run(spec, tmp_path / "six.mp4")
    assert calls == [] and second["cache"] == {"cached": IDS, "rendered": []}
    # 命中的 beat 照样推演、照样检查：时长、句子时刻、版式问题、时间报告都和真渲染一样
    for key in ("clip_seconds", "layout_problems", "layout_warnings", "timing_issues", "seconds"):
        assert second[key] == first[key]
    assert second["stats"] == first["stats"]


def test_changing_last_beat_rerenders_only_it_and_matches_full_render(tmp_path, calls):
    spec = write(tmp_path / "inc", SIX)
    run(spec, tmp_path / "inc" / "six.mp4")
    changed = SIX.replace("最后一句。", "这是改过的最后一句。").replace("act: underline, target: side", "act: circle, target: side")
    spec.write_text(changed, encoding="utf-8")
    calls.clear()
    inc = run(spec, tmp_path / "inc" / "six.mp4")
    assert calls == ["b6"] and inc["cache"]["cached"] == IDS[:5]

    full_spec = write(tmp_path / "full", changed)
    calls.clear()
    full = run(full_spec, tmp_path / "full" / "six.mp4", cache=False)
    assert calls == IDS
    assert same_video(tmp_path / "inc" / "six.mp4", tmp_path / "full" / "six.mp4")
    assert inc["clip_seconds"] == full["clip_seconds"] and inc["layout_problems"] == full["layout_problems"]


def test_persisting_change_in_beat_two_invalidates_the_rest(tmp_path, calls):
    spec = write(tmp_path, SIX)
    run(spec, tmp_path / "six.mp4")
    # b2 把公式改成强调色：之后每个 beat 开头的画面都变了
    spec.write_text(SIX.replace("do: [{at: s1, act: circle, target: eq}]",
                                "do: [{at: s1, act: circle, target: eq}, {with: prev, act: set, target: eq, args: {role: HL}}]"),
                    encoding="utf-8")
    calls.clear()
    run(spec, tmp_path / "six.mp4")
    assert calls == IDS[1:]


def test_narration_only_change_in_beat_two_still_invalidates_later_beats(tmp_path, calls):
    # 链式：前面任何一个 beat 改了，后面的一律重渲（编译器里有不在对象树上的状态，宁可多渲）
    spec = write(tmp_path, SIX)
    run(spec, tmp_path / "six.mp4")
    spec.write_text(SIX.replace("看公式。", "仔细看这个公式。"), encoding="utf-8")
    calls.clear()
    run(spec, tmp_path / "six.mp4")
    assert calls == IDS[1:]


@pytest.mark.parametrize("change", [{"theme_name": "chalk"}, {"quality": "m"}, {"subtitles": "burned"}, {"lang": "en"}])
def test_global_settings_invalidate_every_beat(tmp_path, calls, change):
    text = SIX
    if change.get("lang"):  # 英文版要每句都有 en，标题和文字对象在 i18n.en 里
        text = text.replace("beats:\n", "i18n:\n  en:\n    meta: {title: Six}\n    objects:\n      note: {text: a note}\nbeats:\n")
        for zh, en in [("开始。", "Start."), ("看公式。", "Look."), ("加一句说明。", "A note."), ("再看旁边。", "Side."),
                       ("收起说明。", "Hide it."), ("最后一句。", "Last.")]:
            text = text.replace(f'text: "{zh}"', f'text: "{zh}", en: "{en}"')
    spec = write(tmp_path, text)
    run(spec, tmp_path / "six.mp4")
    calls.clear()
    run(spec, tmp_path / "six.mp4", **change)
    assert calls == IDS


def test_audio_change_invalidates_that_beat_and_later(tmp_path, calls):
    spec = write(tmp_path, SIX)
    audio = tmp_path / "audio"
    audio.mkdir()
    for bid in IDS:
        tone(audio / f"{bid}.wav", 1.2)
    run(spec, tmp_path / "six.mp4", audio=audio)
    tone(audio / "b4.wav", 1.6)
    calls.clear()
    run(spec, tmp_path / "six.mp4", audio=audio)
    assert calls == IDS[3:]


def tone(path, seconds):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-ac", "1", "-ar", "22050", str(path)], check=True)


def test_broken_or_missing_cache_entries_are_rerendered(tmp_path, calls):
    spec = write(tmp_path, SIX)
    run(spec, tmp_path / "six.mp4")
    parts = tmp_path / ".build-six.scene-moxian" / "parts"
    (parts / "b2.hash").unlink()  # 没有旁注
    with (parts / "b4.mp4").open("ab") as handle:  # 文件被改过（大小不对）
        handle.write(b"x")
    note = json.loads((parts / "b5.hash").read_text())
    (parts / "b5.hash").write_text(json.dumps({**note, "key": "0" * 64}))  # 键不对
    calls.clear()
    run(spec, tmp_path / "six.mp4")
    assert calls == ["b2", "b4", "b5"]


def test_no_cache_rerenders_everything(tmp_path, calls):
    spec = write(tmp_path, SIX)
    run(spec, tmp_path / "six.mp4")
    calls.clear()
    assert main(["render", str(spec), "--out", str(tmp_path / "six.mp4"), "--no-cache"]) == 0
    assert calls == IDS


def test_only_renders_just_that_beat(tmp_path, calls):
    spec = write(tmp_path / "only", SIX)
    out = tmp_path / "only" / "six.mp4"
    assert main(["render", str(spec), "--out", str(out), "--only", "b3"]) == 0
    assert calls == ["b3"]
    assert (tmp_path / "only" / "b3.mp4").exists()
    assert not out.exists() and not (tmp_path / "only" / "timing-report.json").exists()
    # 和全量渲染里的 b3 逐帧一致（前面的 beat 只推演、没渲染，起始状态照样对）
    full_spec = write(tmp_path / "full", SIX)
    run(full_spec, tmp_path / "full" / "six.mp4")
    assert same_video(tmp_path / "only" / "b3.mp4", tmp_path / "full" / ".build-six.scene-moxian" / "parts" / "b3.mp4")
    # 之后全量渲染：b3 命中缓存，其余渲染
    calls.clear()
    run(spec, out)
    assert calls == ["b1", "b2", "b4", "b5", "b6"] and out.exists()


def test_only_rejects_unknown_beats(tmp_path):
    spec = write(tmp_path, SIX)
    with pytest.raises(SystemExit):
        main(["render", str(spec), "--out", str(tmp_path / "six.mp4"), "--only", "nope"])


CLIP = """\
spec: "0.1"
meta: {id: clipcache, title: 片段, line: worked, author: t, license: CC-BY-4.0}
pedagogy: {insight: 底板, status: method}
style: {theme: paper, subtitles: none}
objects:
  plate: {type: clip, src: frames, kind: frames, fps: 5, end: loop, content_kind: plate, explain: allow}
  eq: {type: math, tex: 'a+b=c', in: stage}
beats:
  - id: One
    say: [{id: s1, pace: normal, text: "看这里。"}]
    do: [{at: 0, act: show, target: plate}, {at: s1, act: show, target: eq}]
  - id: Two
    say: [{id: s1, pace: normal, text: "再看一遍这里。"}]
    do: [{at: s1, act: hide, target: eq}]
  - id: Three
    say: [{id: s1, pace: normal, text: "最后。"}]
    do: [{at: s1, act: show, target: eq}]
"""


def frames_dir(directory, n=7):
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        surface = skia.Surface(64, 36)
        with surface as canvas:
            canvas.clear(skia.ColorSetARGB(255, 30 * i, 255 - 30 * i, 90))
        surface.makeImageSnapshot().save(str(directory / f"{i:03d}.png"), skia.EncodedImageFormat.kPNG)


def test_clip_clock_survives_cached_beats(tmp_path, calls):
    """片段跨 beat 接着播：前面的 beat 命中缓存时，后面 beat 的片段时钟和全量渲染一样。"""
    for d in ("inc", "full"):
        frames_dir(tmp_path / d / "frames")
    spec = write(tmp_path / "inc", CLIP, "clipcache")
    run(spec, tmp_path / "inc" / "c.mp4")
    changed = CLIP.replace("最后。", "最后再说一句。")
    spec.write_text(changed, encoding="utf-8")
    calls.clear()
    run(spec, tmp_path / "inc" / "c.mp4")
    assert calls == ["Three"]
    run(write(tmp_path / "full", changed, "clipcache"), tmp_path / "full" / "c.mp4", cache=False)
    assert same_video(tmp_path / "inc" / "c.mp4", tmp_path / "full" / "c.mp4")


def test_clip_asset_change_invalidates(tmp_path, calls):
    frames_dir(tmp_path / "frames")
    spec = write(tmp_path, CLIP, "clipcache")
    run(spec, tmp_path / "c.mp4")
    frames_dir(tmp_path / "frames", n=8)  # 多一帧
    calls.clear()
    run(spec, tmp_path / "c.mp4")
    assert calls == ["One", "Two", "Three"]


def test_scene_state_sees_geometry_changes():
    from moxian.core import Camera, Group, PathNode
    from moxian.path import rounded_rect

    root = Group(PathNode(path=rounded_rect(1, 1, 0.1)))
    cam = Camera()
    a = beat_cache.scene_state(root, cam, {})
    assert beat_cache.scene_state(root, cam, {}) == a
    root.children[0].shift([0.0, 1e-9])
    assert beat_cache.scene_state(root, cam, {}) != a


# ---------- 播放中才生成的公式、字体 ----------

READOUT = """\
spec: "0.1"
meta: {id: frac, title: 分数, line: worked, author: t, license: CC-BY-4.0}
pedagogy: {insight: 读数, status: method}
style: {theme: paper, subtitles: none}
params: {k: 0}
objects:
  r: {type: readout, value: k, format: fraction, in: stage}
beats:
  - id: b1
    say: [{id: s1, pace: normal, text: "从零开始。"}]
    do: [{at: 0, act: show, target: r}]
  - id: b2
    say: [{id: s1, pace: normal, text: "变成二分之一。"}]
    do: [{at: s1, act: tween, args: {k: 0.5}, dur: 1}]
  - id: b3
    say: [{id: s1, pace: normal, text: "停在这里。"}]
    do: [{at: s1, act: hold, dur: 0.5}]
"""


@pytest.fixture
def fresh_tex(tmp_path, monkeypatch):
    """公式缓存放进临时目录；本进程的公式记录和读数的节点缓存都清空（别的测试编过的公式不算进来）。"""
    from moxian.text import tex

    from animind_kit.mx import readout

    monkeypatch.setattr(tex, "CACHE", tmp_path / "tex-cache")
    monkeypatch.setattr(beat_cache, "_tex_used", {})
    monkeypatch.setattr(readout, "_CACHE", {})
    return tex


def test_tex_compiled_only_during_playback_is_verified(tmp_path, calls, fresh_tex):
    """b2 把读数从整数 0 补间到 1/2：\\frac{1}{2} 到播放时才编译，不在开播时的画面里、键也不变。
    它编出的 svg 变了（同一个 TeX 源），b2 和之后的 beat 要重渲；b1 没用到它，照样命中。"""
    from animind_kit.mx import readout

    spec = write(tmp_path / "s", READOUT, "frac")
    run(spec, tmp_path / "s" / "f.mp4")
    assert calls == ["b1", "b2", "b3"]
    parts = tmp_path / "s" / ".build-frac.scene-moxian" / "parts"
    note = json.loads((parts / "b2.hash").read_text())
    assert r"$\frac{1}{2}$" in note["resources"]["tex"]
    assert r"$\frac{1}{2}$" not in json.loads((parts / "b1.hash").read_text())["resources"]["tex"]
    before = framemd5(parts / "b2.mp4")

    half = fresh_tex._compile(r"$\frac{1}{2}$")
    half.write_text(fresh_tex._compile(r"$\frac{1}{3}$").read_text(encoding="utf-8"), encoding="utf-8")
    readout._CACHE.clear()  # 像新进程一样：读数不复用上一次建好的节点
    calls.clear()
    second = run(spec, tmp_path / "s" / "f.mp4")
    assert calls == ["b2", "b3"] and second["cache"]["cached"] == ["b1"]
    assert framemd5(parts / "b2.mp4") != before  # 重渲出来的确实是新的字形


def test_tex_toolchain_is_in_the_environment(monkeypatch):
    base = beat_cache.environment()
    real = beat_cache._first_line
    monkeypatch.setattr(beat_cache, "_first_line", lambda cmd: "pdfTeX 0.0 (changed)" if cmd[0] == "latex" else real(cmd))
    beat_cache.environment.cache_clear()
    try:
        assert beat_cache.environment() != base
    finally:
        beat_cache.environment.cache_clear()


def test_font_change_invalidates(tmp_path, calls, monkeypatch):
    spec = write(tmp_path, SIX)
    run(spec, tmp_path / "six.mp4")
    # 同名字体换了文件（字形不同）：每个字体的内容哈希都变了
    monkeypatch.setattr(beat_cache, "_face_memo", {k: "0" * 64 for k in beat_cache._face_memo})
    calls.clear()
    run(spec, tmp_path / "six.mp4")
    assert calls == IDS


def test_unrecordable_resources_disable_caching(tmp_path, calls, monkeypatch):
    spec = write(tmp_path, SIX)
    monkeypatch.setattr(beat_cache, "resources", lambda: None)
    run(spec, tmp_path / "six.mp4")
    assert not list((tmp_path / ".build-six.scene-moxian" / "parts").glob("*.hash"))
    calls.clear()
    run(spec, tmp_path / "six.mp4")
    assert calls == IDS


# ---------- mp4 内容、旁注结构 ----------


def test_same_length_overwrite_of_mp4_is_a_miss(tmp_path, calls):
    import os

    spec = write(tmp_path, SIX)
    run(spec, tmp_path / "six.mp4")
    clip = tmp_path / ".build-six.scene-moxian" / "parts" / "b3.mp4"
    st = clip.stat()
    data = bytearray(clip.read_bytes())
    mid = len(data) // 2
    data[mid:mid + 64] = bytes(b ^ 0xFF for b in data[mid:mid + 64])
    clip.write_bytes(bytes(data))
    os.utime(clip, ns=(st.st_atime_ns, st.st_mtime_ns))  # 大小、mtime 都和原来一样
    assert clip.stat().st_size == st.st_size
    calls.clear()
    run(spec, tmp_path / "six.mp4")
    assert calls == ["b3"]


GOOD = {"format": beat_cache.FORMAT, "key": "a" * 64, "bytes": 4, "sha256": "", "resources": {"tex": {}, "fonts": []}}


@pytest.mark.parametrize("note", [
    "[]", "null", "1", '"x"', "{}", "[1, 2]",
    json.dumps({**GOOD, "key": 123}),
    json.dumps({**GOOD, "bytes": "4"}),
    json.dumps({**GOOD, "bytes": True}),
    json.dumps({**GOOD, "format": None}),
    json.dumps({**GOOD, "sha256": None}),
    json.dumps({**GOOD, "resources": []}),
    json.dumps({**GOOD, "resources": None}),
    json.dumps({**GOOD, "resources": {"tex": [], "fonts": []}}),
    json.dumps({**GOOD, "resources": {"tex": {"x": 1}, "fonts": []}}),
    json.dumps({**GOOD, "resources": {"tex": {}, "fonts": [["a", "normal"]]}}),
    json.dumps({**GOOD, "resources": {"tex": {}, "fonts": "abc"}}),
    json.dumps({k: v for k, v in GOOD.items() if k != "resources"}),
])
def test_malformed_sidecar_is_a_miss_not_a_crash(tmp_path, note):
    import hashlib

    clip = tmp_path / "b.mp4"
    clip.write_bytes(b"abcd")
    good = {**GOOD, "sha256": hashlib.sha256(b"abcd").hexdigest()}
    beat_cache.sidecar(clip).write_text(json.dumps(good))
    assert beat_cache.hit(clip, "a" * 64)  # 结构完好的旁注是命中
    beat_cache.sidecar(clip).write_text(note.replace('"sha256": ""', f'"sha256": "{good["sha256"]}"'))
    assert beat_cache.hit(clip, "a" * 64) is False


def test_malformed_sidecars_in_a_render_are_rerendered(tmp_path, calls):
    spec = write(tmp_path, SIX)
    run(spec, tmp_path / "six.mp4")
    parts = tmp_path / ".build-six.scene-moxian" / "parts"
    (parts / "b2.hash").write_text("[]")
    (parts / "b3.hash").write_text("null")
    note = json.loads((parts / "b5.hash").read_text())
    (parts / "b5.hash").write_text(json.dumps({**note, "resources": {"tex": [], "fonts": {}}}))
    calls.clear()
    run(spec, tmp_path / "six.mp4")
    assert calls == ["b2", "b3", "b5"]


def test_no_cache_never_reads_old_sidecars(tmp_path, calls, monkeypatch):
    spec = write(tmp_path, SIX)
    run(spec, tmp_path / "six.mp4")
    for note in (tmp_path / ".build-six.scene-moxian" / "parts").glob("*.hash"):
        note.write_text("[]")

    def boom(*_):
        raise AssertionError("cache=False 不该读旁注")

    monkeypatch.setattr(beat_cache, "hit", boom)
    calls.clear()
    assert main(["render", str(spec), "--out", str(tmp_path / "six.mp4"), "--no-cache"]) == 0
    assert calls == IDS
    calls.clear()
    assert main(["render", str(spec), "--out", str(tmp_path / "six.mp4"), "--no-cache", "--only", "b4"]) == 0
    assert calls == ["b4"]
