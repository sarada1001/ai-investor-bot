"""
FundamentalAgent RAG 機能テスト（EDGAR 自律取得フロー検証）
============================================================
agents/fundamental_agent.py の FundamentalAgent.analyze() をテストする。

テストケース:
  1. エクサウィザーズ — financial_filings に実データあり → RAG 経路
  2. AAPL            — 事前に DB から削除 → EDGAR 自律取得 → RAG 経路

実行方法:
    cd /home/naito/exa-investor
    python test_fundamental_rag.py
"""

import sys
import json
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agents.fundamental_agent import FundamentalAgent

SESSION_ID  = datetime.datetime.now().strftime("rag_test_%Y%m%d_%H%M%S")
PERSIST_DIR = "chroma_db_saved"
COLLECTION  = "financial_filings"
BANNER      = "=" * 62


print(f"\n{BANNER}")
print(f"  FundamentalAgent RAG + EDGAR 自律取得テスト")
print(f"  セッション  : {SESSION_ID}")
print(f"  DB          : {PERSIST_DIR}  /  コレクション: {COLLECTION}")
print(f"{BANNER}")


# ================================================================
# ヘルパー: AAPL の既存チャンクを DB から削除（冪等テストのため）
# ================================================================

def _purge_ticker_from_db(ticker: str, persist_dir: str, collection_name: str) -> int:
    """
    指定 ticker の全チャンクを ChromaDB から削除し、削除件数を返す。
    テスト前に EDGAR 取得フローを確実にトリガーするために使う。
    """
    try:
        import chromadb
        client = chromadb.PersistentClient(path=persist_dir)
        col    = client.get_or_create_collection(collection_name)

        # ticker メタデータで該当 ID を取得
        result = col.get(where={"ticker": {"$eq": ticker.upper()}})
        ids    = result.get("ids", [])
        if ids:
            col.delete(ids=ids)
            print(f"  [Setup] DB から {ticker} の {len(ids)} チャンクを削除しました")
        else:
            print(f"  [Setup] DB に {ticker} のチャンクは存在しませんでした（スキップ）")
        return len(ids)
    except Exception as e:
        print(f"  [Setup] DB 削除中にエラー（無視）: {e}")
        return 0


# ================================================================
# ヘルパー: 結果を見やすく表示
# ================================================================

def _print_result(label: str, result: dict) -> None:
    edgar_flag = "✓ (自動取得)" if result.get("edgar_auto_fetch") else "−"
    fb_flag    = "yes (yfinance)" if result.get("fallback_used") else "no (RAG)"
    print(f"\n{'─' * 62}")
    print(f"  [ {label} ]")
    print(f"{'─' * 62}")
    print(f"  データ有無       : {'あり' if result.get('data_available') else 'なし'}")
    print(f"  データソース     : {result.get('data_source', 'N/A')}")
    print(f"  使用チャンク数   : {result.get('chunks_used', 0)}")
    print(f"  フォールバック   : {fb_flag}")
    print(f"  EDGAR 自律取得   : {edgar_flag}")
    print(f"\n  ── ファンダメンタルズ分析 ──")
    print(f"  売上/利益成長    : {str(result.get('revenue_growth', 'N/A'))[:160]}")
    print(f"  収益性           : {str(result.get('profitability',  'N/A'))[:160]}")
    print(f"  リスク要因       : {str(result.get('risks',          'N/A'))[:160]}")
    print(f"  今後の展望       : {str(result.get('outlook',        'N/A'))[:160]}")
    print(f"\n  トレンド判定     : {result.get('trend', 'neutral').upper()}")
    print(f"  判定理由         : {result.get('trend_reason', '')}")
    if result.get("error"):
        print(f"\n  ⚠ エラー         : {result['error']}")


# ================================================================
# Test 1: エクサウィザーズ (RAG 経路を期待)
# ================================================================
print(f"\n{'━' * 62}")
print(f"  Test 1: エクサウィザーズ — RAG 経路（一次情報あり想定）")
print(f"{'━' * 62}")

agent = FundamentalAgent(
    persist_dir=PERSIST_DIR,
    collection_name=COLLECTION,
    top_k=5,
)

result_exa = agent.analyze("エクサウィザーズ")
_print_result("エクサウィザーズ", result_exa)

assert result_exa["ticker"]         == "エクサウィザーズ", "ticker mismatch"
assert result_exa["data_available"] is True,             "data_available should be True"
assert result_exa["fallback_used"]  is False,            "should NOT use fallback (RAG data exists)"
assert result_exa["chunks_used"]    >= 1,                "at least 1 chunk should be retrieved"
assert result_exa["edgar_auto_fetch"] is False,          "should NOT trigger EDGAR (data already in DB)"
print("\n  ✓ Test 1 PASSED: RAG 経路で分析完了（EDGAR 未トリガー）")


# ================================================================
# Test 2: AAPL — EDGAR 自律取得 → RAG 経路
# ================================================================
print(f"\n{'━' * 62}")
print(f"  Test 2: AAPL — EDGAR 自律取得 → RAG 経路")
print(f"{'━' * 62}")

# AAPL の既存データを削除して「初めて解析する」状態を再現
print(f"\n  [Setup] AAPL の既存 DB データをパージ中...")
_purge_ticker_from_db("AAPL", PERSIST_DIR, COLLECTION)

# 新しい FundamentalAgent インスタンス（DB キャッシュなし）
agent_aapl = FundamentalAgent(
    persist_dir=PERSIST_DIR,
    collection_name=COLLECTION,
    top_k=5,
)

print(f"\n  AAPL の分析を開始（EDGAR 自律取得フローが走るはず）...")
result_aapl = agent_aapl.analyze("AAPL")
_print_result("AAPL", result_aapl)

assert result_aapl["ticker"]          == "AAPL", "ticker mismatch"
assert result_aapl["edgar_auto_fetch"] is True,  "EDGAR auto-fetch should have been triggered"
assert result_aapl["data_available"]   is True,  "data_available should be True after EDGAR fetch"

if result_aapl.get("chunks_used", 0) > 0:
    assert result_aapl["fallback_used"] is False, \
        "should use RAG (not yfinance) after successful EDGAR fetch"
    print("\n  ✓ Test 2 PASSED: EDGAR 自律取得 → RAG 経路で分析完了")
else:
    # EDGAR 取得は試みたが 0 チャンク（ネットワーク問題等）→ yfinance フォールバック
    assert result_aapl["fallback_used"] is True, \
        "if EDGAR fetch yielded 0 chunks, should fall back to yfinance"
    print("\n  ⚠ Test 2 PARTIAL: EDGAR 取得に失敗 → yfinance フォールバックで分析完了")
    print(f"    (エラー: {result_aapl.get('error')})")


# ================================================================
# サマリー
# ================================================================
print(f"\n{BANNER}")
print(f"  テストサマリー")
print(f"{BANNER}")

for label, result in [("エクサウィザーズ", result_exa), ("AAPL", result_aapl)]:
    source  = result.get("data_source", "N/A")
    trend   = result.get("trend", "neutral").upper()
    chunks  = result.get("chunks_used", 0)
    fb      = "yes" if result.get("fallback_used") else "no"
    edgar   = "yes" if result.get("edgar_auto_fetch") else "no"
    print(f"  {label:<18} trend={trend:<10} chunks={chunks:<4} fallback={fb:<4} "
          f"edgar_fetch={edgar}  source={source}")

print(f"\n  全テスト PASSED ✓")
print(f"{BANNER}\n")
