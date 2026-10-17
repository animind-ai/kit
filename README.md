# animind-kit

[scene-spec](../scene-spec) 的运行时。读一份场景描述文件，加上旁白音频，渲染出成片。渲染引擎默认是 [墨线 moxian](../moxian)，也可以切回 Manim（`--backend manim`）。

0.1 包含：

- **主题** `theme.py`：palette 的语义角色 → 颜色，以及派生的卡片底色、mark 垫色、浅底上的结论墨色。色值只来自 [animind-ai/palette](https://github.com/animind-ai/palette)。
- **文字** `text.py`：中文用 Noto Sans CJK SC（思源黑体，开源），公式走 xelatex + ctex；把 `[片段]{#id .角色}` 标注渲染成可单独指向、单独上色的段。
- **布局** `layout.py`：top / stage / side / footer 四个区域，`in`、`below`、`over`、`pin` 等约束；没人用的区域让给主讲区；渲染后做出框和重叠检查。
- **组件**：题卡（可回填答案）、步骤胶囊、想一想框、总结卡、差分表（derive / extend / triangle）、对齐等式组 `equations`、数列格子 `seq_cells`（fill）。墨线版在 `mx/`；Manim 版 `widgets/` 只有前五个，所以只能渲染 p01 这样的内容。
- **数轴** `mx/numberline.py`：跳跃（可从任意点起跳、几段接着走、换颜色、不标计数）、垫色（可几段并存）、带角色的点，
  以及轴下方和 0..end 等长的分数条（`bars: true` 预留位置；`bar` / `fill` / `group` 动作）；`unit` 调一个单位的长度。
  数字和分数标注一律用 `\dfrac`（分子分母不缩小），排版后按数轴的实际缩放把标注字号抬到下限（见下面的"字号下限"）。
- **格子正方形** `mx/grid.py`（`area_grid`）：cols × rows 格，`cut` 画切线、`shade` 涂列或行、`overlap` 标出交叠格，分数乘法用。
  `rect: true` 时是小方格拼成的长方形：`split` 按段切开并标出各段长度、`apart` / `join` 分开拼回、`fill` 涂块并写格数、`unfill`，分配律和竖式乘法用。
- **十格框和点阵** `mx/dots.py`（`ten_frame`、`dot_array`）：十格框 `fill` / `side` / `move_in` / `count` / `recolor` / `remove`（凑十）；
  点阵 `rotate`（整体转 90°）、`extend`（补满到 10）、`fade_col`（淡出一列）、`squares`（点放进方格）、`recolor`。示例 `scene-spec/examples/numbers.scene.yaml`。
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
  # --subtitles soft     # 覆盖 style.subtitles：burned（默认）/ soft / none
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

### 版式检查：inspect

改版式时先跑这个，不编码视频，一支片子一两秒：

```bash
uv run animind-kit inspect ../scene-spec/examples/ep30.scene.yaml            # 分镜图写到 build/inspect/ep30/
uv run animind-kit inspect ../scene-spec/examples/s67-p02.scene.yaml --audio /path/to/p02/audio --beat Guess
uv run animind-kit inspect ../scene-spec/examples/ep22.scene.yaml --no-sheets   # 只打印问题
```

在每批动作结束后的第一帧和每个 beat 的末帧看每个字（墨线后端），报五类问题：`small`（字号低于 `style.min_font`，没写时 16px，按 1080p 算；几类文字另有下限，见下）、`cross`（线或填充的边穿过字，同一对象内部也算）、`covered`（不透明填充盖住字）、`overlap`（两段文字压在一起）、`clip`（伸出画面）。每条问题带编号、beat、时间范围（beat 内的成片时刻和整支片子里的大致时刻）、来源动作（`do[4] focus at=s2@b3`）和涉及的对象，例如：

```
[5] Zoom t=3.9s（片中 83.0s） do[5] camera at=s1@z  cross  cap10 × g：g 的线穿过文字「放大 10 倍：弯曲已经不太明显」
```

同一个问题跨好几个 beat 时合并成一条。分镜图每张 6 格、按时间排，问题用红框圈出、标上同样的编号（虚线框是另一方）。`render` 跑的是同一套检查、同样的采样帧，问题列在"版式问题"里。确实是故意的情况，在对象上写 `allow`（见 scene-spec 设计稿 §6.4）。有问题时退出码 1。

`--audio` 目录里要有每个 beat 的 `<beat>.wav` 和 `<beat>.timing.json`（voice 流水线的输出格式；没有音频的 beat 按每个汉字 0.22 秒估算时长）。`--quality` 取 `l` / `m` / `h`。

### 字号下限

2026-10-17 定的默认值，按**成片 720p** 的像素写在 `lint.FLOORS_720`（手机上看得清）：

| 类别 | 下限 | 哪些字 |
|---|---|---|
| `label` | 36px | 数轴上的整数、分数刻度、点的标注、剩余段的分数 |
| `side` | 26px | `side` 侧栏里的对象（侧栏宽 4.2 个单位） |
| `practice` | 26px | 「练一练」/ 想一想框 `think_box` |

字幕烧进画面时画面整体缩到 88%，下限按 1 / 0.88 抬高（换成 1080p 渲染坐标：`label` 61px，其余 44px；不烧录时 54px、39px）。组件和区域在节点上标 `font_floor`，inspect / render 的 `small` 按类别判。数轴标注在排版后若被缩到下限以下，kit 加大 `label_size` 重建数轴再排一次；侧栏文字和练一练框的默认字号直接取下限，放不下时会报 `small`，这时把长句拆成几行。

### 分段播放、字幕和章节文件

`x.mp4` 渲染完，旁边还有（墨线后端）：

- `x.chapters.json`：播放器用的章节表；
- `x.vtt`、`x.srt`：字幕文件，三种模式都出；
- 字幕默认开（`style.subtitles: burned`，默认）：字幕烧进 `x.mp4` 的画面，画面整体缩到 88%，底部让出一条字幕带，字幕画在带里，不压住 footer 的公式；另出 `x.clean.mp4`（画面干净，字幕以 mov_text 轨道封装，给 App、YouTube 这类自己显示字幕的播放器）；
- `subtitles: soft`：字幕只以 mov_text 轨道封装进 `x.mp4`（默认轨，可关）；再加 `--burn-subtitles` 另出烧录副本 `x.burned.mp4`；`subtitles: none` 时都不出；
- 中英双语：句子写了 `en`（英文字幕）时每条两行，中文在上、英文在下（烧录时英文小一号、颜色淡一点）；英文按词切成和中文条数相当的几段，跟着中文走。

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
