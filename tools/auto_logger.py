"""
auto_logger.py — Reflexion エージェント用 Obsidian ログ自動生成モジュール

AIエージェントの推論結果（辞書）を受け取り、
YAML フロントマター付き Markdown を data/knowledge_base/obsidian_logs/ に保存します。

Usage:
    from tools.auto_logger import ObsidianLogger

    logger = ObsidianLogger()
    logger.save_log({
        "ticker":  "NVDA",
        "action":  "BUY",
        "context": "VIX < 18. NVDA broke out above 200-day MA on high volume.",
        "tags":    ["reflexion", "breakout"],
    })
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

SAVE_DIR = Path(__file__).parent.parent / "data" / "knowledge_base" / "obsidian_logs"

REQUIRED_KEYS = {"ticker", "action", "context"}
DEFAULTS = {
    "outcome":        "PENDING",
    "profit_loss":    "N/A",
    "root_cause":     "決済後に追記",
    "rule_for_future": "決済後に追記",
    "tags":           [],
}


class ObsidianLogger:
    """AIエージェントの推論結果を Obsidian 用 Markdown ログとして保存するクラス。"""

    def __init__(self, save_dir: Path | str | None = None) -> None:
        self.save_dir = Path(save_dir) if save_dir else SAVE_DIR
        self.save_dir.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def save_log(self, trade_data: dict) -> Path:
        """
        trade_data を受け取り、Markdown ログを生成・保存する。

        Args:
            trade_data: エージェントの推論結果を含む辞書。
                必須キー: ticker, action, context
                任意キー: outcome, profit_loss, root_cause, rule_for_future, tags

        Returns:
            保存したファイルの Path オブジェクト。

        Raises:
            ValueError: 必須キーが不足している場合。
        """
        data = self._validate_and_fill(trade_data)
        filepath = self._resolve_filepath(data)
        markdown = self._build_markdown(data)
        filepath.write_text(markdown, encoding="utf-8")
        logger.info("Obsidian log saved: %s", filepath)
        return filepath

    # ----------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------

    def _validate_and_fill(self, raw: dict) -> dict:
        """必須キーを検証し、任意キーにデフォルト値を補完する。"""
        missing = REQUIRED_KEYS - raw.keys()
        if missing:
            raise ValueError(f"trade_data に必須キーが不足しています: {missing}")

        data = {**DEFAULTS, **raw}
        data["ticker"] = str(data["ticker"]).upper().strip()
        data["action"] = str(data["action"]).upper().strip()
        data["outcome"] = str(data["outcome"]).upper().strip()
        data["date"] = date.today().strftime("%Y-%m-%d")

        # tags をリストに統一（文字列で渡された場合はカンマ分割）
        if isinstance(data["tags"], str):
            data["tags"] = [t.strip() for t in data["tags"].split(",") if t.strip()]

        return data

    def _resolve_filepath(self, data: dict) -> Path:
        """ファイルパスを決定する。同名ファイルが存在する場合は連番を付ける。"""
        date_compact = data["date"].replace("-", "")
        base_name = f"Log_{date_compact}_{data['ticker']}_{data['action']}.md"
        filepath = self.save_dir / base_name

        if filepath.exists():
            i = 2
            while filepath.exists():
                stem = f"Log_{date_compact}_{data['ticker']}_{data['action']}_{i}"
                filepath = self.save_dir / f"{stem}.md"
                i += 1

        return filepath

    def _build_markdown(self, data: dict) -> str:
        """YAML フロントマターと本文を含む Markdown 文字列を組み立てる。"""
        tags_yaml = ", ".join(f'"{t}"' for t in data["tags"])

        pl = str(data["profit_loss"])
        if pl not in ("N/A", "PENDING") and not pl.startswith("-"):
            pl = f"+{pl}" if pl else pl
        pl_display = pl if pl.endswith("%") or pl in ("N/A", "PENDING") else f"{pl}%"

        def to_bullets(text: str) -> str:
            lines = [l.strip() for l in str(text).splitlines() if l.strip()]
            return "\n".join(f"- {l}" for l in lines) if lines else "- (未入力)"

        md = f"""\
---
date: {data["date"]}
ticker: {data["ticker"]}
action: {data["action"]}
outcome: {data["outcome"]}
profit_loss: {pl_display}
tags: [{tags_yaml}]
---

# [{data["outcome"]}] {data["ticker"]} — {data["action"]} ({pl_display}) | {data["date"]}

## 1. 当時の市場コンテキスト

{to_bullets(data["context"])}

## 2. 実際の結果

{to_bullets(data.get("actual_outcome", data["outcome"]))}

## 3. Root Cause Analysis（なぜ間違えたのか？）

{to_bullets(data["root_cause"])}

## 4. Rule for Future（次回への教訓）

{to_bullets(data["rule_for_future"])}
"""
        return md


# ============================================================
# 統合テスト（ManagerAgent の出力を模したダミーデータ）
# ============================================================
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # ManagerAgent が決済前（PENDING状態）に出力する推論結果を想定
    dummy_trade = {
        "ticker":  "NVDA",
        "action":  "BUY",
        "context": (
            "VIX was at 17.2 (low fear). NVDA broke above the 200-day MA "
            "on 1.8x average volume after a positive earnings surprise (EPS beat +12%). "
            "Semiconductor sector ETF (SOXX) confirmed relative strength."
        ),
        "tags": ["reflexion", "breakout", "earnings_beat", "low_vix"],
        # outcome / profit_loss / root_cause / rule_for_future は省略 → デフォルト値が入る
    }

    print("=" * 60)
    print("  ObsidianLogger 統合テスト")
    print("  (ManagerAgent PENDING状態ダミーデータ)")
    print("=" * 60)

    log_logger = ObsidianLogger()
    try:
        saved_path = log_logger.save_log(dummy_trade)
        print(f"\n✅ 保存しました: {saved_path}")
        print("\n--- 生成された Markdown の先頭 30 行 ---")
        lines = saved_path.read_text(encoding="utf-8").splitlines()
        for line in lines[:30]:
            print(line)
    except ValueError as e:
        print(f"❌ エラー: {e}", file=sys.stderr)
        sys.exit(1)
