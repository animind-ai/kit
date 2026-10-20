"""对 scene-spec 文档的静态分析，不依赖任何渲染后端。

- 每个对象在哪些 beat 可见（按 show / hide / transform 推算；group 的成员算在整组头上）；
- 对象活着的时候，top / side / footer 有没有被别人占用（决定主讲区能占多大）。
"""

from __future__ import annotations

from scene_spec import selectors

LAYOUT_REFS = ("below", "above", "left_of", "right_of", "over", "within")


def group_of(doc: dict) -> dict[str, tuple[str, int]]:
    """group 的成员 → (直接所在的 group, 在 children 里的下标)。"""
    out: dict[str, tuple[str, int]] = {}
    for obj_id, d in doc.get("objects", {}).items():
        if d.get("type") == "group" and "widget" not in d:
            for i, child in enumerate(d.get("children") or []):
                out.setdefault(child, (obj_id, i))
    return out


def top_of(doc: dict, obj_id: str) -> str:
    """对象所在的最外层 group（不在 group 里就是它自己）：成员跟着整组出现、占区域。"""
    owners = group_of(doc)
    seen = {obj_id}
    while obj_id in owners and owners[obj_id][0] not in seen:
        obj_id = owners[obj_id][0]
        seen.add(obj_id)
    return obj_id


def region_static(doc: dict, obj_id: str, depth: int = 0) -> str:
    d = doc["objects"][obj_id]
    if d.get("pin") == "top":
        return "top"
    if "in" in d:
        return d["in"]
    top = top_of(doc, obj_id)
    if top != obj_id and depth < 10:  # group 的成员：在整组所在的区域
        return region_static(doc, top, depth + 1)
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
                obj = top_of(doc, selectors.parse(action["target"]).obj)
                visible.add(obj)
                shown.add(obj)
            elif act == "hide":
                sel = selectors.parse(action["target"])
                if sel.span is None and sel.part is None and sel.index is None and top_of(doc, sel.obj) == sel.obj:
                    visible.discard(sel.obj)
            elif act == "transform":
                obj = top_of(doc, selectors.parse(action["to"]).obj)
                visible.add(obj)
                shown.add(obj)
                gone = selectors.transform_replaced(action)  # replace：源被替换掉
                if gone is not None and top_of(doc, gone) == gone:
                    visible.discard(gone)
        for k in visible | shown:
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
