#!/usr/bin/env python3
"""
create_reflexion_log.py — Reflexion エージェント用 Obsidian ログ生成 CLI

対話形式でトレード振り返りを入力し、YAML フロントマター付き Markdown を
data/knowledge_base/obsidian_logs/ に保存します。
"""

import sys
from datetime import date
from pathlib import Path

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt, IntPrompt
    from rich.table import Table
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ============================================================
# 設定
# ============================================================
SAVE_DIR = Path(__file__).parent.parent / "data" / "knowledge_base" / "obsidian_logs"

ACTIONS = ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]
OUTCOMES = ["SUCCESS", "FAILURE"]

console = Console() if HAS_RICH else None


# ============================================================
# 入出力ヘルパー
# ============================================================
def ask(prompt_text: str, default: str = "", choices: list[str] | None = None) -> str:
    """rich がある場合は Prompt.ask、なければ標準 input を使う。"""
    if HAS_RICH:
        kwargs = {}
        if default:
            kwargs["default"] = default
        if choices:
            kwargs["choices"] = choices
        return Prompt.ask(f"  [bold cyan]{prompt_text}[/bold cyan]", **kwargs).strip()
    else:
        hint = f" [{default}]" if default else ""
        hint += f" ({'/'.join(choices)})" if choices else ""
        return input(f"  {prompt_text}{hint}: ").strip() or default


def ask_multiline(prompt_text: str) -> str:
    """複数行入力を受け付ける（空行で終了）。"""
    if HAS_RICH:
        console.print(f"  [bold cyan]{prompt_text}[/bold cyan]  [dim](空行で入力終了)[/dim]")
    else:
        print(f"  {prompt_text} (空行で入力終了):")
    lines = []
    while True:
        try:
            line = input("  > ")
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)


def print_header():
    if HAS_RICH:
        console.print(Panel.fit(
            "[bold magenta]Reflexion Log Generator[/bold magenta]\n"
            "[dim]トレード振り返りを入力し、Obsidian 用 Markdown を生成します[/dim]",
            border_style="magenta",
            padding=(0, 2),
        ))
    else:
        print("=" * 60)
        print("  Reflexion Log Generator")
        print("=" * 60)


def print_preview(data: dict):
    """入力内容のプレビューを表形式で表示する。"""
    if not HAS_RICH:
        return
    table = Table(title="入力内容の確認", box=box.ROUNDED, border_style="dim")
    table.add_column("項目", style="cyan", no_wrap=True)
    table.add_column("値", style="white")
    for k, v in data.items():
        val = str(v).replace("\n", " ↵ ")
        table.add_row(k, val[:80] + ("…" if len(str(v)) > 80 else ""))
    console.print(table)


# ============================================================
# Markdown 生成
# ============================================================
def build_markdown(data: dict) -> str:
    tags_yaml = ", ".join(f'"{t.strip()}"' for t in data["tags"].split(",") if t.strip())
    pl = data["profit_loss"]
    pl_display = f"+{pl}%" if not pl.startswith("-") else f"{pl}%"

    md = f"""---
date: {data["date"]}
ticker: {data["ticker"]}
action: {data["action"]}
outcome: {data["outcome"]}
profit_loss: {pl_display}
tags: [{tags_yaml}]
---

# [{data["outcome"]}] {data["ticker"]} — {data["action"]} ({pl_display}) | {data["date"]}

## 1. 当時の市場コンテキスト

{chr(10).join("- " + line for line in data["context"].splitlines() if line.strip())}

## 2. 実際の結果

{chr(10).join("- " + line for line in data["actual_outcome"].splitlines() if line.strip())}

## 3. Root Cause Analysis（なぜ間違えたのか？）

{chr(10).join("- " + line for line in data["root_cause"].splitlines() if line.strip())}

## 4. Rule for Future（次回への教訓）

{chr(10).join("- " + line for line in data["rule"].splitlines() if line.strip())}
"""
    return md.strip() + "\n"


# ============================================================
# メイン
# ============================================================
def main():
    print_header()
    if HAS_RICH:
        console.print()

    today = date.today().strftime("%Y-%m-%d")

    # --- 基本情報 ---
    ticker = ask("Ticker (例: NVDA)").upper()
    if not ticker:
        ticker = "UNKNOWN"

    action = ask("Action", choices=ACTIONS)

    outcome = ask("Outcome", choices=OUTCOMES)

    pl_raw = ask("Profit/Loss % (例: -3.5 または 5.2)")
    # 記号の正規化
    pl_raw = pl_raw.lstrip("+").strip() if pl_raw else "0"

    tags = ask("Tags (カンマ区切り。例: reflexion, high_vix)", default="reflexion")

    if HAS_RICH:
        console.print("\n  [dim]─── 以下は複数行入力できます ───[/dim]\n")

    context      = ask_multiline("当時の市場コンテキスト (Context)")
    actual_outcome = ask_multiline("実際の結果 (Actual Outcome)")
    root_cause   = ask_multiline("なぜ間違えたのか？ (Root Cause Analysis)")
    rule         = ask_multiline("次回への教訓・ルール (Rule for Future)")

    data = {
        "date":           today,
        "ticker":         ticker,
        "action":         action,
        "outcome":        outcome,
        "profit_loss":    pl_raw,
        "tags":           tags,
        "context":        context or "(未入力)",
        "actual_outcome": actual_outcome or "(未入力)",
        "root_cause":     root_cause or "(未入力)",
        "rule":           rule or "(未入力)",
    }

    # --- プレビュー ---
    if HAS_RICH:
        console.print()
    print_preview(data)

    # --- 確認 ---
    confirm = ask("\n保存しますか？", choices=["y", "n"], default="y")
    if confirm.lower() != "y":
        if HAS_RICH:
            console.print("[yellow]キャンセルしました。[/yellow]")
        else:
            print("キャンセルしました。")
        sys.exit(0)

    # --- ファイル生成・保存 ---
    SAVE_DIR.mkdir(parents=True, exist_ok=True)

    date_compact = today.replace("-", "")
    filename = f"Log_{date_compact}_{ticker}_{outcome}.md"
    filepath = SAVE_DIR / filename

    # 同名ファイルが既にある場合は連番を付ける
    if filepath.exists():
        i = 2
        while filepath.exists():
            filepath = SAVE_DIR / f"Log_{date_compact}_{ticker}_{outcome}_{i}.md"
            i += 1

    markdown = build_markdown(data)
    filepath.write_text(markdown, encoding="utf-8")

    if HAS_RICH:
        console.print(f"\n[bold green]✅ 保存しました: {filepath}[/bold green]")
    else:
        print(f"\n✅ 保存しました: {filepath}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        msg = "\n\n中断しました。"
        if HAS_RICH:
            Console().print(f"[yellow]{msg}[/yellow]")
        else:
            print(msg)
        sys.exit(1)
