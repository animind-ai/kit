# animind-kit

[中文](#中文) | [English](#english)

## 中文

[scene-spec](https://github.com/animind-ai/scene-spec) 的运行时。读一份场景描述文件，加上旁白音频，渲染出成片。渲染引擎默认是 [墨线 moxian](https://github.com/animind-ai/moxian)，也可以切回 Manim（`--backend manim`）。

0.1 包含：

- **主题** `theme.py`：palette 的语义角色 → 颜色，以及派生的卡片底色、mark 垫色、浅底上的结论墨色。色值只来自 [animind-ai/palette](https://github.com/animind-ai/palette)。
- **文字** `text.py`：中文用 Noto Sans CJK SC（思源黑体，开源），公式走 xelatex + ctex；把 `[片段]{#id .角色}` 标注渲染成可单独指向、单独上色的段。
- **布局** `layout.py`：top / stage / side / footer 四个区域，`in`、`below`、`over`、`pin` 等约束；没人用的区域让给主讲区；渲染后做出框和重叠检查。
- **组件**：题卡（可回填答案）、步骤胶囊、想一想框、总结卡、差分表（derive / extend / triangle）、对齐等式组 `equations`、数列格子 `seq_cells`（fill）。墨线版在 `mx/`；Manim 版 `widgets/` 只有前五个，所以只能渲染 p01 这样的内容。
- **数轴** `mx/numberline.py`：跳跃（可从任意点起跳、几段接着走、换颜色、不标计数）、垫色（可几段并存）、带角色的点，
  以及轴下方和 0..end 等长的分数条（`bars: true` 预留位置；`bar` / `fill` / `group` 动作）；`unit` 调一个单位的长度。
  从 0 出发的箭头（`arrows: true`）：`arrow` 画、`stretch` 拉长（乘正数）、`turn` 绕 0 转半圈（乘 −1），负负得正用。
  数字和分数标注一律用 `\dfrac`（分子分母不缩小），排版后按数轴的实际缩放把标注字号抬到下限（见下面的"字号下限"）。
- **格子正方形** `mx/grid.py`（`area_grid`）：cols × rows 格，`cut` 画切线、`shade` 涂列或行、`overlap` 标出交叠格，分数乘法用。
  `rect: true` 时是小方格拼成的长方形：`split` 按段切开并标出各段长度、`apart` / `join` 分开拼回、`fill` 涂块并写格数、`unfill`，分配律和竖式乘法用。
- **十格框和点阵** `mx/dots.py`（`ten_frame`、`dot_array`）：十格框 `fill` / `side` / `move_in` / `count` / `recolor` / `remove`（凑十）；
  点阵 `rotate`（整体转 90°）、`extend`（补满到 10）、`fade_col`（淡出一列）、`squares`（点放进方格）、`recolor`。示例 `scene-spec/examples/numbers.scene.yaml`。
- **积木条形图** `mx/bars.py`（`block_bars`）：每列几块方块，`transfer` 把顶上的块跳到另一列（块数跟着变）、`add` 在空位长出新列、
  `level` 画一条几块高的虚线并写标签、`unlevel`、`recolor`，平均数"削峰填谷"用。示例 `scene-spec/examples/ep35.scene.yaml`。
- **钱包** `mx/wallet.py`（`wallet`）：上排现金、下排欠条，右边余额条；`put` 放进、`take` 拿走（欠条撕成两半落下，`quiet` 只淡出），
  负负得正（撕欠条）和减去负数用。示例 `scene-spec/examples/ep15-iou.scene.yaml`。
- **天平** `mx/balance.py`（`balance`）：两个托盘放砝码和一个未知盒子，按重量自动倾斜；`remove` / `add` 一边或两边拿走、放上砝码，
  `label` 换盒子上的字，解方程"两边同时减"用。示例 `scene-spec/examples/ep19.scene.yaml`。
- **立体** `mx/solid.py`（`solid`）：正方体、长方体、四棱锥、圆柱、圆锥的轴测图（淡色面、看不见的棱画虚线）；正方体剖成三个全等的四棱锥 `split` / `apart` / `join` / `unsplit`；
  截平面 `sweep` 扫过几个立体、`compare` 把截面平铺到旁边比大小（祖暅原理）；`orbit` 连续转动视角（每帧重算朝前的面、虚实棱、远近顺序，排版按 orbit 经过的视角留位置）；`set {role}` 换面和棱的颜色。示例 `scene-spec/examples/solids.scene.yaml`。
- **三维坐标空间** `mx/space.py`（`space`）：线性代数的三维画面（列空间、零空间、投影、行列式是体积）。从原点出发的向量 `vector`、点 `point`、直线 `line`（span，裁在坐标范围里）、
  平面 `plane`（`span` 或 `normal`，和坐标范围的交画成半透明多边形）、平行六面体 `box {edges}`（单位正方体 = 单位矩阵的三列，被挡住的棱画虚线）、
  `project {name, onto, as, error}`（投影从原点长出来、误差向量从原来那支的尖落下来、拐角画直角记号）、`warp {matrix: 3×3}`（整个空间连同 z = 0 的彩色网格渐变，可以奇异：压没了的直线和它的名字藏起来）、
  `reset`、`relabel`、`remove`、`orbit`（同 `solid`，每帧重算投影和远近顺序，排版按经过的视角留位置）。名字自动躲开轴名和别的名字。示例 `scene-spec/examples/space.scene.yaml`。
- **剖分** `mx/dissect.py`（`dissect`）：多边形、圆、扇形、点阵按直线 / 折线 / 扇形切开，块可以 `cut`、`shift`、`rotate`、`flip`、`snap`（边对边吸附）、`copy`、`merge`（拼合外轮廓）、`arrange`（扇形交错排开），
  几何和检查都来自 `scene_spec.dissect`。`fold {cut, flap, back?}`：沿一刀 `line` 切法的切线把 `flap` 那几块翻到另一侧（没切过就先切）——
  翻的那侧在垂直于折痕的方向从 1 压到 −1，中途变暗，`back` 是翻过来露出的背面颜色，落下后放在最上层；折纸、轴对称、角平分线用。
  示例 `scene-spec/examples/ep11.scene.yaml`（剪开挪过去）、`ep20-fold.scene.yaml`（等腰直角三角形折两次，√2 无理）。
- **坐标平面** `mx/plane.py`（`plane`）：淡网格 + 坐标轴（`complex: true` 时是复平面，纵轴写 i、2i），画在上面的东西都有名字：
  `vector`（分量 v、尾巴 at）、`slide`（平移，`copy` 时滑过去的是虚线副本：首尾相接）、`point`（`fixed` 时钉在画面上不跟着变）、`shape`（填色多边形）、`segment`（线段）、`line`（直线：`dir` + 可选 `through`，不写 through 就是过原点的 span；或 `eq: [p, q, r]`；裁在坐标范围里，跟着变换走，特征向量在 warp 时一直躺在自己的 span 上）、
  `rotate`（绕原点转，写了 name 时画出扫过的弧；不写 name 是整个平面转）、`scale`、`warp`（`mesh: true` 的彩色网格连同所有箭头按矩阵变形，和之前的复合）、`reset`、`relabel`、`highlight` / `unhighlight`（同色光晕 + 描边加粗，`pulse` 时跳两下，跟着变换走；标某一个东西用它，不用 mark 的金色方块）、`remove`；定义里写 `matrix: [[a, b], [c, d]]`（数或参数表达式）时整个平面再按它变形，`tween` 参数时连续地跟着变（行列式、矩阵元素用 `readout` 显示）。
  名字和刻度垫了底色，网格线不穿字。向量加法、乘 i 转 90°、矩阵变形（洞察 #32–#34）用。
- **随机试验** `mx/chance.py`：随机数都来自 `scene_spec.chance`（`random.Random(seed)`），旁白里说的数用动作的 `expect` 写出来，检查器按同一个 seed 核对。
  `coin_flips`：抛硬币的频率折线（横轴对数刻度），`flip`（画到第 k 次，右上角写"正面 h/k"）、`runs`（再画几条别的 seed）、`band`（p ± 1/√k 的漏斗）；
  `galton`：高尔顿板，`drop`（一批球依次落下、在格子里堆起来）、`path`（描出某个球的左右路线）、`pascal`（格子底下写路径数）、`expect`（二项分布的期望台阶）、`flat`（"每格一样"的水平线）、`erase`；
  `outcomes`：样本空间方格，`shade` / `unshade`、`strike` / `restore`（划掉被条件排除的格子）、`frame` / `unframe`（描出剩下的样本空间）。抛硬币频率、高尔顿板、条件概率（洞察 #36–#38）用。
- **墨线后端** `backends/moxian_backend.py` + `mx/`：对象树跨 beat 常驻，每个 beat 一条时间线；文字、组件、布局都在 `mx/` 里用墨线实现。动作按旁白句子和书签的时刻播放，画面跟不上旁白时打印 `CUE LATE`。
- **Manim 后端** `backends/manim_backend.py` + `widgets/`：保留用于对照，每个 beat 一个 Scene。
- **渲染** `render.py`：逐 beat 渲染，和旁白合成，拼成一支成片。
- 两个后端共用的部分：`analysis.py`（可见期静态分析、区域占用）、`regions.py`（画面分区、出框和重叠检查）、`timing.py`、`theme.py`。

分段播放（`step` 子步定格、`ghost` 残影、`pin` 常驻，见 scene-spec 设计稿 §7.6）、字幕导出的定格顺延只有墨线后端实现；Manim 后端只导出字幕和章节。

还没有：自动布局求解（现在只按约束摆放 + 检查）、三维（`solid` 是正交轴测图，`orbit` 只转相机，没有透视）、camera 动作、area_model 等洞察系列的原语、Whisper 词级时间戳（书签时刻按字符位置估算）。

### 安装

```bash
pip install "animind-kit @ git+https://github.com/animind-ai/kit@v0.1.0"
```

scene-spec、moxian、zh-math-reading 会按对应的 tag 一起装上（这几个包还没有发布到 PyPI）。系统里还要有 ffmpeg、xelatex + ctex、dvisvgm 和 Noto Sans CJK SC 字体；用 Manim 后端还要 Cairo / Pango（可参考 `.github/workflows/ci.yml` 里 Ubuntu 的 apt 包列表）。要改代码时，把依赖的几个仓库和本仓库并排检出到同一个目录，再 `uv sync`（见「开发」）。

### 用法

```bash
uv sync
uv run animind-kit render ../scene-spec/examples/s67-p01.scene.yaml \
  --out build/out/s67-p01.mp4 \
  --audio /path/to/p01/audio \
  --quality m            # l=480p15 m=720p30 h=1080p30
  # --backend manim      # 切回 Manim 对照
  # --subtitles soft     # 覆盖 style.subtitles：burned（默认）/ soft / none
  # --lang en            # 英文版：每句的 en + i18n.en（scene-spec 设计稿 §7.7）；音频要用 animind-voice --lang en 合成
```

英文版的字幕只有英文一行，在逗号、句号等标点后切成小句，再合并到每条不超过 44 个字符（不跨句号、问号合并）；中文版双语字幕的英文行里不显示书签。`animind-voice align` 还会在 timing.json 里写每个词首的真实时刻（`words`），显示文本和念的稿子字数对得上时（英文版、不含公式的中文句），每条字幕从那个词真正念出来的时刻开始，不再按字数比例估。

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

#### 版式检查：inspect

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

#### 字号下限

2026-10-17 定的默认值，按**成片 720p** 的像素写在 `lint.FLOORS_720`（手机上看得清）：

| 类别 | 下限 | 哪些字 |
|---|---|---|
| `label` | 36px | 数轴上的整数、分数刻度、点的标注、剩余段的分数 |
| `side` | 26px | `side` 侧栏里的对象（侧栏宽 4.2 个单位） |
| `practice` | 26px | 「练一练」/ 想一想框 `think_box` |

字幕烧进画面时画面整体缩到 88%，下限按 1 / 0.88 抬高（换成 1080p 渲染坐标：`label` 61px，其余 44px；不烧录时 54px、39px）。组件和区域在节点上标 `font_floor`，inspect / render 的 `small` 按类别判。数轴标注在排版后若被缩到下限以下，kit 加大 `label_size` 重建数轴再排一次；侧栏文字和练一练框的默认字号直接取下限，放不下时会报 `small`，这时把长句拆成几行。

#### 分段播放、字幕和章节文件

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

### 开发

```bash
uv run pytest
```

### 作者与许可

作者：Boxi Yu（[@BoxiYu](https://github.com/BoxiYu)）

代码以 [MIT 许可](LICENSE)发布。`src/animind_kit/data/palette.json` 来自 animind-ai/palette（MIT）。引用方式见 [CITATION.cff](CITATION.cff)。

## English

The runtime for [scene-spec](https://github.com/animind-ai/scene-spec). It reads a scene description file plus narration audio and renders the finished video. The rendering engine defaults to [moxian 墨线](https://github.com/animind-ai/moxian); you can switch back to Manim (`--backend manim`).

0.1 includes:

- **Theme** `theme.py`: palette semantic roles → colors, plus derived card backgrounds, mark highlight colors, and conclusion ink color on light backgrounds. Color values come only from [animind-ai/palette](https://github.com/animind-ai/palette).
- **Text** `text.py`: Chinese uses Noto Sans CJK SC (Source Han Sans, open source); formulas go through xelatex + ctex; `[fragment]{#id .role}` annotations are rendered as segments that can be targeted and colored individually.
- **Layout** `layout.py`: four regions — top / stage / side / footer — and constraints such as `in`, `below`, `over`, `pin`; unused regions are given to the main stage; after rendering, out-of-frame and overlap checks are run.
- **Widgets**: problem card (answer can be filled in later), step capsule, think box, summary card, difference table (derive / extend / triangle), aligned equation group `equations`, sequence cells `seq_cells` (fill). The moxian versions are in `mx/`; the Manim versions in `widgets/` cover only the first five, so they can only render content like p01.
- **Number line** `mx/numberline.py`: jumps (from any point, several segments in a row, color changes, without counting labels), highlight bands (several can coexist), points with roles,
  plus fraction bars under the axis spanning 0..end (`bars: true` reserves space; `bar` / `fill` / `group` actions); `unit` sets the length of one unit.
  Arrows from 0 (`arrows: true`): `arrow` draws, `stretch` lengthens (multiply by a positive number), `turn` rotates half a turn around 0 (multiply by −1), used for "negative times negative is positive".
  Number and fraction labels always use `\dfrac` (numerator and denominator not shrunk); after typesetting, label size is raised to the floor according to the axis's actual scale (see "Font size floors" below).
- **Grid square** `mx/grid.py` (`area_grid`): cols × rows cells; `cut` draws cut lines, `shade` shades columns or rows, `overlap` marks the overlapping cells; used for fraction multiplication.
  With `rect: true` it is a rectangle made of small squares: `split` cuts it into parts and labels each part's length, `apart` / `join` separates and rejoins, `fill` shades a block and writes the cell count, `unfill`; used for the distributive law and long multiplication.
- **Ten frame and dot array** `mx/dots.py` (`ten_frame`, `dot_array`): ten frame `fill` / `side` / `move_in` / `count` / `recolor` / `remove` (making ten);
  dot array `rotate` (rotate the whole thing 90°), `extend` (fill up to 10), `fade_col` (fade out a column), `squares` (put dots into squares), `recolor`. Example: `scene-spec/examples/numbers.scene.yaml`.
- **Block bar chart** `mx/bars.py` (`block_bars`): several blocks per column; `transfer` moves the top block to another column (counts update), `add` grows a new column in an empty slot,
  `level` draws a dashed line at a given block height with a label, `unlevel`, `recolor`; used for "leveling" the mean. Example: `scene-spec/examples/ep35.scene.yaml`.
- **Wallet** `mx/wallet.py` (`wallet`): cash on the top row, IOUs on the bottom row, a balance bar on the right; `put` adds, `take` removes (an IOU tears in two and falls; `quiet` just fades out),
  used for negative times negative (tearing up IOUs) and subtracting negatives. Example: `scene-spec/examples/ep15-iou.scene.yaml`.
- **Balance scale** `mx/balance.py` (`balance`): two pans holding weights and an unknown box, tilting automatically by weight; `remove` / `add` takes off or puts on weights on one or both sides,
  `label` changes the text on the box; used for solving equations by "subtracting from both sides". Example: `scene-spec/examples/ep19.scene.yaml`.
- **Solids** `mx/solid.py` (`solid`): axonometric drawings of cubes, cuboids, square pyramids, cylinders, and cones (light faces, hidden edges dashed); a cube split into three congruent square pyramids `split` / `apart` / `join` / `unsplit`;
  a cutting plane `sweep` across several solids, `compare` lays the cross-sections out to the side to compare sizes (Cavalieri's / Zu Geng's principle); `set {role}` changes face and edge colors. Example: `scene-spec/examples/solids.scene.yaml`.
- **Dissection** `mx/dissect.py` (`dissect`): polygons, circles, sectors, and dot arrays cut by straight lines / polylines / sectors; pieces can `cut`, `shift`, `rotate`, `flip`, `snap` (edge-to-edge snapping), `copy`, `merge` (join outer outlines), `arrange` (sectors laid out alternately);
  geometry and checks all come from `scene_spec.dissect`. `fold {cut, flap, back?}`: along the cut line of a single `line` cut, flips the `flap` pieces to the other side (cutting first if not yet cut) —
  the flipping side is compressed from 1 to −1 perpendicular to the crease, darkening midway; `back` is the color of the back side revealed after flipping, and it lands on the top layer; used for paper folding, axial symmetry, and angle bisectors.
  Examples: `scene-spec/examples/ep11.scene.yaml` (cut and move over), `ep20-fold.scene.yaml` (fold an isosceles right triangle twice: √2 is irrational).
- **Coordinate plane** `mx/plane.py` (`plane`): faint grid + axes (with `complex: true` it is the complex plane, vertical axis labeled i, 2i); everything drawn on it has a name:
  `vector` (components v, tail at), `slide` (translate; with `copy`, a dashed copy slides over: tip to tail), `point` (with `fixed`, pinned to the screen and not transformed), `shape` (filled polygon),
  `rotate` (around the origin; with a name, draws the swept arc; without a name, the whole plane rotates), `scale`, `warp` (the colored `mesh: true` grid and all arrows are transformed by a matrix, composed with earlier ones), `reset`, `relabel`, `remove`.
  Names and tick labels have background pads, so grid lines don't run through text. Used for vector addition, multiplying by i as a 90° rotation, and matrix transformations (insights #32–#34).
- **Random experiments** `mx/chance.py`: random numbers all come from `scene_spec.chance` (`random.Random(seed)`); numbers spoken in the narration are written in the action's `expect`, and the checker verifies them with the same seed.
  `coin_flips`: frequency curve of coin flips (log-scale x axis), `flip` (draw up to flip k, with "heads h/k" at top right), `runs` (draw a few more seeds), `band` (the p ± 1/√k funnel);
  `galton`: Galton board, `drop` (a batch of balls falls one by one and piles up in the bins), `path` (trace one ball's left/right route), `pascal` (write path counts under the bins), `expect` (the binomial expectation steps), `flat` (the "every bin equal" horizontal line), `erase`;
  `outcomes`: sample-space grid, `shade` / `unshade`, `strike` / `restore` (strike out cells excluded by a condition), `frame` / `unframe` (outline the remaining sample space). Used for coin-flip frequencies, the Galton board, and conditional probability (insights #36–#38).
- **moxian backend** `backends/moxian_backend.py` + `mx/`: the object tree persists across beats, one timeline per beat; text, widgets, and layout are all implemented with moxian in `mx/`. Actions play at the times of narration sentences and bookmarks; when the picture can't keep up with the narration, `CUE LATE` is printed.
- **Manim backend** `backends/manim_backend.py` + `widgets/`: kept for comparison, one Scene per beat.
- **Rendering** `render.py`: renders beat by beat, combines with narration, and concatenates into one finished video.
- Shared by both backends: `analysis.py` (static visibility analysis, region occupancy), `regions.py` (frame regions, out-of-frame and overlap checks), `timing.py`, `theme.py`.

Segmented playback (`step` sub-step freezes, `ghost` afterimages, `pin` persistent items; see scene-spec design doc §7.6) and freeze-aware shifting of exported subtitles are implemented only in the moxian backend; the Manim backend only exports subtitles and chapters.

Not yet: automatic layout solving (currently placement by constraints + checks only), 3D (`solid` is a fixed-view axonometric drawing; the view can't rotate), camera actions, primitives for the insight series such as area_model, Whisper word-level timestamps (bookmark times are estimated from character positions).

### Installation

```bash
pip install "animind-kit @ git+https://github.com/animind-ai/kit@v0.1.0"
```

scene-spec, moxian and zh-math-reading are installed from their matching tags (these packages are not on PyPI yet). The system also needs ffmpeg, xelatex + ctex, dvisvgm and the Noto Sans CJK SC font; the Manim backend also needs Cairo / Pango (see the apt package list for Ubuntu in `.github/workflows/ci.yml`). To work on the code, check out the repositories it depends on next to this one in the same directory and run `uv sync` (see "Development").

### Usage

```bash
uv sync
uv run animind-kit render ../scene-spec/examples/s67-p01.scene.yaml \
  --out build/out/s67-p01.mp4 \
  --audio /path/to/p01/audio \
  --quality m            # l=480p15 m=720p30 h=1080p30
  # --backend manim      # switch back to Manim for comparison
  # --subtitles soft     # override style.subtitles: burned (default) / soft / none
  # --lang en            # English version: each sentence's en + i18n.en (scene-spec design doc §7.7); audio must be synthesized with animind-voice --lang en
```

In the English version, subtitles have a single English line, split into short clauses after commas, periods, and other punctuation, then merged so each cue is at most 44 characters (never merging across periods or question marks); in the Chinese version's bilingual subtitles, bookmarks are not shown in the English line. `animind-voice align` also writes the real start time of each word into timing.json (`words`); when the displayed text and the spoken script have matching character counts (English versions, and Chinese sentences without formulas), each subtitle starts at the moment its word is actually spoken instead of being estimated from character proportions.

Rendering the whole §6.7 episode 01 (4 min 40 s) with the moxian backend: about 4 s at 480p and about 9 s at 720p; with the Manim backend, about 26 s at 480p and about 24 s at 720p.

For each new episode, run it through both backends and compare, until moxian has handled more than ten episodes; then retire the Manim backend:

```bash
uv run animind-kit compare ../scene-spec/examples/s67-p01.scene.yaml --audio /path/to/p01/audio --out build/compare
```

The outputs are two videos, side-by-side comparison images of each beat's last frame (Manim on the left, moxian on the right), and a `report.json` (render time, layout problems). When a widget the Manim backend lacks is used (e.g. p02), compare last frames segment by segment against the original hand-written video instead.

Screenshot regression (also run in CI): renders every example under `../scene-spec/examples/` without audio and compares the last frame of each segment against `tests/baseline/<platform>/`:

```bash
uv run animind-kit regress            # exit code 1 if anything changed
uv run animind-kit regress --update   # after confirming the visual change is intended, update the baseline images
```

#### Layout check: inspect

Run this first when changing layout; it doesn't encode video, so one video takes a second or two:

```bash
uv run animind-kit inspect ../scene-spec/examples/ep30.scene.yaml            # storyboards written to build/inspect/ep30/
uv run animind-kit inspect ../scene-spec/examples/s67-p02.scene.yaml --audio /path/to/p02/audio --beat Guess
uv run animind-kit inspect ../scene-spec/examples/ep22.scene.yaml --no-sheets   # print problems only
```

It looks at every piece of text in the first frame after each batch of actions and in the last frame of each beat (moxian backend) and reports five kinds of problems: `small` (font size below `style.min_font`, 16px if unset, measured at 1080p; some text categories have their own floors, see below), `cross` (a line or fill edge runs through text, including within the same object), `covered` (an opaque fill covers text), `overlap` (two pieces of text overlap), `clip` (extends beyond the frame). Each problem has a number, beat, time range (time within the beat's segment of the video, and approximate time in the whole video), the source action (`do[4] focus at=s2@b3`), and the objects involved, for example:

```
[5] Zoom t=3.9s（片中 83.0s） do[5] camera at=s1@z  cross  cap10 × g：g 的线穿过文字「放大 10 倍：弯曲已经不太明显」
```

(The tool's output is in Chinese: "片中" = time in the whole video; the message reads "g's line crosses the text '…'".)

The same problem across several beats is merged into one entry. Each storyboard sheet has 6 panels in time order; problems are circled in red with the same numbers (a dashed box marks the other party). `render` runs the same checks on the same sampled frames and lists problems under "layout problems" (版式问题). For intentional cases, write `allow` on the object (see scene-spec design doc §6.4). Exit code is 1 if there are problems.

The `--audio` directory must contain `<beat>.wav` and `<beat>.timing.json` for each beat (the voice pipeline's output format; beats without audio are estimated at 0.22 s per Chinese character). `--quality` takes `l` / `m` / `h`.

#### Font size floors

Defaults set on 2026-10-17, written in **finished-720p** pixels in `lint.FLOORS_720` (readable on phones):

| Category | Floor | Which text |
|---|---|---|
| `label` | 36px | Integers and fraction ticks on number lines, point labels, fractions of remaining segments |
| `side` | 26px | Objects in the `side` panel (the panel is 4.2 units wide) |
| `practice` | 26px | "Practice" (练一练) / think box `think_box` |

When subtitles are burned in, the whole picture is scaled to 88%, so floors are raised by 1 / 0.88 (in 1080p render coordinates: `label` 61px, others 44px; without burn-in, 54px and 39px). Widgets and regions set `font_floor` on nodes, and `small` in inspect / render judges by category. If number-line labels end up below the floor after typesetting, kit increases `label_size`, rebuilds the number line, and lays it out again; side-panel text and practice boxes take the floor directly as their default size, and report `small` when they don't fit — then split long sentences into several lines.

#### Segmented playback, subtitle and chapter files

After `x.mp4` is rendered, next to it you also get (moxian backend):

- `x.chapters.json`: the chapter table for players;
- `x.vtt`, `x.srt`: subtitle files, produced in all three modes;
- Subtitles on by default (`style.subtitles: burned`, the default): subtitles are burned into the picture of `x.mp4`; the whole picture is scaled to 88%, leaving a subtitle band at the bottom where subtitles are drawn, so they don't cover formulas in the footer; a separate `x.clean.mp4` is also produced (clean picture, subtitles muxed as a mov_text track, for apps, YouTube and other players that display subtitles themselves);
- `subtitles: soft`: subtitles are only muxed into `x.mp4` as a mov_text track (default track, can be turned off); add `--burn-subtitles` to also produce a burned-in copy `x.burned.mp4`; with `subtitles: none`, neither is produced;
- Chinese–English bilingual: when a sentence has `en` (English subtitle), each cue has two lines, Chinese on top and English below (when burned in, English is one size smaller and a bit lighter); English is split by words into as many pieces as the Chinese cues and follows them.

Subtitle text is the original `say` text (not the TTS reading): bookmarks `{@x}` are removed, and `$...$` formulas are converted to Unicode (`a_n`→aₙ, `x^2`→x², `\frac{2}{3}`→⅔, `\frac{a+b}{2}`→(a+b)/2, `\sqrt{x}`→√x), with no LaTeX commands left. Each sentence's start and end come from the cues in `timing.json`, plus the segment's start in the finished video (shifted for sub-step freezes); long sentences are split at punctuation into cues of at most 18 character widths (CJK characters 1, Latin letters and digits 0.5), with times within a sentence interpolated from Whisper-aligned bookmarks.

`chapters.json`:

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

- All times are seconds in the finished video. `chapters[].start/end` are the beat's start and end, accumulated from each segment's actual duration.
- `pause`: predict / think / segment pause. `time` is the start of the `hold` seconds during which the picture freezes at the end of the segment; the player pauses here; `app_item` is the id of the corresponding question in the app.
- `steps`: sub-step freezes (`step` actions). When segmented, the video is already frozen from `time` to `resume`; in continuous playback (review), the player may skip this part. When not segmented, `hold` is 0 and only the position is marked. `at` is the time point written in the scene file.
- `segment_end`: the end of this segment is a segment point (true for every segment when `style.segmented: true`, or when this segment has `pause.type: segment`).
- `stops`: the three kinds of stops above, in one time-ordered table; `kind` is `step` / `predict` / `think` / `segment` / `segment_end`.

At the end of rendering, the number of layout problems is printed; intermediate products are in `.build-<name>/` next to `--out`, one mp4 per beat, for easy frame inspection.

Requirements: moxian (sibling directory `../moxian`), ffmpeg, latex + dvisvgm, the Noto Sans CJK SC font; the Manim backend additionally needs Manim Community 0.21 and xelatex (with ctex).

### Development

```bash
uv run pytest
```

### Author and license

Author: Boxi Yu ([@BoxiYu](https://github.com/BoxiYu))

The code is released under the [MIT License](LICENSE). `src/animind_kit/data/palette.json` comes from animind-ai/palette (MIT). To cite, see [CITATION.cff](CITATION.cff).
