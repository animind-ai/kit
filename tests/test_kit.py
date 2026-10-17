"""不渲染的单元测试：主题、布局区域、时间估算、可见期静态分析。"""

from pathlib import Path

from scene_spec.loader import load_file

from animind_kit import layout, theme, timing
from animind_kit.backends.manim_backend import Compiler

EXAMPLE = Path(__file__).resolve().parents[2] / "scene-spec" / "examples" / "s67-p01.scene.yaml"


def test_theme_roles_and_derived_colors():
    t = theme.theme("paper")
    assert t.is_light and t.color("A") == "#2563A8" and t.ink("HL") == "#B7860B"
    dark = theme.theme("chalk")
    assert not dark.is_light and dark.ink("HL") == dark.HL
    assert set(theme.themes()) >= {"paper", "chalk", "jade", "xuan", "blueprint", "riso"}


def test_blend_endpoints():
    assert theme.blend("#FF0000", "#0000FF", 1) == "#FF0000"
    assert theme.blend("#FF0000", "#0000FF", 0) == "#0000FF"


def test_stage_grows_when_regions_unused():
    narrow = layout.regions(True, True, True)["stage"]
    wide = layout.regions(False, False, False)["stage"]
    assert wide.width > narrow.width and wide.height > narrow.height
    assert abs(wide.center[0]) < 1e-9  # 没有侧栏时主讲区居中


def test_bookmark_fractions_follow_character_position():
    beat = {"id": "X", "say": [{"id": "s1", "pace": "normal", "text": "一二{@a}三四五{@b}六"}]}
    t = timing.load(beat, None)
    s = t.sentences[0]
    assert s.marks["a"] < s.marks["b"]
    assert abs((s.marks["a"] - s.start) / (s.end - s.start) - 2 / 6) < 1e-9
    assert t.resolve("s1@b+0.5") == s.marks["b"] + 0.5
    assert t.resolve(0) == s.start


def test_lifetimes_and_region_usage():
    doc = load_file(EXAMPLE)
    c = Compiler(doc, theme.theme("paper"), None, log=lambda *_: None)
    beats = [b["id"] for b in doc["beats"]]
    # 题卡从 Problem 起一直可见；标题只在 Opening
    assert c.lifetimes["card"] == set(range(beats.index("Problem"), len(beats)))
    assert c.lifetimes["title"] == {beats.index("Opening")}
    # 标题活着时没人用 top / side / footer，主讲区可以占满
    assert c.usage_for("title") == (False, False, False)
    # 差分表活着时，题卡钉在顶上、胶囊在底部、后来还有侧栏公式
    assert c.usage_for("table") == (True, True, True)


def test_aligned_marks_override_estimates(tmp_path):
    import json

    beat = {"id": "X", "say": [{"id": "s1", "pace": "normal", "text": "一二{@a}三四五{@b}六"}]}
    (tmp_path / "X.timing.json").write_text(json.dumps({"cues": [{"start": 0.3, "end": 6.3}], "total": 6.3, "marks": {"s1": {"a": 1.0}}}))
    t = timing.load(beat, tmp_path)
    assert t.source == "aligned"
    assert t.sentences[0].marks["a"] == 1.0  # 真实时刻
    assert abs(t.sentences[0].marks["b"] - (0.3 + 5 / 6 * 6.0)) < 1e-9  # 没对齐到的书签仍按比例估


def test_regress_compare_detects_change(tmp_path):
    import skia

    from animind_kit.regress import compare

    def png(path, color):
        surface = skia.Surface(64, 36)
        with surface as canvas:
            canvas.clear(color)
        surface.makeImageSnapshot().save(str(path), skia.kPNG)
        return path

    a = png(tmp_path / "a.png", skia.ColorWHITE)
    b = png(tmp_path / "b.png", skia.ColorWHITE)
    c = png(tmp_path / "c.png", skia.ColorBLACK)
    assert compare(a, b) == (0.0, 0.0)
    mean, fraction = compare(a, c)
    assert mean > 200 and fraction == 1.0


def test_theme_from_json_file(tmp_path):
    from animind_kit.theme import theme

    custom = tmp_path / "house.json"
    custom.write_text('{"house": {"BG": "#FFFFFF", "FG": "#111111", "C_A": "#00AA00", "C_B": "#AA0000", '
                      '"C_C": "#0000AA", "C_HL": "#FFCC00", "C_NO": "#CC0000", "C_DIM": "#999999"}}')
    t = theme(str(custom))
    assert (t.name, t.A, t.is_light) == ("house", "#00AA00", True)
