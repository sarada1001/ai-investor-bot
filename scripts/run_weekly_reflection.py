#!/usr/bin/env python3
"""
run_weekly_reflection.py — 週次自動反省会スクリプト

直近7日間の obsidian_logs を読み込み、LLM でメタ教訓を生成し、
wiki/concepts/Weekly_Reflection_YYYY-MM-DD.md に保存する。

LLM のバックエンド選択（Ollama / Gemini）とモデル名は
`skills/llm_factory.py` に一元化してある。このスクリプトは
モデル名を一切持たず、独自のフォールバックも行わない。

Usage:
    python scripts/run_weekly_reflection.py            # 通常実行
    python scripts/run_weekly_reflection.py --dry-run  # プロンプトのみ表示
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────
# パス設定
#
# skills.llm_factory はインポート時に GEMINI_MODEL 等を読むため、
# sys.path 登録と .env ロードを **インポートより先** に済ませておく。
# （cron は cwd をプロジェクトルートにするが、sys.path に入るのは
#   スクリプトのある scripts/ なので明示登録が要る）
# ─────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

LOG_DIR      = PROJECT_ROOT / "data" / "knowledge_base" / "obsidian_logs"
CONCEPTS_DIR = PROJECT_ROOT / "data" / "knowledge_base" / "wiki" / "concepts"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# LLM ユーティリティ
#
# バックエンド（Ollama / Gemini）の選択・モデル名・API キーの扱いは
# すべて skills/llm_factory.py に委譲する。ここで REST を直接叩いたり
# モデル名を書いたりしないこと — それが 2026-02〜08 の半年間、
# 廃止モデル `gemini-2.0-flash` を呼び続けて無言に失敗した原因である。
# ─────────────────────────────────────────────────────────────

def _describe_exception(exc: BaseException) -> str:
    """例外から「原因特定に足る情報」を可能な限り引き出して 1 行にする。

    LLM クライアントは requests / httpx / google.api_core と層が違い、
    HTTP ステータスやレスポンスボディを載せる属性名が統一されていない。
    どれか 1 つでも拾えれば「モデル名が違う(404)」「キー無効(403)」
    「レート超過(429)」を切り分けられるため、総当たりで探す。
    """
    parts = [f"{type(exc).__name__}: {exc}"]

    for attr in ("status_code", "code"):
        status = getattr(exc, attr, None)
        if status is not None and not callable(status):
            parts.append(f"{attr}={status}")
            break

    response = getattr(exc, "response", None)
    if response is not None:
        body = getattr(response, "text", None)
        if body is None:
            body = str(response)
        parts.append(f"body={body[:500]}")

    # ラッパー例外（langchain 等）はメッセージが薄いことがあるため元例外も添える。
    # 逆に本文を二重に持つ場合もあるので長さは切り詰める。
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        parts.append(f"cause={type(cause).__name__}: {str(cause)[:300]}")

    return " | ".join(parts)


def call_llm(prompt: str) -> str:
    """llm_factory 経由で LLM を呼び出し、生成テキストを返す。

    .env の `FORCE_GEMINI` / `GEMINI_MODEL` / `OLLAMA_MODEL` /
    `DISABLE_GEMINI` がそのまま効く。失敗は握りつぶさず送出し、
    呼び出し元が原因つきでログに残す。

    Raises:
        Exception: LLM の生成・接続に失敗した場合（llm_factory 由来の例外を含む）
        RuntimeError: 応答が空だった場合
    """
    from skills.llm_factory import get_llm

    # json_mode=False 必須: 本スクリプトの出力は Markdown 文章であり、
    # format="json" を付けると Llama 側で文章が崩壊する（llm_factory 参照）。
    llm, source = get_llm(temperature=0)
    model = getattr(llm, "model", "unknown")
    logger.info("LLM 呼び出し: source=%s model=%s", source, model)

    response = llm.invoke(prompt)
    text = str(getattr(response, "content", "") or "").strip()
    if not text:
        raise RuntimeError(
            f"LLM が空応答を返しました (source={source}, model={model})"
        )

    logger.info("LLM 生成成功: source=%s model=%s 文字数=%d", source, model, len(text))
    return text


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
    try:
        llm_output = call_llm(prompt)
    except Exception as exc:
        # 「失敗しました」だけのログにしないこと。ここが無言だったせいで
        # 廃止モデルによる失敗が半年間気づかれなかった。
        logger.error(
            "LLM 生成に失敗しました — %s", _describe_exception(exc), exc_info=True,
        )
        sys.exit(1)

    content = build_output_markdown(llm_output, logs, today)
    out_path = save_reflection(content, today)

    print(f"\n✓ 週次反省レポートを保存しました: {out_path}")
    print("\n--- 生成内容プレビュー（先頭500文字）---")
    print(content[:500])


if __name__ == "__main__":
    main()
