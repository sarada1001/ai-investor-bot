"""engine/constants.py — システム全体で共有する定数"""

from __future__ import annotations

TARGET_TICKER        = "AAPL"
STRONG_BUY_SCORE     = 0.60    # 加重スコアの Strong Buy 閾値
BUY_QTY              = 1.0     # 発注数量（株）
SOCIAL_HYPE_THRESHOLD = 0.7    # このスコア以上を「高Hype」と判定
DAEMON_INTERVAL_SECS = 3_600   # 開場中の次回評価までのデフォルト間隔（1時間）
_W                   = 62      # ターミナル表示幅
_STRONG_BUY_LABEL    = "STRONG BUY"
_HOLD_LABEL          = "HOLD"

# 6 要素の合計ウェイト = 1.00
# ※ LiquidityAgent 追加に伴い fundamental を -0.05, macro を -0.05 調整
WEIGHTS: dict[str, float] = {
    "fundamental": 0.35,
    "technical":   0.20,
    "macro":       0.15,
    "news":        0.10,
    "social":      0.10,
    "liquidity":   0.10,
}

SIGNAL_MAP: dict[str, float] = {
    "positive": +1.0,
    "neutral":   0.0,
    "negative": -1.0,
}

# ── Exit 判定 ──────────────────────────────────────────────
# 最大保有日数（営業日）。0 または None で TIME_EXIT を無効化する。
# バックテスト（run_backtest.py / run_agent_exam.py）と同じ値を使うこと。
MAX_HOLD_DAYS: int = 0    # ← 初期値は 0（無効）。有効化は別コミットで行う

# バックテスト / Agent Exam の `--max-hold-days` 既定値。
# 本番 ExitAgent は上の MAX_HOLD_DAYS を参照するため、両者は独立して動く。
# （本番を無効化したままバックテストを 10 営業日前提で回せるようにするための分離）
BACKTEST_MAX_HOLD_DAYS: int = 10

# ── 本番スクリーニング対象 Universe（100銘柄 / S&P500+NASDAQ100 主要銘柄）──────
# バックテスト検証済み (threshold=0.60, 勝率46.0%, 取引数189/3ヶ月 @ 39→100銘柄比 2.70x)
PRODUCTION_UNIVERSE: list[str] = [
    # Technology (20)
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMD",  "INTC", "QCOM", "AMAT", "LRCX",
    "ORCL", "CRM",  "ADBE", "CSCO",  "AVGO", "TXN",  "NOW",  "PANW", "PLTR", "FTNT",
    # Healthcare (15)
    "DXCM", "UNH",  "JNJ",  "ABT",  "LLY",  "PFE",  "AMGN", "MDT",  "ABBV", "MRK",
    "GILD", "VRTX", "ISRG", "SYK",  "ELV",
    # Industrial / Defense (10)
    "NOC",  "LMT",  "RTX",  "GE",   "CAT",  "BA",   "HON",  "UPS",  "DE",   "MMM",
    # Financials (12)
    "JPM",  "BAC",  "GS",   "V",    "MA",   "WFC",  "C",    "AXP",  "BLK",  "MS",
    "SCHW", "COF",
    # Consumer Discretionary (13)
    "AMZN", "TSLA", "HD",   "NKE",  "COST", "WMT",  "MCD",  "SBUX", "TGT",  "LOW",
    "BKNG", "ABNB", "DG",
    # Consumer Staples (8)
    "PG",   "KO",   "PEP",  "PM",   "MO",   "CL",   "KHC",  "GIS",
    # Energy (6)
    "XOM",  "CVX",  "OXY",  "COP",  "SLB",  "EOG",
    # Communication Services (7)
    "VZ",   "T",    "NFLX", "DIS",  "CMCSA","CHTR", "WBD",
    # Utilities / REIT (5)
    "NEE",  "AMT",  "DUK",  "PLD",  "O",
    # Materials (4)
    "LIN",  "APD",  "SHW",  "FCX",
]

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
    "liquidity_analysis": {
        "ticker":            TARGET_TICKER,
        "verbal_annotation": (
            "📥 大口資金は+$5.5百万の買い越し（純流入）。"
            "板はBid側に65%集中しており、下値サポートが厚く買い圧力が優勢。"
            "小口（個人）は-$70千の売り傾向。"
            "総合的に強い買い圧力優勢。機関資金の積極的な流入が見られる。"
        ),
        "ask_ratio":        0.35,
        "bid_ratio":        0.65,
        "net_large_inflow": 5_500_000.0,
        "net_small_inflow":  -70_000.0,
        "pressure":         "buy_dominant",
        "score":             0.65,
        "data_source":      "mock",
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
