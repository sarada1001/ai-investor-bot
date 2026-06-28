"""
ニュース・ドリブン・トレード 統合テスト
============================================
① NewsAgent  : yfinance で AAPL の最新ニュース 1 件を取得 → センチメント判定 → BBS 書き込み
② ManagerAgent: BBS を読み取り、センチメントに基づいて売買を判断
③ 発注         : Positive → alpaca_trade で AAPL 1 株 BUY  /  それ以外 → 見送り
④ 出力         : BBS 会話履歴 + 注文結果 or 見送り理由
"""

import sys
import json
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from main import BBS
import skills.news_monitor as _news_mod
import skills.alpaca_trade as _alpaca_mod

TICKER = "AAPL"
SESSION_ID = datetime.datetime.now().strftime("test_news_%Y%m%d_%H%M%S")

print(f"\n{'='*55}")
print(f"  ニュース・ドリブン・トレード テスト")
print(f"  セッション: {SESSION_ID}")
print(f"  対象銘柄  : {TICKER}")
print(f"{'='*55}")

bbs = BBS(SESSION_ID)

# =========================================================
# ① NewsAgent — yfinance でニュースを取得してBBSに書き込む
# =========================================================
print(f"\n[① NewsAgent] {TICKER} の最新ニュースを取得中...")

news_result = _news_mod.run(ticker=TICKER, max_articles=1)
bbs.write("NewsAgent", "news_analysis", news_result)

articles = news_result.get("articles", [])
if articles:
    a = articles[0]
    print(f"  タイトル  : {a['title']}")
    print(f"  センチメント: {a['sentiment'].upper()}")
    print(f"  判定理由  : {a['reason']}")
else:
    print("  ニュースが取得できませんでした。")

# =========================================================
# ② ManagerAgent — BBS を読み取って判断
# =========================================================
print(f"\n[② ManagerAgent] BBS を読み取り中...")

news_data = bbs.read("news_analysis") or {}
fetched_articles = news_data.get("articles", [])

if not fetched_articles:
    sentiment = "neutral"
    top_article: dict = {}
    print("  BBS にニュースが見つかりませんでした。HOLD に設定。")
else:
    top_article = fetched_articles[0]
    sentiment = top_article.get("sentiment", "neutral").lower()
    print(f"  BBS から読み取ったセンチメント: {sentiment.upper()}")

# =========================================================
# ③ センチメントに基づいて発注 or 見送り
# =========================================================
order_result: dict | None = None
decision = ""
decision_reason = ""

if sentiment == "positive":
    decision = "BUY"
    decision_reason = f"センチメント POSITIVE のため {TICKER} 1株を成行BUY"
    print(f"\n[③ 発注] {decision_reason}")
    try:
        order_result = _alpaca_mod.place_market_order(TICKER, 1, "buy")
        print(f"  発注成功 → OrderID: {order_result['order_id']}")
    except Exception as e:
        order_result = {"error": str(e)}
        print(f"  発注失敗: {e}")
else:
    decision = "HOLD"
    decision_reason = (
        f"センチメント {sentiment.upper()} のため見送り"
        if sentiment in ("negative", "neutral")
        else "ニュース取得失敗のため見送り"
    )
    print(f"\n[③ 見送り] {decision_reason}")

# ManagerAgent の判断を BBS に書き込む
trade_decision = {
    "decision": decision,
    "ticker": TICKER,
    "reason": decision_reason,
    "based_on_sentiment": sentiment,
    "news_title": top_article.get("title", ""),
    "order": order_result,
}
bbs.write("ManagerAgent", "trade_decision", trade_decision)

# =========================================================
# ④ BBS 会話履歴と注文結果を出力
# =========================================================
print(f"\n{'='*55}")
print(f"  ④ BBS 会話履歴")
print(f"{'='*55}")
print(bbs.to_text_summary())

print(f"\n{'='*55}")
print(f"  ④ 最終結果サマリー")
print(f"{'='*55}")
print(f"  センチメント : {sentiment.upper()}")
print(f"  決定         : {decision}")
print(f"  理由         : {decision_reason}")

if order_result and not order_result.get("error"):
    print(f"\n  [ 注文詳細 ]")
    print(f"  OrderID    : {order_result.get('order_id')}")
    print(f"  銘柄       : {order_result.get('symbol')}")
    print(f"  数量       : {order_result.get('qty')} 株")
    print(f"  売買方向   : {order_result.get('side')}")
    print(f"  ステータス : {order_result.get('status')}")
    print(f"  送信日時   : {order_result.get('submitted_at')}")
elif order_result and order_result.get("error"):
    print(f"\n  [ 発注エラー ]\n  {order_result['error']}")
else:
    print(f"\n  [ 注文なし ] {decision_reason}")

print(f"\n  BBS ログ: {bbs.path}")
print(f"{'='*55}\n")
