"""regress -j：并行渲染各示例，结果、日志顺序、末帧和 report.json 都和串行一样。"""

import shutil

import pytest
from test_beat_cache import SIX

from animind_kit import regress

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")

SCENES = {
    "a": SIX,
    "b": SIX.replace("a+b=c", "x+y=z").replace("开始。", "从这里开始。"),
    "c": SIX.replace("theme: paper", "theme: chalk"),
}


def snapshot(work):
    return {p.relative_to(work).as_posix(): p.read_bytes() for p in sorted(work.rglob("*.png")) if "tex-jobs" not in p.parts} | {
        "report.json": (work / "report.json").read_bytes()}


def test_parallel_regress_matches_serial(tmp_path, monkeypatch):
    examples = tmp_path / "examples"
    examples.mkdir()
    for name, text in SCENES.items():
        (examples / f"{name}.scene.yaml").write_text(text, encoding="utf-8")
    monkeypatch.setattr(regress, "EXAMPLES", examples)
    monkeypatch.setattr(regress, "BASELINE", tmp_path / "baseline")
    monkeypatch.setattr(regress, "WORK", tmp_path / "work1")
    regress.run(update=True, jobs=1)
    shutil.rmtree(tmp_path / "baseline" / "b")  # 一个示例没有基准图（status=new），其余一致
    (tmp_path / "baseline" / "c" / "b6.png").unlink()

    outputs = {}
    for jobs in (1, 3):
        work = tmp_path / f"work{jobs}"
        shutil.rmtree(work, ignore_errors=True)
        monkeypatch.setattr(regress, "WORK", work)
        lines: list[str] = []
        results = regress.run(jobs=jobs, log=lines.append)
        outputs[jobs] = (results, lines, snapshot(work))
    assert outputs[1] == outputs[3]
    results, lines, _ = outputs[3]
    assert [r.example for r in results] == sorted(r.example for r in results)
    assert {r.status for r in results} == {"ok", "new"}
    assert lines == [f"渲染 {n}.scene.yaml：6 段，版式问题 0 条" for n in "abc"]


def test_default_jobs_is_capped(monkeypatch):
    monkeypatch.setattr(regress.os, "cpu_count", lambda: 64)
    assert regress.default_jobs() == regress.DEFAULT_JOBS_CAP
    monkeypatch.setattr(regress.os, "cpu_count", lambda: None)
    assert regress.default_jobs() == 1
