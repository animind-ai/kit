"""命令行：animind-kit render 场景文件 --out 成片 [--audio 目录] [--quality l|m|h]"""

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
    r.add_argument("--burn-subtitles", action="store_true",
                   help="另出一份把字幕烧进画面的 <名>.burned.mp4（给不支持软字幕的平台）；默认只出软字幕")
    c = sub.add_parser("compare", help="两个后端各渲染一遍并拼出每个 beat 的末帧对照图")
    c.add_argument("spec", type=Path)
    c.add_argument("--out", type=Path, required=True, help="输出目录")
    c.add_argument("--audio", type=Path)
    c.add_argument("--quality", choices=["l", "m", "h"], default="l")
    g = sub.add_parser("regress", help="截图回归：渲染示例末帧，和 tests/baseline 比对")
    g.add_argument("--update", action="store_true", help="用当前结果覆盖基准图")
    args = parser.parse_args(argv)

    if args.command == "regress":
        from .regress import run

        results = run(args.update)
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

    if args.command == "compare":
        from .compare import compare

        report = compare(args.spec, args.audio, args.out, args.quality)
        return 1 if any(b["layout_problems"] for b in report["backends"].values()) else 0

    from .render import render

    summary = render(args.spec, args.out, args.audio, args.quality, backend=args.backend,
                     burn_subtitles=args.burn_subtitles)
    return 1 if summary["layout_problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
