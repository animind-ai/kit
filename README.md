# animind-kit

[scene-spec](../scene-spec) 的运行时。读一份场景描述文件，加上旁白音频，渲染出成片。渲染引擎默认是 [墨线 moxian](../moxian)，也可以切回 Manim（`--backend manim`）。

0.1 包含：

- **主题** `theme.py`：palette 的语义角色 → 颜色，以及派生的卡片底色、mark 垫色、浅底上的结论墨色。色值只来自 [animind-ai/palette](https://github.com/animind-ai/palette)。
- **文字** `text.py`：中文用 Noto Sans CJK SC（思源黑体，开源），公式走 xelatex + ctex；把 `[片段]{#id .角色}` 标注渲染成可单独指向、单独上色的段。
- **布局** `layout.py`：top / stage / side / footer 四个区域，`in`、`below`、`over`、`pin` 等约束；没人用的区域让给主讲区；渲染后做出框和重叠检查。
- **组件** `widgets/`：题卡、步骤胶囊、想一想框、总结卡、差分表（含 derive / extend / triangle 动作）。
- **墨线后端** `backends/moxian_backend.py` + `mx/`：对象树跨 beat 常驻，每个 beat 一条时间线；文字、组件、布局都在 `mx/` 里用墨线实现。动作按旁白句子和书签的时刻播放，画面跟不上旁白时打印 `CUE LATE`。
- **Manim 后端** `backends/manim_backend.py` + `widgets/`：保留用于对照，每个 beat 一个 Scene。
- **渲染** `render.py`：逐 beat 渲染，和旁白合成，拼成一支成片。
- 两个后端共用的部分：`analysis.py`（可见期静态分析、区域占用）、`regions.py`（画面分区、出框和重叠检查）、`timing.py`、`theme.py`。

还没有：自动布局求解（现在只按约束摆放 + 检查）、三维、camera 动作、area_model 等洞察系列的原语、Whisper 词级时间戳（书签时刻按字符位置估算）。

## 用法

```bash
uv sync
uv run animind-kit render ../scene-spec/examples/s67-p01.scene.yaml \
  --out build/out/s67-p01.mp4 \
  --audio /path/to/p01/audio \
  --quality m            # l=480p15 m=720p30 h=1080p30
  # --backend manim      # 切回 Manim 对照
```

墨线后端渲染整支 §6.7 第 01 支（4 分 40 秒）：480p 约 30 秒，720p 约 1 分钟。

`--audio` 目录里要有每个 beat 的 `<beat>.wav` 和 `<beat>.timing.json`（voice 流水线的输出格式；没有音频的 beat 按每个汉字 0.22 秒估算时长）。`--quality` 取 `l` / `m` / `h`。

渲染结束会打印版式问题的条数；中间产物在 `--out` 旁边的 `.build-<名字>/` 里，每个 beat 一段 mp4，便于抽帧检查。

依赖：moxian（同目录 `../moxian`）、ffmpeg、latex + dvisvgm、Noto Sans CJK SC 字体；Manim 后端另需 Manim Community 0.21 和 xelatex（含 ctex）。

## 开发

```bash
uv run pytest
```

## 作者与许可

作者：Boxi Yu（[@BoxiYu](https://github.com/BoxiYu)）

代码以 [MIT 许可](LICENSE)发布。`src/animind_kit/data/palette.json` 来自 animind-ai/palette（MIT）。
