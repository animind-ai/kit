"""v2 风格提示（visual_spec §10）：一种强调色、一个镜头一个重点、其余压暗。只提示，不算版式问题。"""

from animind_kit import lint

S = lint.Style(accents={"#2f7a56": "A", "#966524": "B", "#d9a441": "HL", "#74501e": "HL", "#b5452f": "NO"},
               quiet={"#1a2822", "#a39d90", "#6b665c"})


def thing(obj, color, alpha=1.0, act=None, kind="text"):
    return lint.Thing(kind, obj, obj, (0.0, 0.0, 1.0, 1.0), 0, color=color, alpha=alpha, act=act)


def kinds(things):
    return sorted(p.kind for p in lint.style_hints(things, S))


def test_one_accent_and_ink_is_clean():
    assert kinds([thing("tri", "#d9a441"), thing("law", "#74501e"), thing("line", "#1a2822"), thing("axes", "#a39d90")]) == []


def test_two_accents_warn():
    found = lint.style_hints([thing("line", "#2f7a56"), thing("k", "#966524")], S)
    assert [p.kind for p in found] == ["accents"] and found[0].text == "A、B"


def test_dimmed_accent_does_not_count():
    assert kinds([thing("tri", "#d9a441"), thing("line", "#2f7a56", alpha=0.45)]) == []


def test_two_emphasised_objects_warn():
    things = [thing("a", "#1a2822"), thing("b", "#1a2822"),
              thing("a", "#d9a441", act="mark", kind="shape"), thing("b", "#d9a441", act="focus", kind="shape")]
    assert kinds(things) == ["emphasis"]


def test_undimmed_others_warn_only_when_loud():
    focus = thing("tri", "#d9a441", act="focus", kind="shape")
    assert kinds([focus, thing("k", "#1a2822")]) == []  # 墨色不算没压暗
    assert kinds([focus, thing("k", "#966524", alpha=0.45)]) == []  # 压暗了
    assert "undimmed" in kinds([focus, thing("k", "#2f7a56")])


def test_scan_hex():
    from animind_kit.mx.scan import _hex
    assert _hex("#2F7A56") == "#2f7a56" and _hex("#2F7A56CC") == "#2f7a56" and _hex(None) == ""
