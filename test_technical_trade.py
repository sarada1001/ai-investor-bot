"""
テクニカル・ドリブン・トレード 統合テスト
===========================================
① TechnicalAgent : AAPL のテクニカル分析（RSI/MACD/SMA25）→ LLM でトレンド判定 → BBS 書き込み
② ManagerAgent   : BBS を読み取り、トレンドに基づいて売買判断
③ 発注            : Positive → alpaca_trade で AAPL 1 株 BUY  /  それ以外 → HOLD
④ 出力            : エージェント分析レポート（BBS 会話履歴）+ 最終注文結果
"""

import sys
import json
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from main import BBS
import skills.technical_calc as _tech_mod
import skills.alpaca_trade as _alpaca_mod

TICKER = "AAPL"
PERIOD = "6mo"
SESSION_ID = datetime.datetime.now().strftime("test_tech_%Y%m%d_%H%M%S")

print(f"\n{'='*58}")
print(f"  テクニカル・ドリブン・トレード テスト")
print(f"  セッション : {SESSION_ID}")
print(f"  対象銘柄   : {TICKER}  /  期間 : {PERIOD}")
print(f"{'='*58}")

bbs = BBS(SESSION_ID)

# =========================================================
# ① TechnicalAgent — 指標計算 + LLM トレンド判定 → BBS 書き込み
# =========================================================
print(f"\n[① TechnicalAgent] {TICKER} のテクニカル分析を実行中...")

tech_result = _tech_mod.run(ticker=TICKER, period=PERIOD)

if tech_result.get("error"):
    print(f"  ERROR: {tech_result['error']}")
    sys.exit(1)

ind = tech_result["indicators"]
rsi_val     = ind["rsi"]["value"]
macd_data   = ind["macd"]
sma_data    = ind["sma25"]
trend       = tech_result.get("trend", "neutral")
trend_reason = tech_result.get("trend_reason", "")

print(f"\n  [ テクニカル指標 — 直近 {tech_result['latest_date']} ]")
print(f"  現在値        : ${tech_result['latest_price']:.2f}")
print(f"  RSI(14)       : {rsi_val}")
print(f"  MACD          : {macd_data['macd']:.4f}  /  Signal: {macd_data['signal']:.4f}  /  Histogram: {macd_data['histogram']:+.4f}")
print(f"  MACD トレンド : {macd_data['trend']}")
print(f"  SMA25         : ${sma_data['sma25']:.2f}  /  乖離率: {sma_data['diff_pct']:+.2f}%  ({sma_data['position']})")
print(f"\n  シグナル要約   : {tech_result['signal_summary']}")
print(f"\n  LLM トレンド判定 : {trend.upper()}")
print(f"  判定理由         : {trend_reason}")

bbs.write("TechnicalAgent", "technical_analysis", tech_result)

# =========================================================
# ② ManagerAgent — BBS 読み取り → 売買判断
# =========================================================
print(f"\n[② ManagerAgent] BBS を読み取り中...")

ta_data = bbs.read("technical_analysis") or {}
bbs_trend = ta_data.get("trend", "neutral").lower()
bbs_reason = ta_data.get("trend_reason", "")

print(f"  BBS から読み取ったトレンド: {bbs_trend.upper()}")
print(f"  根拠: {bbs_reason}")

# =========================================================
# ③ トレンドに基づいて発注 or HOLD
# =========================================================
order_result: dict | None = None
decision = ""
decision_reason = ""

if bbs_trend == "positive":
    decision = "BUY"
    decision_reason = f"テクニカルトレンド POSITIVE のため {TICKER} 1株を成行BUY"
    print(f"\n[③ 発注] {decision_reason}")
    try:
        order_result = _alpaca_mod.place_market_order(TICKER, 1, "buy")
        print(f"  発注成功 → OrderID: {order_result['order_id']}")
    except Exception as e:
        order_result = {"error": str(e)}
        print(f"  発注失敗: {e}")
else:
    decision = "HOLD"
    label = {"negative": "NEGATIVE", "neutral": "NEUTRAL"}.get(bbs_trend, bbs_trend.upper())
    decision_reason = f"テクニカルトレンド {label} のため見送り（HOLD）"
    print(f"\n[③ 見送り] {decision_reason}")

# ManagerAgent の判断を BBS に書き込む
trade_decision = {
    "decision": decision,
    "ticker": TICKER,
    "reason": decision_reason,
    "based_on_trend": bbs_trend,
    "signal_summary": ta_data.get("signal_summary", ""),
    "order": order_result,
}
bbs.write("ManagerAgent", "trade_decision", trade_decision)

# =========================================================
# ④ 分析レポート（BBS 会話履歴）と注文結果を出力
# =========================================================
print(f"\n{'='*58}")
print(f"  ④ エージェント分析レポート（BBS 会話履歴）")
print(f"{'='*58}")
print(bbs.to_text_summary())

print(f"\n{'='*58}")
print(f"  ④ 最終結果サマリー")
print(f"{'='*58}")
print(f"  銘柄       : {TICKER}")
print(f"  トレンド   : {bbs_trend.upper()}")
print(f"  決定       : {decision}")
print(f"  理由       : {decision_reason}")

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
    print(f"\n  [ 注文なし ]")

print(f"\n  BBS ログ: {bbs.path}")
print(f"{'='*58}\n")
