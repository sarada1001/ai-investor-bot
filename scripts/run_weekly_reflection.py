#!/usr/bin/env python3
"""
run_weekly_reflection.py — 週次自動反省会スクリプト

直近7日間の obsidian_logs を読み込み、Ollama（Gemini フォールバック）で
メタ教訓を生成し、wiki/concepts/Weekly_Reflection_YYYY-MM-DD.md に保存する。

Usage:
    python scripts/run_weekly_reflection.py            # 通常実行
    python scripts/run_weekly_reflection.py --dry-run  # プロンプトのみ表示
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────
# パス設定
# ─────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
load_dotenv(PROJECT_ROOT / ".env")

LOG_DIR      = PROJECT_ROOT / "data" / "knowledge_base" / "obsidian_logs"
CONCEPTS_DIR = PROJECT_ROOT / "data" / "knowledge_base" / "wiki" / "concepts"

OLLAMA_URL   = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434") + "/api/generate"
OLLAMA_MODEL = "llama3.1:latest"
OLLAMA_TIMEOUT = 300  # seconds — 週次要約は長文になるため余裕を持たせる

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# LLM ユーティリティ（server_librarian.py と同じパターン）
# ─────────────────────────────────────────────────────────────

def _call_gemini(prompt: str) -> str:
    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        return ""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={api_key}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return ""


def _call_ollama(prompt: str) -> str:
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except requests.exceptions.ConnectionError as e:
        logger.error("Ollama に接続できません: %s", e)
        return "[ERROR] Ollama に接続できませんでした。"
    except requests.exceptions.Timeout:
        logger.error("Ollama タイムアウト (%d 秒)", OLLAMA_TIMEOUT)
        return "[ERROR] Ollama がタイムアウトしました。"
    except Exception as e:
        return f"[ERROR] {e}"


def call_llm(prompt: str) -> str:
    """Gemini 優先 → Ollama フォールバック。"""
    result = _call_gemini(prompt)
    if result and not result.startswith("[ERROR]"):
        logger.info("Gemini で生成しました。")
        return result
    logger.info("Gemini 失敗 → Ollama にフォールバックします。")
    return _call_ollama(prompt)


# ─────────────────────────────────────────────────────────────
# ログ読み込み
# ─────────────────────────────────────────────────────────────

def _parse_frontmatter(text: str) -> dict[str, str]:
    """YAML frontmatter（---..---）を簡易パースして dict を返す。"""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = val.strip().strip('"')
    return result


def load_weekly_logs(days: int = 7) -> list[dict]:
    """直近 `days` 日間の obsidian_logs を読み込み、リストで返す。"""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    logs: list[dict] = []

    if not LOG_DIR.exists():
        logger.warning("obsidian_logs ディレクトリが見つかりません: %s", LOG_DIR)
        return logs

    for md_file in sorted(LOG_DIR.glob("Log_*.md")):
        # ファイル名から日付を抽出して高速フィルタ（YYYYMMDD）
        name_match = re.search(r"Log_(\d{8})_", md_file.name)
        if name_match:
            file_date = datetime.strptime(name_match.group(1), "%Y%m%d").replace(
                tzinfo=timezone.utc
            )
            if file_date < cutoff:
                continue

        text = md_file.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        logs.append(
            {
                "filename": md_file.name,
                "frontmatter": fm,
                "body": text,
            }
        )

    logger.info("直近 %d 日間のログ: %d 件", days, len(logs))
    return logs


# ─────────────────────────────────────────────────────────────
# プロンプト構築
# ─────────────────────────────────────────────────────────────

_PROMPT_TEMPLATE = """\
You are a senior quantitative trading analyst performing a weekly post-mortem review.
Your task is to read the trade logs below and produce a structured weekly reflection IN JAPANESE.

## 今週のトレードログ（{log_count} 件）

{log_summaries}

---

## 出力フォーマット（以下の Markdown を厳密に使うこと）

