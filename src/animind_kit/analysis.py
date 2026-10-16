"""对 scene-spec 文档的静态分析，不依赖任何渲染后端。

- 每个对象在哪些 beat 可见（按 show / hide / transform-to 推算）；
- 对象活着的时候，top / side / footer 有没有被别人占用（决定主讲区能占多大）。
"""

from __future__ import annotations

from scene_spec import selectors

LAYOUT_REFS = ("below", "above", "left_of", "right_of", "over", "within")


def region_static(doc: dict, obj_id: str, depth: int = 0) -> str:
    d = doc["objects"][obj_id]
    if d.get("pin") == "top":
        return "top"
    if "in" in d:
        return d["in"]
    for key in LAYOUT_REFS:
        if key in d and depth < 10:
            return region_static(doc, selectors.parse(d[key]).obj, depth + 1)
    return "stage"


def lifetimes(doc: dict) -> dict[str, set[int]]:
    """对象 → 可见的 beat 序号集合。"""
    visible: set[str] = set()
    life: dict[str, set[int]] = {k: set() for k in doc.get("objects", {})}
    for n, beat in enumerate(doc["beats"]):
        for action in beat.get("do", []):
            act = action["act"]
            if act == "show":
                visible.add(selectors.parse(action["target"]).obj)
            elif act == "hide":
                visible.discard(selectors.parse(action["target"]).obj)
            elif act == "transform":
                visible.add(selectors.parse(action["to"]).obj)
            for k in visible:
                life[k].add(n)
        for k in visible:
            life[k].add(n)
    return life


def usage_for(doc: dict, life: dict[str, set[int]], obj_id: str) -> tuple[bool, bool, bool]:
    """对象活着的这段时间里，top / side / footer 有没有被别的对象用到。"""
    mine = life.get(obj_id) or set()
    used = {"top": False, "side": False, "footer": False}
    for other, beats in life.items():
        if other == obj_id or not (beats & mine):
            continue
        region = region_static(doc, other)
        if region in used:
            used[region] = True
    return used["top"], used["side"], used["footer"]
