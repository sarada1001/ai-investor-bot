#!/usr/bin/env python3
"""
scripts/gen_test_report.py — pytest 実行結果を docs/TEST_REPORT.md に集計する

read-only スクリプト。pytest を実行するのみで、本番コード・状態ファイルには一切書き込まない。
「既知の失敗」をコード内にハードコードせず、実行のたびに現在の失敗テストを実測して明示する
（曖昧な既知リストの陳腐化を避け、常に実態と一致させるため）。

実行:
    python scripts/gen_test_report.py [--out PATH]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
TESTS_DIR = ROOT / "tests"
DEFAULT_OUT = ROOT / "docs" / "TEST_REPORT.md"

RESULT_LINE_RE = re.compile(
    r"^(?P<nodeid>tests/[^\s]+::[^\s]+)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b"
)


def run_pytest_verbose() -> tuple[int, list[str]]:
    """pytest -v --tb=no を実行し、(returncode, stdout_lines) を返す。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "--tb=no", "-q", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout + "\n" + proc.stderr).splitlines()


def parse_results(lines: list[str]) -> list[tuple[str, str]]:
    """[(nodeid, status), ...] を抽出する。"""
    results: list[tuple[str, str]] = []
    for line in lines:
        m = RESULT_LINE_RE.match(line.strip())
        if m:
            results.append((m.group("nodeid"), m.group("status")))
    return results


def build_report(results: list[tuple[str, str]]) -> str:
    per_file: dict[str, dict[str, int]] = defaultdict(lambda: {"PASSED": 0, "FAILED": 0, "ERROR": 0, "SKIPPED": 0})
    failing: list[str] = []

    for nodeid, status in results:
        file_path = nodeid.split("::", 1)[0]
        per_file[file_path][status] = per_file[file_path].get(status, 0) + 1
        if status in ("FAILED", "ERROR"):
            failing.append(f"{nodeid} — {status}")

    total = len(results)
    total_passed = sum(1 for _, s in results if s == "PASSED")
    total_failed = sum(1 for _, s in results if s in ("FAILED", "ERROR"))
    total_skipped = sum(1 for _, s in results if s == "SKIPPED")
    all_passing = total_failed == 0

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    lines = [
        "# テストスイート内訳レポート",
        "",
        f"生成日時（UTC）: {generated_at}",
        "",
        "**このファイルは `scripts/gen_test_report.py` による自動生成です。手動編集しないこと。**",
        "",
        "## サマリー",
        "",
        f"- 総テスト数: {total}",
        f"- PASSED: {total_passed}",
        f"- FAILED/ERROR: {total_failed}",
        f"- SKIPPED: {total_skipped}",
        f"- 成功率: {(total_passed / total * 100):.1f}%" if total else "- 成功率: N/A",
        f"- 全件成功（all_passing）: `{str(all_passing)}`",
        "",
        "## ファイル別内訳",
        "",
        "| ファイル | テスト数 | PASSED | FAILED/ERROR | SKIPPED |",
        "|---|---|---|---|---|",
    ]

    for file_path in sorted(per_file):
        counts = per_file[file_path]
        n = counts["PASSED"] + counts["FAILED"] + counts["ERROR"] + counts["SKIPPED"]
        failed_n = counts["FAILED"] + counts["ERROR"]
        lines.append(
            f"| `{file_path}` | {n} | {counts['PASSED']} | {failed_n} | {counts['SKIPPED']} |"
        )

    lines += ["", "## 現在の失敗テスト", ""]
    if failing:
        for entry in failing:
            lines.append(f"- `{entry}`")
    else:
        lines.append("現在、失敗しているテストはありません（`all_passing: true`）。")

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="出力先パス")
    args = parser.parse_args()

    print(f"Running pytest in {ROOT} ...")
    returncode, lines = run_pytest_verbose()
    results = parse_results(lines)

    if not results:
        print("pytest の実行結果をパースできませんでした。出力を確認してください:")
        print("\n".join(lines[-30:]))
        sys.exit(1)

    report = build_report(results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")

    total_failed = sum(1 for _, s in results if s in ("FAILED", "ERROR"))
    print(f"Wrote {args.out} — {len(results)} tests, {total_failed} failing.")


if __name__ == "__main__":
    main()
