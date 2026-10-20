"""命令行：
    animind-kit render 场景文件 --out 成片 [--audio 目录] [--quality l|m|h] [--lang en] [--only BEAT ...] [--no-cache]
    animind-kit inspect 场景文件 [--beat X] [--out 分镜图目录] [--audio 目录] [--lang en]
    animind-kit regress [-j N] [--update]

render 写 成片旁边的 timing-report.json，inspect 写到 --out（默认 build/inspect/<名>）。两者都过 scene-spec 的时间门禁
（CUE LATE 的 SS480、句间静音 SS487）：有错误就非零退出。regress 不做这个门禁。
render（墨线）按 beat 缓存：内容哈希没变的 beat 直接复用上次的分段；--only 只出选中 beat 的 <beat>.mp4（门禁只查它们）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="animind-kit")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("render", help="把 scene-spec 文件渲染成视频")
    r.add_argument("spec", type=Path)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--audio", type=Path, help="旁白目录：<beat>.wav 与 <beat>.timing.json")
    r.add_argument("--quality", choices=["l", "m", "h"], default="l")
    r.add_argument("--backend", choices=["manim", "moxian"], default="moxian", help="渲染引擎，默认墨线")
    r.add_argument("--subtitles", choices=["burned", "soft", "none"],
                   help="覆盖 style.subtitles：burned（默认，字幕烧进画面，另出画面干净的 <名>.clean.mp4）、soft、none")
    r.add_argument("--burn-subtitles", action="store_true",
                   help="soft 模式下另出一份把字幕烧进画面的 <名>.burned.mp4")
    r.add_argument("--theme", help="覆盖场景文件里的 style.theme：palette 主题名，或一个同格式的 .json 文件")
    r.add_argument("--only", action="append", metavar="BEAT",
                   help="只渲染这个 beat（可重复，墨线后端）：写成 --out 所在目录下的 <BEAT>.mp4（分段本身，不烧字幕），不出成片、"
                        "不写 timing-report.json；其余 beat 照样推演状态但不渲染；时间门禁只查选中的 beat")
    r.add_argument("--no-cache", action="store_true",
                   help="不用 beat 缓存，全部重渲（缓存在 --out 旁边的 .build-<名>-moxian/parts/<beat>.mp4 + .hash）")
    c = sub.add_parser("compare", help="两个后端各渲染一遍并拼出每个 beat 的末帧对照图")
    c.add_argument("spec", type=Path)
    c.add_argument("--out", type=Path, required=True, help="输出目录")
    c.add_argument("--audio", type=Path)
    c.add_argument("--quality", choices=["l", "m", "h"], default="l")
    i = sub.add_parser("inspect", help="不编码视频，逐批动作检查字形级版式问题并出分镜图（墨线后端）")
    i.add_argument("spec", type=Path)
    i.add_argument("--beat", action="append", help="只检查这个 beat（可重复）；其余 beat 照样推演但不检查")
    i.add_argument("--out", type=Path,
                   help="分镜图和 timing-report.json 的目录，默认 build/inspect/<场景名>")
    i.add_argument("--no-sheets", action="store_true", help="只打印问题，不出分镜图（timing-report.json 仍写到 --out 或默认目录）")
    i.add_argument("--audio", type=Path, help="旁白目录：用真实时长（没有就按字数估算）")
    i.add_argument("--quality", choices=["l", "m", "h"], default="m", help="和 render 用同一档，采样才落在同一帧")
    for cmd in (r, i):
        cmd.add_argument("--lang", choices=["zh", "en"], default="zh",
                         help="渲染哪种语言的版本（场景文件里每句的 en + i18n.en），旁白目录要是同一语言合成的")
    g = sub.add_parser("regress", help="截图回归：渲染示例末帧，和 tests/baseline 比对")
    g.add_argument("--update", action="store_true", help="用当前结果覆盖基准图")
    g.add_argument("-j", "--jobs", type=int, metavar="N",
                   help="并行渲染示例的进程数，默认 CPU 核数（最多 8）；-j 1 串行。结果、输出顺序和串行一样")
    args = parser.parse_args(argv)

    if args.command == "regress":
        from .regress import run

        results = run(args.update, jobs=args.jobs)
        for r in results:
            if r.status != "ok":
                print(f"  {r}")
        bad = [r for r in results if r.status in ("changed", "missing")]
        new = [r for r in results if r.status == "new"]
        if not results:
            print("基准图已更新")
        else:
            print(f"截图回归：{len(results) - len(bad) - len(new)} 段一致，{len(bad)} 段有变化，{len(new)} 段没有基准图")
        return 1 if bad else 0

    if args.command == "inspect":
        from .render import inspect

        name = args.spec.name.removesuffix(".yaml").removesuffix(".scene")
        report_dir = args.out or Path("build/inspect") / name  # timing-report.json 总在这里；分镜图只在没有 --no-sheets 时出
        out = None if args.no_sheets else report_dir
        problems = inspect(args.spec, args.audio, out, args.beat, args.quality, lang=args.lang, report_dir=report_dir)
        return 1 if problems else 0

    if args.command == "compare":
        from .compare import compare

        report = compare(args.spec, args.audio, args.out, args.quality)
        return 1 if any(b["layout_problems"] for b in report["backends"].values()) else 0

    from .render import render

    summary = render(args.spec, args.out, args.audio, args.quality, backend=args.backend,
                     burn_subtitles=args.burn_subtitles, theme_name=args.theme, subtitles=args.subtitles,
                     lang=args.lang, only=args.only, cache=not args.no_cache)
    # 视频照常写出；版式或时间门禁有错就非零退出（时间错误：CUE LATE 的 SS480、句间静音的 SS487、报告格式 SS481）
    return 1 if summary["layout_problems"] or summary["timing_issues"] else 0


if __name__ == "__main__":
    sys.exit(main())