### 今週の全体サマリー
（勝率・損益傾向・市場環境・エージェント判断の傾向を 3〜5 文で総括）

### 主な勝因
- （箇条書き。成功したトレードに共通するシグナル・条件を列挙）

### 主な敗因
- （箇条書き。失敗・損失・見送りで後悔したパターンを列挙）

### 来週に向けたシステムパラメータ・戦略調整案（Rule for Future）
- （箇条書き。具体的な数値や条件変更を含む実用的な提案）

### 自律学習ループへの示唆
- （エージェントのプロンプト改善・閾値チューニング・新ルール候補など）
"""


def build_prompt(logs: list[dict]) -> str:
    lines: list[str] = []
    for log in logs:
        fm = log["frontmatter"]
        lines.append(
            f"- [{fm.get('date', '?')}] {fm.get('ticker', '?')} "
            f"{fm.get('action', '?')} → {fm.get('outcome', '?')} "
            f"({fm.get('profit_loss', '?')})"
        )
        # body からセクション（##）を要約として添付
        body_sections = re.findall(r"## .+?\n(.+?)(?=\n## |\Z)", log["body"], re.DOTALL)
        for section in body_sections[:2]:  # 最大2セクションだけ抜粋
            snippet = section.strip()[:200]
            if snippet:
                lines.append(f"  → {snippet}")

    log_summaries = "\n".join(lines)
    return _PROMPT_TEMPLATE.format(
        log_count=len(logs),
        log_summaries=log_summaries,
    )


# ─────────────────────────────────────────────────────────────
# 出力ファイル生成
# ─────────────────────────────────────────────────────────────

def build_output_markdown(llm_output: str, logs: list[dict], today: str) -> str:
    tickers = sorted({log["frontmatter"].get("ticker", "") for log in logs if log["frontmatter"].get("ticker")})
    ticker_links = ", ".join(f"[[tickers/{t}]]" for t in tickers)

    return f"""\
---
concept: weekly_reflection_{today.replace("-", "_")}
title: 週次反省会 {today}
last_updated: {today}
linked_tickers: [{", ".join(tickers)}]
log_count: {len(logs)}
---

# 週次反省会 {today}

> 対象期間: 直近7日間 | ログ件数: {len(logs)} | 関連銘柄: {ticker_links}

{llm_output}

---

*このページは `scripts/run_weekly_reflection.py` によって自動生成されました。*
"""


def save_reflection(content: str, today: str) -> Path:
    CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CONCEPTS_DIR / f"Weekly_Reflection_{today}.md"
    out_path.write_text(content, encoding="utf-8")
    logger.info("保存完了: %s", out_path)
    return out_path


# ─────────────────────────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="週次自動反省会スクリプト")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="LLM を呼び出さずプロンプトのみ標準出力に表示する",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="読み込む日数（デフォルト: 7）",
    )
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    logs = load_weekly_logs(days=args.days)

    if not logs:
        logger.warning("対象期間のログが 0 件です。終了します。")
        sys.exit(0)

    prompt = build_prompt(logs)

    if args.dry_run:
        print("=" * 60)
        print("【DRY-RUN】以下のプロンプトを LLM に送信する予定です:")
        print("=" * 60)
        print(prompt)
        print("=" * 60)
        print(f"対象ログ件数: {len(logs)}")
        sys.exit(0)

    logger.info("LLM に週次反省レポートを生成依頼します...")
    llm_output = call_llm(prompt)

    if llm_output.startswith("[ERROR]"):
        logger.error("LLM 生成に失敗しました: %s", llm_output)
        sys.exit(1)

    content = build_output_markdown(llm_output, logs, today)
    out_path = save_reflection(content, today)

    print(f"\n✓ 週次反省レポートを保存しました: {out_path}")
    print("\n--- 生成内容プレビュー（先頭500文字）---")
    print(content[:500])


if __name__ == "__main__":
    main()
