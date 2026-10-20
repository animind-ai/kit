"""对 scene-spec 文档的静态分析，不依赖任何渲染后端。

- 每个对象在哪些 beat 可见（按 show / hide / transform-to 推算）；
- 对象活着的时候，top / side / footer 有没有被别人占用（决定主讲区能占多大）；
  style.layout: stable 时改成整个场景只算一次。
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
    """对象 → 占着画面的 beat 序号集合。

    一个对象算占着第 n 个 beat，当且仅当它在这个 beat 里被显示过，或者到这个 beat 结束时仍然可见。
    一进 beat 就被隐藏的对象（上一段留下、这一段开头就收掉）不算，免得白占区域。
    """
    visible: set[str] = set()
    life: dict[str, set[int]] = {k: set() for k in doc.get("objects", {})}
    for n, beat in enumerate(doc["beats"]):
        shown: set[str] = set()
        for action in beat.get("do", []):
            act = action["act"]
            if act == "show":
                obj = selectors.parse(action["target"]).obj
                visible.add(obj)
                shown.add(obj)
            elif act == "hide":
                sel = selectors.parse(action["target"])
                if sel.span is None and sel.part is None and sel.index is None:
                    visible.discard(sel.obj)
            elif act == "transform":
                obj = selectors.parse(action["to"]).obj
                visible.add(obj)
                shown.add(obj)
        for k in visible | shown:
            life[k].add(n)
    return life


def scene_usage(doc: dict, life: dict[str, set[int]]) -> tuple[bool, bool, bool]:
    """整个场景里 top / side / footer 有没有被用到过（任何对象、任何 beat）。"""
    used = {"top": False, "side": False, "footer": False}
    for obj_id, beats in life.items():
        region = region_static(doc, obj_id)
        if beats and region in used:
            used[region] = True
    return used["top"], used["side"], used["footer"]


def usage_for(doc: dict, life: dict[str, set[int]], obj_id: str) -> tuple[bool, bool, bool]:
    """对象活着的这段时间里，top / side / footer 有没有被别的对象用到。

    style.layout: stable 时不看可见期，整场只算一次（scene_usage）：场景里用到过的区域一直预留，
    所以主讲区在 beat 之间大小、位置都不变，换组件或顶上出现文字时不会跳、不会缩。
    """
    if (doc.get("style") or {}).get("layout") == "stable":
        return scene_usage(doc, life)
    mine = life.get(obj_id) or set()
    used = {"top": False, "side": False, "footer": False}
    for other, beats in life.items():
        if other == obj_id or not (beats & mine):
            continue
        region = region_static(doc, other)
        if region in used:
            used[region] = True
    return used["top"], used["side"], used["footer"]
