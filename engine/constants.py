"""engine/constants.py — システム全体で共有する定数"""

from __future__ import annotations

TARGET_TICKER        = "AAPL"
STRONG_BUY_SCORE     = 0.50    # 加重スコアの Strong Buy 閾値
BUY_QTY              = 1.0     # 発注数量（株）
SOCIAL_HYPE_THRESHOLD = 0.7    # このスコア以上を「高Hype」と判定
DAEMON_INTERVAL_SECS = 3_600   # 開場中の次回評価までのデフォルト間隔（1時間）
_W                   = 62      # ターミナル表示幅
_STRONG_BUY_LABEL    = "STRONG BUY"
_HOLD_LABEL          = "HOLD"

# 5 要素の合計ウェイト = 1.00
WEIGHTS: dict[str, float] = {
    "fundamental": 0.40,
    "technical":   0.20,
    "macro":       0.20,
    "news":        0.10,
    "social":      0.10,
}

SIGNAL_MAP: dict[str, float] = {
    "positive": +1.0,
    "neutral":   0.0,
    "negative": -1.0,
}

# モックモード用ダミー BBS データ（API トークン消費ゼロでフローテスト）
MOCK_BBS_DATA: dict[str, dict] = {
    "news_analysis": {
        "articles": [{
            "sentiment": "positive",
            "reason": "AAPL unveils revolutionary new AI features, driving positive market sentiment.",
            "title": "[MOCK] AAPL unveils revolutionary new AI features",
        }],
        "avg_sentiment_score": 1.0,
    },
    "technical_analysis": {
        "trend": "positive",
        "trend_reason": "MACD golden cross confirmed. RSI is at healthy 58.",
    },
    "macro_analysis": {
        "trend": "neutral",
        "trend_reason": "SPY is trading flat. VIX remains stable at 15.2.",
    },
    "fundamental_analysis": {
        "trend": "positive",
        "trend_reason": "Q2 earnings show 20% revenue growth and strong iPhone sales.",
        "analyses": [],
        "data_available": True,
    },
    "social_analysis": {
        "ticker": TARGET_TICKER,
        "sentiment": "POSITIVE",
        "hype_score": 0.8,
        "reason": (
            "Lots of rocket emojis 🚀 and YOLO mentions on Reddit r/wallstreetbets, "
            "but lacking fundamental discussion about P/E or earnings."
        ),
        "post_count": 5,
        "source": "mock_reddit_wsb",
    },
    "risk_analysis": {
        "ticker":                  TARGET_TICKER,
        "account_balance":         100_000.0,
        "current_price":           185.00,
        "atr":                     6.25,
        "stop_loss_price":         172.50,
        "stop_loss_pct":           6.76,
        "take_profit_price":       210.00,
        "take_profit_pct":         13.51,
        "risk_amount":             2000.0,
        "fixed_fractional_shares": 40,
        "kelly_shares":            35,
        "recommended_shares":      35,
        "reason": (
            "Volatility is moderate. Risking 2% of total account balance ($2,000). "
            "ATR=$6.25 × 2 = SL distance $12.50 / × 4 = TP distance $25.00 (RR 1:2). "
            "Fixed Fractional=40株, Kelly=35株 → 保守的な 35株を採用。"
        ),
    },
}
