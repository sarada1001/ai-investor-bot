"""
ファンダメンタル・RAGドリブン 統合テスト
==========================================
① FundamentalAgent : RAG で AAPL（データなし確認）と エクサウィザーズ（実データ）を検索・分析 → BBS 書き込み
② ManagerAgent     : BBS を読み取り、最終判断を構築（Alpaca注文はスキップ、ターミナル出力のみ）
"""

import sys
import json
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from main import BBS
import skills.rag_search as _rag_mod
import yaml

PERSIST_DIR = "chroma_db_saved"
SESSION_ID = datetime.datetime.now().strftime("test_fa_%Y%m%d_%H%M%S")

# fundamental_agent.yaml からクエリを読み込む
with open(".agents/fundamental_agent.yaml", encoding="utf-8") as f:
    _agent_cfg = yaml.safe_load(f)

AAPL_QUERIES = _agent_cfg["params"]["aapl_queries"]
EXA_QUERIES = _agent_cfg["params"]["exawizards_queries"]

print(f"\n{'='*60}")
print(f"  ファンダメンタル・RAG 統合テスト")
print(f"  セッション : {SESSION_ID}")
print(f"  DB         : {PERSIST_DIR}")
print(f"{'='*60}")

bbs = BBS(SESSION_ID)

# =========================================================
# ① FundamentalAgent — AAPL 検索（データなし確認）
# =========================================================
print(f"\n[① FundamentalAgent] AAPL のファンダメンタル分析を試行中...")
print(f"  クエリ数: {len(AAPL_QUERIES)} 件")

aapl_result = _rag_mod.run(
    company="AAPL",
    queries=AAPL_QUERIES,
    persist_dir=PERSIST_DIR,
    top_k=6,
)

print(f"  データ有無   : {'あり' if aapl_result.get('data_available') else 'なし（DB に AAPL 資料未格納）'}")
print(f"  トレンド判定 : {aapl_result.get('trend', 'neutral').upper()}")
print(f"  判定理由     : {aapl_result.get('trend_reason', '')}")

bbs.write("FundamentalAgent", "fundamental_analysis_aapl", {
    "company": "AAPL",
    "data_available": aapl_result.get("data_available", False),
    "trend": aapl_result.get("trend", "neutral"),
    "trend_reason": aapl_result.get("trend_reason", ""),
    "queries_count": aapl_result.get("queries_count", 0),
})

# =========================================================
# ① FundamentalAgent — エクサウィザーズ 実データ分析
# =========================================================
print(f"\n[① FundamentalAgent] エクサウィザーズ のファンダメンタル分析を実行中...")
print(f"  クエリ数: {len(EXA_QUERIES)} 件")

exa_result = _rag_mod.run(
    company="エクサウィザーズ",
    queries=EXA_QUERIES,
    persist_dir=PERSIST_DIR,
    top_k=6,
)

print(f"\n  [ 各クエリの分析結果 ]")
for a in exa_result.get("analyses", []):
    hit = "✓" if a.get("company_found_in_context") else "△"
    print(f"\n  {hit} Q: {a['query']}")
    print(f"     A: {a['analysis'][:200].strip()}...")

print(f"\n  トレンド判定 : {exa_result.get('trend', 'neutral').upper()}")
print(f"  判定理由     : {exa_result.get('trend_reason', '')}")

# BBS には要約のみ書き込む（context_chunksは除外）
exa_bbs_entry = {
    "company": "エクサウィザーズ",
    "data_available": exa_result.get("data_available", False),
    "queries_count": exa_result.get("queries_count", 0),
    "analyses": [
        {
            "query": a["query"],
            "analysis": a["analysis"][:500],
            "company_found": a.get("company_found_in_context", False),
        }
        for a in exa_result.get("analyses", [])
    ],
    "trend": exa_result.get("trend", "neutral"),
    "trend_reason": exa_result.get("trend_reason", ""),
}
bbs.write("FundamentalAgent", "fundamental_analysis_exawizards", exa_bbs_entry)

# =========================================================
# ② ManagerAgent — BBS を読み取り最終判断を構築
# =========================================================
print(f"\n[② ManagerAgent] BBS を読み取り、最終判断を構築中...")

aapl_bbs = bbs.read("fundamental_analysis_aapl") or {}
exa_bbs  = bbs.read("fundamental_analysis_exawizards") or {}

# AAPL の判断（データなし → HOLD）
aapl_decision = "HOLD"
aapl_reason = (
    "DBにAAPL固有の決算資料がないため、ファンダメンタル根拠なし → HOLD"
    if not aapl_bbs.get("data_available")
    else f"AAPL ファンダメンタル: {aapl_bbs.get('trend', 'neutral').upper()}"
)

# エクサウィザーズの判断（実データに基づく）
exa_trend = exa_bbs.get("trend", "neutral").lower()
if exa_trend == "positive":
    exa_decision = "BUY 検討"
elif exa_trend == "negative":
    exa_decision = "SELL 検討"
else:
    exa_decision = "HOLD"

manager_judgment = {
    "AAPL": {
        "decision": aapl_decision,
        "reason": aapl_reason,
        "data_available": aapl_bbs.get("data_available", False),
        "fundamental_trend": aapl_bbs.get("trend", "neutral"),
        "note": "Alpaca注文スキップ（テスト実行）",
    },
    "エクサウィザーズ": {
        "decision": exa_decision,
        "reason": f"ファンダメンタル: {exa_trend.upper()} — {exa_bbs.get('trend_reason', '')}",
        "data_available": exa_bbs.get("data_available", False),
        "fundamental_trend": exa_trend,
        "note": "Alpaca注文スキップ（テスト実行）",
    },
}
bbs.write("ManagerAgent", "manager_judgment", manager_judgment)

# =========================================================
# ③ 分析レポートと最終判断を出力
# =========================================================
print(f"\n{'='*60}")
print(f"  ③ FundamentalAgent 分析レポート（BBS 会話履歴）")
print(f"{'='*60}")
print(bbs.to_text_summary())

print(f"\n{'='*60}")
print(f"  ③ ManagerAgent 最終判断サマリー")
print(f"{'='*60}")
print(f"\n  [ AAPL ]")
print(f"  データ有無   : {'あり' if manager_judgment['AAPL']['data_available'] else 'なし'}")
print(f"  ファンダメンタル: {manager_judgment['AAPL']['fundamental_trend'].upper()}")
print(f"  最終判断     : {manager_judgment['AAPL']['decision']}")
print(f"  理由         : {manager_judgment['AAPL']['reason']}")
print(f"  補足         : {manager_judgment['AAPL']['note']}")

print(f"\n  [ エクサウィザーズ ]")
print(f"  データ有無   : {'あり' if manager_judgment['エクサウィザーズ']['data_available'] else 'なし'}")
print(f"  ファンダメンタル: {manager_judgment['エクサウィザーズ']['fundamental_trend'].upper()}")
print(f"  最終判断     : {manager_judgment['エクサウィザーズ']['decision']}")
print(f"  理由         : {manager_judgment['エクサウィザーズ']['reason']}")
print(f"  補足         : {manager_judgment['エクサウィザーズ']['note']}")

print(f"\n  BBS ログ: {bbs.path}")
print(f"{'='*60}\n")
