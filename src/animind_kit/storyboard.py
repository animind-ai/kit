"""分镜图：每张 6 格（2 列 × 3 行），按时间顺序；每格标题是「#序号 beat t=… 来源」，
有问题的地方用红框圈出并标上问题编号（和 inspect 打印的问题列表同号）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import skia
from moxian.text.font import DEFAULT_FONT, typeface

from .lint import FRAME, Box, Problem

CELL_W, CELL_H = 960, 540
CAPTION = 30
GAP = 8
COLS, ROWS = 2, 3
RED = skia.Color4f(0.9, 0.12, 0.12, 1.0)
AMBER = skia.Color4f(0.95, 0.55, 0.0, 1.0)


@dataclass
class Cell:
    image: skia.Image
    caption: str
    marks: list[tuple[int, Problem]]


def _to_px(box: Box) -> skia.Rect:
    k = CELL_H / (FRAME[3] - FRAME[1])
    x0, y0, x1, y1 = box
    return skia.Rect.MakeLTRB((x0 - FRAME[0]) * k, (FRAME[3] - y1) * k, (x1 - FRAME[0]) * k, (FRAME[3] - y0) * k)


def _font(size: float) -> skia.Font:
    return skia.Font(typeface(DEFAULT_FONT), size)


def _annotate(canvas: skia.Canvas, marks: list[tuple[int, Problem]]) -> None:
    box_paint = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=2.5, Color4f=RED)
    other_paint = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=1.5, Color4f=AMBER,
                             PathEffect=skia.DashPathEffect.Make([6.0, 4.0], 0.0))
    tag_paint = skia.Paint(AntiAlias=True, Color4f=RED)
    white = skia.Paint(AntiAlias=True, Color4f=skia.Color4f(1, 1, 1, 1))
    font = _font(16)
    for number, p in marks:
        if p.other_box is not None:
            other = _to_px(p.other_box)
            if other.width() * other.height() < CELL_W * CELL_H / 8:  # 整张图那么大的就不画了，只会糊住画面
                canvas.drawRect(other.makeOutset(2, 2), other_paint)
        r = _to_px(p.box).makeOutset(3, 3)
        canvas.drawRect(r, box_paint)
        label = str(number)
        w = font.measureText(label) + 8
        x = min(max(r.left(), 0.0), CELL_W - w)
        y = r.top() - 20 if r.top() >= 20 else min(r.bottom(), CELL_H - 20)
        canvas.drawRect(skia.Rect.MakeXYWH(x, y, w, 20), tag_paint)
        canvas.drawString(label, x + 4, y + 15, font, white)


def write_sheets(cells: list[Cell], out: Path, stem: str) -> list[Path]:
    """cells 按时间顺序；写成 stem.storyboard.png、stem.storyboard-2.png …"""
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    per = COLS * ROWS
    caption_font = _font(18)
    for n, start in enumerate(range(0, len(cells), per)):
        chunk = cells[start:start + per]
        rows = int(np.ceil(len(chunk) / COLS))
        width = COLS * CELL_W + (COLS - 1) * GAP
        height = rows * (CELL_H + CAPTION) + (rows - 1) * GAP
        surface = skia.Surface(width, height)
        with surface as canvas:
            canvas.clear(skia.Color4f(0.13, 0.13, 0.13, 1.0))
            for i, cell in enumerate(chunk):
                x = (i % COLS) * (CELL_W + GAP)
                y = (i // COLS) * (CELL_H + CAPTION + GAP)
                color = RED if cell.marks else skia.Color4f(0.9, 0.9, 0.9, 1.0)
                canvas.drawString(cell.caption, x + 6, y + 21, caption_font, skia.Paint(AntiAlias=True, Color4f=color))
                canvas.save()
                canvas.translate(x, y + CAPTION)
                canvas.clipRect(skia.Rect.MakeWH(CELL_W, CELL_H))
                canvas.drawImage(cell.image, 0, 0)
                _annotate(canvas, cell.marks)
                canvas.restore()
        path = out / (f"{stem}.storyboard.png" if n == 0 else f"{stem}.storyboard-{n + 1}.png")
        surface.makeImageSnapshot().save(str(path), skia.kPNG)
        paths.append(path)
    return paths
