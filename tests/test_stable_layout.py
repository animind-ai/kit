"""style.layout: stable — regions are solved once per scene, so the stage does not jump between beats."""

import copy

from animind_kit import analysis, theme
from animind_kit.backends.moxian_backend import MoxianCompiler

DOC = {
    "spec": "0.1",
    "meta": {"id": "stable", "title": "stable"},
    "style": {"theme": "paper"},
    "objects": {
        "first": {"type": "text", "text": "first picture", "in": "stage"},
        "second": {"type": "text", "text": "second picture", "in": "stage"},
        "head": {"type": "text", "text": "a heading", "in": "top"},
        "note": {"type": "text", "text": "a note", "in": "side"},
    },
    "beats": [
        {"id": "A", "do": [{"at": 0, "act": "show", "target": "first"}]},
        {"id": "B", "do": [{"at": 0, "act": "hide", "target": "first"}, {"with": "prev", "act": "show", "target": "second"}]},
        {"id": "C", "do": [{"at": 0, "act": "show", "target": "head"}]},
    ],
}


def doc(layout: str | None) -> dict:
    d = copy.deepcopy(DOC)
    if layout:
        d["style"]["layout"] = layout
    return d


def test_adaptive_sizes_each_object_by_its_own_lifetime():
    d = doc(None)
    life = analysis.lifetimes(d)
    assert analysis.usage_for(d, life, "first") == (False, False, False)
    assert analysis.usage_for(d, life, "second") == (True, False, False)  # head appears while second is up


def test_stable_uses_one_answer_for_the_whole_scene():
    d = doc("stable")
    life = analysis.lifetimes(d)
    assert analysis.scene_usage(d, life) == (True, False, False)  # note is never shown: side stays free
    assert analysis.usage_for(d, life, "first") == analysis.usage_for(d, life, "second") == (True, False, False)


def test_stage_region_is_the_same_for_every_stage_object_when_stable():
    t = theme.theme("paper")
    quiet = lambda *_: None  # noqa: E731
    adaptive = MoxianCompiler(doc(None), t, None, quiet)
    assert adaptive.region("first") != adaptive.region("second")  # the jump this option removes
    stable = MoxianCompiler(doc("stable"), t, None, quiet)
    assert stable.region("first") == stable.region("second")
