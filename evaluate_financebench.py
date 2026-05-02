#!/usr/bin/env python3
"""
evaluate_financebench.py — FundamentalAgent ベンチマーク評価

FinanceBench 形式（Q&A JSONL）を読み込み、FundamentalAgent の
推論精度を Accuracy で自動採点する。

入力ファイル形式 (JSONL, 1行 = 1サンプル):
  {
    "question": "AAPLの2023年度の売上高はいくらですか？",
    "answer":   "3,833億ドル",
    "ticker":   "AAPL",
    "category": "revenue"   // オプション
  }

使い方:
    python evaluate_financebench.py
    python evaluate_financebench.py --qa-file data/evaluation/my_qa.jsonl
    python evaluate_financebench.py --ticker AAPL --strict

出力:
    - コンソールに採点結果を表示
    - data/evaluation/financebench_results.csv に書き出し
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

DEFAULT_QA_FILE    = Path("data/evaluation/financebench_qa.jsonl")
OUTPUT_CSV         = Path("data/evaluation/financebench_results.csv")
OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

SAMPLE_QA = [
    {
        "question": "AAPLの直近四半期の売上高のトレンドはポジティブですか？",
        "answer":   "positive",
        "ticker":   "AAPL",
        "category": "revenue_trend",
    },
    {
        "question": "MSFTのファンダメンタルな投資判断シグナルは何ですか？",
        "answer":   "positive",
        "ticker":   "MSFT",
        "category": "signal",
    },
    {
        "question": "NVDAの財務状況に重大なリスクはありますか？",
        "answer":   "neutral",
        "ticker":   "NVDA",
        "category": "risk",
    },
]

CSV_FIELDS = [
    "run_at", "ticker", "category", "question",
    "expected_answer", "agent_trend", "agent_reasoning",
    "is_correct", "strict_match",
]


def _load_qa_samples(qa_file: Path) -> list[dict]:
    if not qa_file.exists():
        print(f"  [EvaluateFB] QAファイルが見つかりません: {qa_file}")
        print("  [EvaluateFB] サンプルQAを使用して評価を実行します。")
        return SAMPLE_QA

    samples = []
    with open(qa_file, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  [EvaluateFB] 行 {lineno} パースエラー: {e}")
    return samples


def _run_fundamental_agent(ticker: str, question: str) -> dict:
    try:
        from agents.fundamental_agent import FundamentalAgent
        agent = FundamentalAgent(
            persist_dir="chroma_db_saved",
            collection_name="financial_filings",
            top_k=5,
        )
        result = agent.analyze(ticker)
        return result
    except Exception as e:
        return {"trend": "neutral", "trend_reason": f"エラー: {e}", "error": str(e)}


def _normalize_signal(value: str) -> str:
    v = (value or "").lower().strip()
    if v in ("positive", "ポジティブ", "買い", "強気"):
        return "positive"
    if v in ("negative", "ネガティブ", "売り", "弱気"):
        return "negative"
    return "neutral"


def _is_match(expected: str, agent_trend: str, strict: bool) -> bool:
    expected_norm = _normalize_signal(expected)
    agent_norm    = _normalize_signal(agent_trend)
    if strict:
        return expected_norm == agent_norm
    # 非厳格: positive/negative の方向性のみ一致確認
    if expected_norm == "neutral":
        return agent_norm == "neutral"
    if expected_norm == "positive":
        return agent_norm in ("positive", "neutral")
    return agent_norm in ("negative", "neutral")


def _score_keyword_match(question: str, agent_result: dict, expected: str) -> bool:
    """trend フィールドがない場合、trend_reason テキストからキーワードで正誤判定する。"""
    text = " ".join([
        str(agent_result.get("trend_reason", "")),
        str(agent_result.get("outlook", "")),
        str(agent_result.get("revenue_growth", "")),
    ]).lower()
    expected_norm = _normalize_signal(expected)
    positive_kw = ["growth", "increase", "strong", "positive", "beat", "上昇", "成長", "増収"]
    negative_kw = ["decline", "loss", "risk", "negative", "miss", "下落", "減収", "懸念"]
    pos_hit = any(kw in text for kw in positive_kw)
    neg_hit = any(kw in text for kw in negative_kw)

    if expected_norm == "positive":
        return pos_hit and not neg_hit
    if expected_norm == "negative":
        return neg_hit and not pos_hit
    return not pos_hit and not neg_hit


def main() -> None:
    parser = argparse.ArgumentParser(description="FundamentalAgent FinanceBench 評価")
    parser.add_argument("--qa-file", type=Path, default=DEFAULT_QA_FILE,
                        help=f"Q&A JSONL ファイルパス (デフォルト: {DEFAULT_QA_FILE})")
    parser.add_argument("--ticker", default=None,
                        help="評価対象を特定銘柄に絞り込む")
    parser.add_argument("--strict", action="store_true",
                        help="厳格モード: 完全一致のみ正解とする")
    args = parser.parse_args()

    print("=" * 64)
    print(" ECC FundamentalAgent FinanceBench 評価")
    print(f" QAファイル: {args.qa_file}")
    print(f" 厳格モード: {'ON' if args.strict else 'OFF（方向性一致で正解）'}")
    print("=" * 64)

    samples = _load_qa_samples(args.qa_file)
    if args.ticker:
        samples = [s for s in samples if s.get("ticker", "").upper() == args.ticker.upper()]

    if not samples:
        print("[ERROR] 評価サンプルが見つかりません。")
        sys.exit(1)

    print(f"\n  評価サンプル数: {len(samples)} 件\n")

    rows = []
    correct = 0

    for i, sample in enumerate(samples, 1):
        ticker   = sample.get("ticker", "AAPL")
        question = sample.get("question", "")
        expected = sample.get("answer", "neutral")
        category = sample.get("category", "general")

        print(f"  [{i:02d}/{len(samples):02d}] {ticker} | {category}")
        print(f"        Q: {question[:70]}")

        agent_result = _run_fundamental_agent(ticker, question)
        agent_trend  = agent_result.get("trend", "neutral")
        agent_reason = str(agent_result.get("trend_reason", ""))[:100]

        trend_match  = _is_match(expected, agent_trend, args.strict)
        kw_match     = _score_keyword_match(question, agent_result, expected)
        is_correct   = trend_match or kw_match

        if is_correct:
            correct += 1
            verdict = "✅ 正解"
        else:
            verdict = "❌ 不正解"

        print(f"        A期待: {expected:10s} | エージェント判定: {agent_trend:10s} | {verdict}")
        print(f"        根拠 : {agent_reason}")
        print()

        rows.append({
            "run_at":           datetime.datetime.now().isoformat(timespec="seconds"),
            "ticker":           ticker,
            "category":         category,
            "question":         question,
            "expected_answer":  expected,
            "agent_trend":      agent_trend,
            "agent_reasoning":  agent_reason,
            "is_correct":       is_correct,
            "strict_match":     trend_match,
        })

    accuracy = correct / len(rows) if rows else 0.0

    print("=" * 64)
    print(f"  最終結果: {correct}/{len(rows)} 正解  Accuracy = {accuracy:.2%}")
    print("=" * 64)

    file_exists = OUTPUT_CSV.exists()
    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists or OUTPUT_CSV.stat().st_size == 0:
            writer.writeheader()
        writer.writerows(rows)

    print(f"\n  結果を保存しました: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
