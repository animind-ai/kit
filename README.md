# animind-kit

[scene-spec](../scene-spec) 的运行时。读一份场景描述文件，加上旁白音频，渲染出成片。渲染引擎默认是 [墨线 moxian](../moxian)，也可以切回 Manim（`--backend manim`）。

0.1 包含：

- **主题** `theme.py`：palette 的语义角色 → 颜色，以及派生的卡片底色、mark 垫色、浅底上的结论墨色。色值只来自 [animind-ai/palette](https://github.com/animind-ai/palette)。
- **文字** `text.py`：中文用 Noto Sans CJK SC（思源黑体，开源），公式走 xelatex + ctex；把 `[片段]{#id .角色}` 标注渲染成可单独指向、单独上色的段。
- **布局** `layout.py`：top / stage / side / footer 四个区域，`in`、`below`、`over`、`pin` 等约束；没人用的区域让给主讲区；渲染后做出框和重叠检查。
- **组件**：题卡（可回填答案）、步骤胶囊、想一想框、总结卡、差分表（derive / extend / triangle）、对齐等式组 `equations`、数列格子 `seq_cells`（fill）。墨线版在 `mx/`；Manim 版 `widgets/` 只有前五个，所以只能渲染 p01 这样的内容。
- **墨线后端** `backends/moxian_backend.py` + `mx/`：对象树跨 beat 常驻，每个 beat 一条时间线；文字、组件、布局都在 `mx/` 里用墨线实现。动作按旁白句子和书签的时刻播放，画面跟不上旁白时打印 `CUE LATE`。
- **Manim 后端** `backends/manim_backend.py` + `widgets/`：保留用于对照，每个 beat 一个 Scene。
- **渲染** `render.py`：逐 beat 渲染，和旁白合成，拼成一支成片。
- 两个后端共用的部分：`analysis.py`（可见期静态分析、区域占用）、`regions.py`（画面分区、出框和重叠检查）、`timing.py`、`theme.py`。

分段播放（`step` 子步定格、`ghost` 残影、`pin` 常驻，见 scene-spec 设计稿 §7.6）、字幕导出的定格顺延只有墨线后端实现；Manim 后端只导出字幕和章节。

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

墨线后端渲染整支 §6.7 第 01 支（4 分 40 秒）：480p 约 4 秒，720p 约 9 秒；Manim 后端 480p 约 26 秒，720p 约 24 秒。

每做一集新内容，用两个后端各跑一遍对照，直到墨线跑过十集以上再退役 Manim 后端：

```bash
uv run animind-kit compare ../scene-spec/examples/s67-p01.scene.yaml --audio /path/to/p01/audio --out build/compare
```

产物是两支视频、每个 beat 末帧的左右对照图（左 Manim、右墨线）和一份 `report.json`（渲染耗时、版式问题）。用到了 Manim 后端没有的组件时（例如 p02），改为和原来手写的成片逐段比对末帧。

截图回归（CI 里也跑）：不带音频渲染 `../scene-spec/examples/` 下所有示例，取每段末帧和 `tests/baseline/<平台>/` 比对：

```bash
uv run animind-kit regress            # 有变化时退出码 1
uv run animind-kit regress --update   # 确认画面变化是有意的之后，更新基准图
```

`--audio` 目录里要有每个 beat 的 `<beat>.wav` 和 `<beat>.timing.json`（voice 流水线的输出格式；没有音频的 beat 按每个汉字 0.22 秒估算时长）。`--quality` 取 `l` / `m` / `h`。

### 分段播放、字幕和章节文件

`x.mp4` 渲染完，旁边还有（墨线后端）：

- `x.chapters.json`：播放器用的章节表；
- `x.vtt`、`x.srt`：软字幕（`style.subtitles: soft`，默认），同一份字幕也以 mov_text 轨道封装进 `x.mp4`（标为默认轨，播放器里可关；画面里没有字）；`subtitles: none` 时都不出；
- `x.burned.mp4`：只有加了 `--burn-subtitles` 才出，给不支持软字幕的平台。画面整体缩到 90%，底部让出一条字幕带，字幕画在带里，不压住 footer 的公式。

字幕文字是 `say` 的原文（不是 TTS 的读法）：去掉书签 `{@x}`，`$...$` 公式转成 Unicode（`a_n`→aₙ、`x^2`→x²、`\frac{2}{3}`→⅔、`\frac{a+b}{2}`→(a+b)/2、`\sqrt{x}`→√x），不留 LaTeX 命令。每句的起止取 `timing.json` 的 cues，加上这一段在成片里的起点（子步定格顺延）；长句按标点切成每条不超过 18 个字宽（汉字 1，西文和数字 0.5），句内时刻按 Whisper 对齐的书签插值。

`chapters.json`：

```json
{
  "id": "ep09", "title": "…", "video": "x.mp4", "duration": 126.3, "segmented": true,
  "subtitles": {"vtt": "x.vtt", "srt": "x.srt", "embedded": "mov_text", "cues": 25},
  "chapters": [
    {"id": "Hook", "title": "Hook", "start": 0.0, "end": 14.42, "shows": ["misconception"],
     "pause": {"type": "predict", "prompt": "…", "time": 13.92, "hold": 0.5, "app_item": "ep09-predict-1"},
     "segment_end": true, "steps": []},
    {"id": "Count", "start": 27.27, "end": 46.75, "segment_end": true,
     "steps": [{"time": 37.38, "hold": 1.0, "resume": 38.38, "at": "s2"}], "…": "…"}
  ],
  "stops": [
    {"time": 13.92, "kind": "predict", "beat": "Hook", "hold": 0.5, "resume": 14.42, "prompt": "…", "app_item": "ep09-predict-1"},
    {"time": 14.42, "kind": "segment_end", "beat": "Hook"},
    {"time": 37.38, "kind": "step", "beat": "Count", "hold": 1.0, "resume": 38.38}
  ]
}
```

- 时间都是成片里的秒数。`chapters[].start/end` 是 beat 的起止，按各段实际时长累加。
- `pause`：预测 / 想一想 / 分段暂停。`time` 是段尾画面定住的那 `hold` 秒的开始，播放器在这里暂停；`app_item` 是 App 里对应题目的 id。
- `steps`：子步定格（`step` 动作）。分段时视频里已经在 `time`..`resume` 定住；连续播放（复看）时播放器可以跳过这一段。不分段时 `hold` 为 0，只标出位置。`at` 是场景文件里写的时间点。
- `segment_end`：这一段末尾是分段点（`style.segmented: true` 时每段都是，或这一段 `pause.type: segment`）。
- `stops`：上面三类停顿按时间排好的一张表，`kind` 取 `step` / `predict` / `think` / `segment` / `segment_end`。

渲染结束会打印版式问题的条数；中间产物在 `--out` 旁边的 `.build-<名字>/` 里，每个 beat 一段 mp4，便于抽帧检查。

依赖：moxian（同目录 `../moxian`）、ffmpeg、latex + dvisvgm、Noto Sans CJK SC 字体；Manim 后端另需 Manim Community 0.21 和 xelatex（含 ctex）。

## 开发

```bash
uv run pytest
```

## 作者与许可

作者：Boxi Yu（[@BoxiYu](https://github.com/BoxiYu)）

代码以 [MIT 许可](LICENSE)发布。`src/animind_kit/data/palette.json` 来自 animind-ai/palette（MIT）。
