"""数例表 seq_table、折线图 line_chart、胶囊 reveal 和留白时的暂停提示条（2026-10-21 学生访谈后加的）。"""

import yaml
from scene_spec import check

from animind_kit import theme
from animind_kit.backends.moxian_backend import MoxianCompiler
from animind_kit.mx import widgets
from animind_kit.mx.chart import LineChart
from animind_kit.mx.table import SeqTable

T = theme.theme("paper")


def op(node) -> float:
    """节点的透明度：没设过样式的组是 1。"""
    return node.style.opacity if node.style is not None else 1.0

DOC = """\
spec: "0.1"
meta: {id: tiny-table, title: 小样, line: contest, author: t, license: CC-BY-4.0}
pedagogy: {insight: 小样, status: method}
style: {theme: paper, segmented: false}
objects:
  plan: {widget: chips, in: footer, reveal: true, items: ["① 试几步", "② 跟踪商", "③ 相遇"]}
  table:
    widget: seq_table
    in: stage
    rows:
      - {label: '$a_n$', values: [24, 27, 30, 35]}
      - {label: '$p_n$', values: [3, 3, 5, 7], role: HL}
      - {label: '$b_n$', values: [8, 9, 6, 5], role: A}
  chart:
    widget: line_chart
    below: table
    x: [24, 27, 30, 35]
    series:
      - {name: 'b_n', values: [8, 9, 6, 5], role: A}
      - {name: 'p_n', values: [3, 3, 5, 7], role: HL}
    yticks: [3, 5, 7, 9]
beats:
  - id: Ask
    pause: {type: think, prompt: 商会怎么走？, hold: 3}
    say:
      - {id: s1, pace: ask, text: "商会怎么走？"}
    do:
      - {at: s1, act: show, target: plan}
      - {with: prev, act: focus, target: "plan[0]"}
  - id: Try
    say:
      - {id: s1, pace: normal, text: "{@a}先试几步，{@b}再看商。"}
    do:
      - {at: s1@a, act: show, target: table}
      - {after: prev, act: show, target: "table[0:2]"}
      - {at: s1@b, act: show, target: "table[2:4]"}
      - {after: prev, act: shade, target: table, args: {row: 2, col: 3}}
      - {after: prev, act: focus, target: "plan[1]"}
      - {after: prev, act: show, target: chart}
      - {after: prev, act: show, target: "chart.series[0]"}
      - {after: prev, act: show, target: "chart.series[1]"}
      - {after: prev, act: ring, target: chart, args: {series: 0, index: 3}}
      - {after: prev, act: reveal, target: plan}
"""


def doc():
    d = yaml.safe_load(DOC)
    report = check(d)
    assert not report.errors, report.errors
    return d


def test_seq_table_exposes_columns_rows_and_shades():
    t = SeqTable("t", {"widget": "seq_table", "rows": [{"label": "$a$", "values": [1, 2, 3]}, {"label": "$b$", "values": [4, 5, 6], "role": "HL"}]}, T, {})
    assert len(t.built.items) == 3 and len(t.built.parts["row"]) == 2 and len(t.built.parts["head"]) == 2
    assert all(op(col) == 0.0 for col in t.built.items)  # known 默认 0：只露行头
    assert len(t.action("shade", {"row": 1, "col": 2})) == 1
    known = SeqTable("k", {"widget": "seq_table", "known": 2, "rows": [{"label": "$a$", "values": [1, 2, 3]}]}, T, {})
    assert [op(col) for col in known.built.items] == [1.0, 1.0, 0.0]


def test_line_chart_series_hidden_until_shown():
    c = LineChart("c", {"widget": "line_chart", "x": [1, 2, 3], "series": [{"name": "b", "values": [1, 3, 2]}, {"values": [2, 2, 2], "role": "HL"}], "yticks": [1, 2]}, T, {})
    assert len(c.built.parts["series"]) == 2 and len(c.built.parts["axes"]) == 1
    assert all(op(s) == 0.0 for s in c.built.parts["series"])
    assert len(c.action("ring", {"series": 0, "index": 2})) == 1
    assert c.points[0][1][1] > c.points[0][0][1]  # 值大的点更高


def test_chips_reveal_follows_focus():
    chips = widgets.Chips("p", {"widget": "chips", "items": ["一", "二", "三"], "reveal": True}, T, {})
    assert all(op(chip) == 0.0 for chip in chips.built.items)
    anims = chips.focus(1)
    assert len(anims) == 2 + 6  # 露出两个 + 三个胶囊各两条换色
    assert chips.shown == [True, True, False]
    assert len(chips.action("reveal", {})) == 1 and chips.shown == [True, True, True]
    plain = widgets.Chips("q", {"widget": "chips", "items": ["一", "二"]}, T, {})
    assert all(op(chip) == 1.0 for chip in plain.built.items) and len(plain.focus(0)) == 4


def test_pause_bar_covers_the_hold_and_hides_footer():
    d = doc()
    c = MoxianCompiler(d, T, None, log=lambda *_: None)
    plan = c._plan(d["beats"][0])
    assert len(c.pause_bars) == 1
    bar = c.pause_bars[0]
    assert bar.style.opacity == 0.0  # 还没播
    plan.tl.seek(plan.until - 2.0)  # 留白中段：提示条在、胶囊让开
    assert bar.style.opacity > 0.9 and c.objects["plan"].node.style.opacity < 0.1
    plan.tl.seek(plan.until)
    assert bar.style.opacity < 0.05 and c.objects["plan"].node.style.opacity > 0.95  # 结束时换回来
    # 后面的 beat 照常编（表格列、折线、圈、reveal 都能走到）
    plan2 = c._plan(d["beats"][1])
    plan2.tl.seek(plan2.until)
    assert all(op(col) == 1.0 for col in c.objects["table"].items)
    assert all(op(chip) == 1.0 for chip in c.objects["plan"].items)
