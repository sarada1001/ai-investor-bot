"""
main.py — ECC スイングトレード自律エンジン（AAPL ターゲット）

実行フロー（ステージゲート方式）:
  Stage 1 : TechnicalAgent + NewsAgent + MacroAgent + SocialAgent（安価スキャン）
  Gate    : マクロ NEGATIVE → ブレーキ発動 HOLD
            技術・ニュース双方 NEUTRAL 以下 → HOLD
  Stage 2 : FundamentalAgent（Gate 通過時のみ: Multi-HyDE RAG + EDGAR 取得）
  Stage 3 : ManagerAgent（BBS レポートを総合評価 → Strong Buy のみ発注）
  Stage 4 : RiskAgent（STRONG BUY 時のみ: ポジションサイジング + ストップロス算出）

Strong Buy 条件（すべて満たすこと）:
  - 加重スコア ≥ 0.60  (FA×0.40, Tech×0.20, Macro×0.20, News×0.10, Social×0.10)
  - ファンダメンタルシグナル > 0 (positive 必須)
  - テクニカルシグナル ≥ 0    (negative 不可)
  - ニュースシグナル   ≥ 0    (negative 不可)
  - マクロシグナル    ≥ 0    (NEGATIVE 時は強制 HOLD)

Gate / マクロブレーキ:
  - MacroAgent が NEGATIVE → ブレーキ発動: FundamentalAgent スキップ → 即 HOLD
  - Tech & News 双方 NEUTRAL 以下 → Fundamental スキップ → 即 HOLD
  - ManagerAgent でも macro=NEGATIVE 時は安全弁として強制 HOLD

Social クロスバリデーション（ManagerAgent):
  - SNS センチメント POSITIVE でも hype_score ≥ 0.7 かつ FA・Tech の裏付けなし
    → 「根拠なき買い煽り」判定: Social スコアを −0.5 にペナルティ

RiskAgent（Stage 4）:
  - Fixed Fractional: 口座残高の 2% ÷ (ATR×2) = 最大許容株数
  - Kelly Criterion(簡略): 勝率55%・利益/損失比1.5 → Kelly分率×口座÷株価
  - 両者の小さい方を recommended_shares とし、損切り価格と共に BBS に記録
"""

from __future__ import annotations

import os
import sys
import json
import time
import datetime
import re
import warnings
import yaml
import requests
from pathlib import Path
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

warnings.filterwarnings("ignore")
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

import skills.news_monitor            as _news_mod
import skills.technical_calc          as _tech_mod
import skills.alpaca_trade             as _alpaca_mod
import skills.macro_monitor            as _macro_mod
import skills.social_monitor           as _social_mod
import skills.risk_calculator          as _risk_mod
import skills.training_data_collector  as _training_mod
import skills.screener                 as _screener_mod
from agents.fundamental_agent import FundamentalAgent as _FundamentalAgentImpl
from agents.exit_agent import ExitAgent as _ExitAgentImpl, add_position as _portfolio_add
from tools.auto_logger import ObsidianLogger as _ObsidianLogger
from tools.alpaca_client import AlpacaClient as _AlpacaClient, PORTFOLIO_PATH as _PORTFOLIO_PATH
from tools.critic_agent import CriticAgent as _CriticAgentImpl

# =========================================================
# 定数
# =========================================================

TARGET_TICKER    = "AAPL"
STRONG_BUY_SCORE = 0.60   # 加重スコアの Strong Buy 閾値
BUY_QTY          = 1.0    # 発注数量（株）

# 5 要素の合計ウェイト = 1.00
WEIGHTS: dict[str, float] = {
    "fundamental": 0.40,
    "technical":   0.20,
    "macro":       0.20,
    "news":        0.10,
    "social":      0.10,
}

SOCIAL_HYPE_THRESHOLD = 0.7   # このスコア以上を「高Hype」と判定

SIGNAL_MAP: dict[str, float] = {
    "positive": +1.0,
    "neutral":   0.0,
    "negative": -1.0,
}

# モックモード用ダミーBBSデータ（APIトークン消費ゼロでフローテスト）
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
        "stop_loss_price":         172.50,   # current_price - ATR×2
        "stop_loss_pct":           6.76,
        "take_profit_price":       210.00,   # current_price + ATR×4 (RR 1:2)
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

# =========================================================
# BBS (Bulletin Board System) — 共有テキストメモリ
# =========================================================

BBS_DIR = Path("bbs")
BBS_DIR.mkdir(exist_ok=True)


class BBS:
    """テキストベースの共有メモリ。エージェントが順番に書き込む。"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.path = BBS_DIR / f"{session_id}.json"
        self._data: dict = {
            "session_id": session_id,
            "created_at": datetime.datetime.now().isoformat(),
            "entries": [],
        }
        self._save()

    def write(self, agent_name: str, key: str, data: dict | str) -> None:
        entry = {
            "agent": agent_name,
            "key": key,
            "timestamp": datetime.datetime.now().isoformat(),
            "data": data,
        }
        self._data["entries"].append(entry)
        self._save()
        _log(f"[BBS] {agent_name} → '{key}' 書き込み完了")

    def read(self, key: str) -> dict | str | None:
        for entry in reversed(self._data["entries"]):
            if entry["key"] == key:
                return entry["data"]
        return None

    def read_all(self) -> list[dict]:
        return self._data["entries"]

    def to_text_summary(self) -> str:
        lines = [f"=== BBS セッション {self.session_id} ==="]
        for e in self._data["entries"]:
            lines.append(f"\n--- [{e['agent']}] key={e['key']} ---")
            lines.append(json.dumps(e["data"], ensure_ascii=False, indent=2))
        return "\n".join(lines)

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)


# =========================================================
# ログ出力ヘルパー
# =========================================================

_W = 62  # ターミナル表示幅


def _sep() -> None:
    print(f"│  {'┄' * (_W - 4)}")


def _log(msg: str) -> None:
    print(f"│  {msg}")


def _phase_header(tag: str, name: str) -> None:
    title = f" {tag}: {name} "
    line  = title.center(_W - 2, "─")
    print(f"\n┌{line}┐")


def _phase_footer() -> None:
    print(f"└{'─' * _W}┘")


def _stage_header(n: int, title: str) -> None:
    label = f"  ◆ Stage {n}: {title}"
    print(f"\n{'━' * (_W + 2)}")
    print(label)
    print(f"{'━' * (_W + 2)}")


def _mock_banner(sub: str = "") -> None:
    bar = "█" * (_W + 2)
    msg = "⚠️  [MOCK MODE]  トークン消費0でテスト実行中  ⚠️"
    print(f"\n{bar}")
    print(f"  {msg}")
    if sub:
        print(f"  {sub}")
    print(f"{bar}\n")


def _hybrid_banner(sub: str = "") -> None:
    bar = "▓" * (_W + 2)
    msg = "🔄  [HYBRID MODE]  リアル市場データ / 発注スキップ  🔄"
    print(f"\n{bar}")
    print(f"  {msg}")
    if sub:
        print(f"  {sub}")
    print(f"{bar}\n")


def _main_header(ticker: str, session_id: str) -> None:
    print(f"\n╔{'═' * _W}╗")
    print(f"║  ECC スイングトレード自律エンジン  [{ticker}]".ljust(_W + 1) + "║")
    print(f"║  セッション: {session_id}".ljust(_W + 1) + "║")
    print(f"╚{'═' * _W}╝")


def _decision_box(lines: list[str]) -> None:
    print(f"\n╔{'═' * _W}╗")
    for line in lines:
        print(f"║  {line}".ljust(_W + 1) + "║")
    print(f"╚{'═' * _W}╝")


def _load_agent_config(agent_name: str) -> dict:
    path = Path(".agents") / f"{agent_name}.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# =========================================================
# LINE 通知（オプション）
# =========================================================

LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN", "")
LINE_USER_ID      = os.getenv("LINE_USER_ID", "")


def send_line_message(text: str) -> None:
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        _log("[LINE] スキップ: LINE_ACCESS_TOKEN または LINE_USER_ID が未設定 (.env を確認)")
        return
    try:
        requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
            },
            data=json.dumps({"to": LINE_USER_ID,
                             "messages": [{"type": "text", "text": text}]}),
            timeout=10,
        )
    except Exception as e:
        _log(f"[LINE] 送信失敗: {e}")


# =========================================================
# Stage 1-A — TechnicalAgent
# =========================================================

class TechnicalAgent:
    NAME = "TechnicalAgent"

    def __init__(self, bbs: BBS):
        self.bbs = bbs

    def run(self, ticker: str = TARGET_TICKER, phase_tag: str = "S1-1/3") -> dict:
        _phase_header(phase_tag, self.NAME)
        _log(f"{ticker} のテクニカル指標を計算中 (期間: 6ヶ月)...")
        _sep()

        try:
            result = _tech_mod.analyze_ticker(ticker, period="6mo")
        except Exception as e:
            result = {"ticker": ticker, "error": str(e)}
            _log(f"エラー: {e}")
            self.bbs.write(self.NAME, "technical_analysis", result)
            _phase_footer()
            return result

        if result.get("error"):
            _log(f"取得エラー: {result['error']}")
            self.bbs.write(self.NAME, "technical_analysis", result)
            _phase_footer()
            return result

        ind  = result.get("indicators", {})
        rsi  = ind.get("rsi",  {})
        macd = ind.get("macd", {})
        sma  = ind.get("sma25", {})

        _log(f"最新価格   : ${result.get('latest_price', 0):.2f}  ({result.get('latest_date', '')})")
        _log(f"RSI(14)    : {rsi.get('value', '-')}")
        _log(f"MACD       : {macd.get('trend', '-')}  (histogram={macd.get('histogram', 0):+.4f})")
        _log(f"SMA25      : {sma.get('sma25', '-'):.2f}  ({sma.get('position', '-')} / {sma.get('diff_pct', 0):+.2f}%)")
        _sep()
        _log(f"シグナルサマリー: {result.get('signal_summary', '')}")
        _sep()
        trend  = result.get("trend", "neutral")
        t_icon = {"positive": "📈", "negative": "📉", "neutral": "➡️"}.get(trend, "❓")
        _log(f"テクニカルトレンド判定: {t_icon} {trend.upper()}")
        _log(f"根拠: {result.get('trend_reason', '')[:80]}")

        self.bbs.write(self.NAME, "technical_analysis", result)
        _phase_footer()
        return result


# =========================================================
# Stage 1-B — NewsAgent
# =========================================================

class NewsAgent:
    NAME = "NewsAgent"

    def __init__(self, bbs: BBS):
        self.bbs = bbs

    def run(self, ticker: str = TARGET_TICKER, phase_tag: str = "S1-2/3") -> dict:
        _phase_header(phase_tag, self.NAME)
        _log(f"{ticker} の最新ニュースを取得中 (yfinance)...")
        _sep()

        try:
            result = _news_mod.fetch_ticker_news(ticker, max_articles=3)
        except Exception as e:
            result = {"ticker": ticker, "articles": [], "new_count": 0, "error": str(e)}
            _log(f"エラー: {e}")

        articles = result.get("articles", [])
        _log(f"取得完了: {len(articles)} 件")
        _sep()

        sentiment_scores: list[float] = []
        for a in articles:
            s = a.get("sentiment", "neutral")
            sentiment_scores.append(SIGNAL_MAP.get(s, 0.0))
            icon = {"positive": "📈", "negative": "📉", "neutral": "➡️"}.get(s, "❓")
            title_short = a.get("title", "")[:55]
            _log(f"{icon} [{s:8s}] {title_short}...")
            _log(f"           理由: {a.get('reason','')[:60]}")

        avg_signal = round(sum(sentiment_scores) / len(sentiment_scores), 4) if sentiment_scores else 0.0
        avg_label  = "強気" if avg_signal > 0.3 else "弱気" if avg_signal < -0.3 else "中立"
        _sep()
        _log(f"センチメント平均スコア: {avg_signal:+.2f}  ({avg_label})")

        for a in articles:
            a.setdefault("company", ticker)
        result["avg_sentiment_score"] = avg_signal

        self.bbs.write(self.NAME, "news_analysis", result)
        _phase_footer()
        return result


# =========================================================
# Stage 1-C — MacroAgent
# =========================================================

class MacroAgent:
    NAME = "MacroAgent"

    def __init__(self, bbs: BBS):
        self.bbs = bbs
        self._cfg = _load_agent_config("macro_agent")

    def run(self, phase_tag: str = "S1-3/3") -> dict:
        _phase_header(phase_tag, self.NAME)
        period = self._cfg.get("params", {}).get("period", "1mo")
        _log(f"市場全体の指標を取得中 (SPY / ^VIX, period={period})...")
        _sep()

        try:
            result = _macro_mod.run(period=period)
        except Exception as e:
            result = {
                "spy": {}, "vix": {},
                "signal_summary": f"取得エラー: {e}",
                "trend": "neutral",
                "trend_reason": f"マクロデータ取得失敗: {e}",
                "error": str(e),
            }
            _log(f"エラー: {e}")

        if result.get("error"):
            _log(f"データ取得エラー: {result['error']} → NEUTRAL で継続")
        else:
            spy = result.get("spy", {})
            vix = result.get("vix", {})
            _log(f"SPY  価格  : ${spy.get('latest_price', '-')}")
            _log(f"SPY  SMA乖離: {spy.get('diff_pct', 0):+.2f}%  ({spy.get('position', '-')})")
            _log(f"SPY  5日R  : {spy.get('return_5d_pct', 0):+.2f}%")
            _sep()
            _log(f"VIX  現在値: {vix.get('latest', '-'):.1f}  ({vix.get('level', '-')})")
            _log(f"VIX  5日平均: {vix.get('avg_5d', '-'):.1f}  トレンド: {vix.get('trend', '-')}")
            _sep()
            _log(f"サマリー: {result.get('signal_summary', '')}")

        _sep()
        trend  = result.get("trend", "neutral")
        t_icon = {"positive": "📈", "negative": "📉", "neutral": "➡️"}.get(trend, "❓")
        brake  = "  ← ⚠ ブレーキ発動！" if trend == "negative" else ""
        _log(f"マクロ環境判定: {t_icon} {trend.upper()}{brake}")
        _log(f"根拠: {result.get('trend_reason', '')[:80]}")

        self.bbs.write(self.NAME, "macro_analysis", result)
        _phase_footer()
        return result


# =========================================================
# Stage 1-D — SocialAgent
# =========================================================

class SocialAgent:
    NAME = "SocialAgent"

    def __init__(self, bbs: BBS):
        self.bbs = bbs

    def run(self, ticker: str = TARGET_TICKER, phase_tag: str = "S1-4/4") -> dict:
        _phase_header(phase_tag, self.NAME)
        _log(f"{ticker} のSNSセンチメントを分析中 (Reddit r/wallstreetbets 風モック)...")
        _sep()

        try:
            result = _social_mod.fetch_social_sentiment(ticker, hype_mode=True)
        except Exception as e:
            result = {
                "ticker": ticker, "sentiment": "NEUTRAL", "hype_score": 0.5,
                "reason": f"取得エラー: {e}", "error": str(e),
            }
            _log(f"エラー: {e}")

        sentiment  = result.get("sentiment", "NEUTRAL")
        hype_score = result.get("hype_score", 0.0)
        reason     = result.get("reason", "")
        source     = result.get("source", "")
        posts      = result.get("posts_preview", [])

        s_icon   = {"POSITIVE": "📈", "NEGATIVE": "📉", "NEUTRAL": "➡️"}.get(sentiment, "❓")
        filled   = int(hype_score * 10)
        hype_bar = "█" * filled + "░" * (10 - filled)

        _log(f"データソース   : {source}  (投稿数: {result.get('post_count', 0)}件)")
        if posts:
            _log(f"投稿サンプル   : {posts[0][:65]}...")
        _sep()
        _log(f"センチメント判定: {s_icon} {sentiment}")
        _log(f"買い煽りスコア  : [{hype_bar}] {hype_score:.2f}  "
             f"{'⚠️  高Hype警戒 (FA/Tech裏付け必要)' if hype_score >= SOCIAL_HYPE_THRESHOLD else '✅ 正常範囲'}")
        _sep()
        _log(f"判定根拠: {reason[:80]}")

        self.bbs.write(self.NAME, "social_analysis", result)
        _phase_footer()
        return result


# =========================================================
# Stage 0 — ExitAgent（Selling Loop）
# =========================================================

class ExitAgent:
    """
    Stage 0 ラッパー: agents/exit_agent.py の _ExitAgentImpl に処理を委譲し、
    結果を BBS に書き込んで画面表示する。
    """

    NAME = "ExitAgent"

    def __init__(self, bbs: BBS) -> None:
        self.bbs = bbs

    def run(
        self,
        mock_mode: bool    = False,
        alpaca_client      = None,
        phase_tag: str     = "S0",
    ) -> list[dict]:
        _phase_header(phase_tag, self.NAME)
        _log("portfolio.json を読み込み、保有銘柄の売却判断を実行中...")
        if mock_mode:
            _log("  ⚠ mock_mode=True: LLM による thesis 判定をスキップ / Alpaca 注文なし")
        elif alpaca_client is None:
            _log("  ⚠ dry_run: Alpaca 注文なし（評価のみ）")
        _sep()

        impl    = _ExitAgentImpl(self.bbs)
        results = impl.run(mock_mode=mock_mode, alpaca_client=alpaca_client)

        if not results:
            _log("保有ポジションなし — Selling Loop をスキップ")
        else:
            sell_cnt = sum(1 for r in results if r["action"] == "SELL")
            hold_cnt = sum(1 for r in results if r["action"] == "HOLD")
            _log(f"評価完了: {len(results)} 件  売却判定: {sell_cnt} 件  継続保有: {hold_cnt} 件")
            _sep()
            for r in results:
                icon = {"SELL": "🔴", "HOLD": "🟡"}.get(r["action"], "❓")
                _log(f"  {icon} {r['action']:4s} | {r['ticker']:6s} | "
                     f"P&L: {r['pnl_pct']:+.2f}%  [{r['exit_type']}]")
                _log(f"       理由: {r['reason'][:65]}")
                if r["action"] == "SELL":
                    _log(f"       買値: ${float(r['entry_price']):.2f} → "
                         f"現在: ${r['current_price']:.2f}")

        _phase_footer()
        return results


# =========================================================
# Stage 2 — FundamentalAgent
# =========================================================

class FundamentalAgent:
    """
    Stage 2 オーケストレーター。
    agents/fundamental_agent.py の FundamentalAgentImpl に処理を委譲し、
    結果を BBS に書き込む。

    allow_edgar_fetch フラグ
    -------------------------
    True  (default) : EDGAR 自律取得を許可。ChromaDB に該当銘柄のデータが
                      なければ 10-Q/10-K を自動ダウンロードして DB に格納し、
                      再度 RAG 検索を実行する（自己修復フロー）。
    False           : EDGAR 呼び出しをスキップ。ChromaDB ヒット時は RAG 分析、
                      ヒットなし時は yfinance フォールバック（mock_mode 用）。
    """

    NAME = "FundamentalAgent"

    def __init__(self, bbs: BBS):
        self.bbs = bbs

    def run(
        self,
        ticker: str            = TARGET_TICKER,
        phase_tag: str         = "S2",
        allow_edgar_fetch: bool = True,
    ) -> dict:
        _phase_header(phase_tag, self.NAME)
        _log(f"{ticker} のファンダメンタルズ分析を開始 (RAG + 自己修復フロー)...")
        if not allow_edgar_fetch:
            _log("  ⚠ EDGAR 自律取得は無効化されています (mock_mode=True)")
        _sep()

        fa = _FundamentalAgentImpl(
            persist_dir="chroma_db_saved",
            collection_name="financial_filings",
            top_k=5,
        )

        # mock_mode では EDGAR 呼び出しを禁止し、ChromaDB ヒットなし時は
        # そのまま yfinance フォールバックへ流す
        if not allow_edgar_fetch:
            fa._fetch_from_edgar = lambda t: {          # type: ignore[method-assign]
                "ticker": t, "form": "", "chunks_added": 0,
                "error": "EDGAR fetch disabled (allow_edgar_fetch=False)",
            }

        result = fa.analyze(ticker)

        # ── 結果を表示 ────────────────────────────────────────────
        _sep()
        edgar_note = "  ✓ EDGAR 自律取得済" if result.get("edgar_auto_fetch") else ""
        _log(f"データソース : {result.get('data_source', '不明')}{edgar_note}")
        _log(f"使用チャンク : {result.get('chunks_used', 0)} 件  "
             f"({'yfinance FB' if result.get('fallback_used') else 'RAG 一次情報'})")
        _sep()
        for label, key in [
            ("売上/成長性", "revenue_growth"),
            ("収益性    ", "profitability"),
            ("リスク要因", "risks"),
            ("今後の展望", "outlook"),
        ]:
            val = str(result.get(key, "N/A"))[:90].replace("\n", " ")
            _log(f"{label}: {val}")
        _sep()
        trend  = result.get("trend", "neutral")
        t_icon = {"positive": "📈", "negative": "📉", "neutral": "➡️"}.get(trend, "❓")
        _log(f"ファンダメンタルトレンド判定: {t_icon} {trend.upper()}")
        _log(f"根拠: {result.get('trend_reason', '')[:80]}")

        self.bbs.write(self.NAME, "fundamental_analysis", result)
        _phase_footer()
        return result


# =========================================================
# Stage 3 — ManagerAgent + Gate ヘルパー
# =========================================================

_STRONG_BUY_LABEL = "STRONG BUY"
_HOLD_LABEL       = "HOLD"


def _extract_news_signal(news_data: dict, ticker: str) -> tuple[float, str]:
    """ニュース記事からティッカー一致記事を抽出してシグナルを計算する。"""
    articles = news_data.get("articles", [])
    target_arts = [
        a for a in articles
        if a.get("ticker", "").upper() == ticker.upper()
        or a.get("company", "").upper() == ticker.upper()
    ]
    if not target_arts:
        target_arts = articles
    if not target_arts:
        return 0.0, "ニュースなし"

    scores = [SIGNAL_MAP.get(a.get("sentiment", "neutral"), 0.0) for a in target_arts]
    avg    = round(sum(scores) / len(scores), 4)
    parts  = [a.get("sentiment", "?") for a in target_arts[:3]]
    return avg, " / ".join(parts)


def _trend_to_signal(trend_str: str) -> float:
    return SIGNAL_MAP.get((trend_str or "neutral").lower(), 0.0)


def _gate_check(bbs: BBS, ticker: str) -> dict:
    """
    Stage 1 の BBS データを読み、FundamentalAgent をスキップするか判定する。

    優先度:
      1. Macro NEGATIVE → ブレーキ発動（即 HOLD）
      2. Tech ≤ 0 AND News ≤ 0 → 通常 Gate（コスト節約 HOLD）
    """
    tech_data  = bbs.read("technical_analysis") or {}
    news_data  = bbs.read("news_analysis")       or {}
    macro_data = bbs.read("macro_analysis")      or {}

    tech_sig             = _trend_to_signal(tech_data.get("trend", "neutral"))
    news_sig, _news_desc = _extract_news_signal(news_data, ticker)
    macro_sig            = _trend_to_signal(macro_data.get("trend", "neutral"))

    macro_brake    = macro_sig < 0.0   # NEGATIVE → 強制 HOLD
    signals_flat   = tech_sig <= 0.0 and news_sig <= 0.0

    skip = macro_brake or signals_flat

    if macro_brake:
        reason = "マクロ環境 NEGATIVE → 市場全体がリスクオフ（ブレーキ発動）"
    elif signals_flat:
        reason = "Tech・News 両シグナルが NEUTRAL 以下 → Fundamental スキップ（コスト節約）"
    else:
        reason = "少なくとも 1 シグナルが POSITIVE → Stage 2 (Fundamental) へ進む"

    return {
        "tech_signal":      tech_sig,
        "news_signal":      news_sig,
        "macro_signal":     macro_sig,
        "macro_brake":      macro_brake,
        "skip_fundamental": skip,
        "reason":           reason,
    }


def _gate_display(gate: dict) -> None:
    """Gate の判定結果と次のルーティングをボックス表示する。"""
    skip       = gate["skip_fundamental"]
    tech_sig   = gate["tech_signal"]
    news_sig   = gate["news_signal"]
    macro_sig  = gate["macro_signal"]
    macro_brake = gate["macro_brake"]

    def _icon(v: float) -> str:
        return "📈" if v > 0 else "📉" if v < 0 else "➡️"

    def _label(v: float) -> str:
        return "positive" if v > 0 else "negative" if v < 0 else "neutral"

    macro_note = "  ← ⚠ ブレーキ発動！" if macro_brake else ""
    verdict    = (
        "⛔ SKIP  → HOLD で終了（Fundamental をスキップ）"
        if skip else
        "✅ PASS  → Stage 2 (FundamentalAgent) へ進む"
    )

    print(f"\n┌{'─' * _W}┐")
    print(f"│  {'◇ Gate チェック':^{_W - 4}}")
    print(f"│  {'┄' * (_W - 4)}")
    print(f"│  {_icon(tech_sig)} TechnicalAgent : {tech_sig:+.2f}  ({_label(tech_sig)})")
    print(f"│  {_icon(news_sig)} NewsAgent      : {news_sig:+.2f}  ({_label(news_sig)})")
    print(f"│  {_icon(macro_sig)} MacroAgent     : {macro_sig:+.2f}  ({_label(macro_sig)}){macro_note}")
    print(f"│  {'┄' * (_W - 4)}")
    print(f"│  判定: {verdict}")
    print(f"└{'─' * _W}┘")


class ManagerAgent:
    NAME = "ManagerAgent"

    def __init__(self, bbs: BBS):
        self.bbs  = bbs
        self._llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0)

    def _build_rationale(
        self,
        ticker: str,
        decision: str,
        score: float,
        sigs: dict[str, float],
        reasons: dict[str, str],
        wiki_context: str = "",
    ) -> str:
        history_block = (
            f"\n\n【過去実績（参考）】\n{wiki_context[:600]}\n"
            if wiki_context else ""
        )
        prompt = (
            f"スイングトレード分析結果を投資家向けに200文字以内で要約してください。"
            f"過去実績がある場合は現在の判断との整合性も言及してください。\n"
            f"{history_block}\n"
            f"銘柄: {ticker}\n"
            f"最終判断: {decision}  (加重スコア: {score:+.3f})\n"
            f"マクロ環境       ({WEIGHTS['macro']:.0%}): {sigs['macro']:+.2f} — {reasons['macro']}\n"
            f"ニュース         ({WEIGHTS['news']:.0%}): {sigs['news']:+.2f} — {reasons['news']}\n"
            f"ファンダメンタル ({WEIGHTS['fundamental']:.0%}): "
            f"{sigs['fundamental']:+.2f} — {reasons['fundamental']}\n"
            f"テクニカル       ({WEIGHTS['technical']:.0%}): {sigs['technical']:+.2f} — {reasons['technical']}\n"
            f"SNSセンチメント  ({WEIGHTS['social']:.0%}): {sigs['social']:+.2f} — {reasons['social']}"
        )
        try:
            return self._llm.invoke(prompt).content.strip()[:200]
        except Exception as e:
            return f"LLM要約エラー: {e}"

    def run(
        self,
        ticker: str                       = TARGET_TICKER,
        dry_run: bool                     = False,
        phase_tag: str                    = "S3",
        mock_mode: bool                   = False,
        effective_weights: dict | None    = None,
        excluded_keys: list[str] | None   = None,
    ) -> dict:
        _phase_header(phase_tag, self.NAME)
        excluded_keys = excluded_keys or []
        w = effective_weights if effective_weights else WEIGHTS
        _log("BBS から5エージェントのレポートを読み込み中...")
        if excluded_keys:
            _log(f"  [アブレーション] 除外: {excluded_keys}  有効ウェイト: { {k: f'{v:.3f}' for k, v in w.items()} }")
        _sep()

        news_data   = self.bbs.read("news_analysis")        or {}
        tech_data   = self.bbs.read("technical_analysis")   or {}
        fa_data     = self.bbs.read("fundamental_analysis") or {}
        macro_data  = self.bbs.read("macro_analysis")       or {}
        social_data = self.bbs.read("social_analysis")      or {}

        # ── シグナル抽出 ──────────────────────────────────────────
        news_sig, news_reason   = _extract_news_signal(news_data, ticker)
        tech_sig                = _trend_to_signal(tech_data.get("trend", "neutral"))
        tech_reason             = tech_data.get("trend_reason", "データなし")
        fa_sig                  = _trend_to_signal(fa_data.get("trend", "neutral"))
        fa_reason               = fa_data.get("trend_reason", "データなし")
        macro_sig               = _trend_to_signal(macro_data.get("trend", "neutral"))
        macro_reason            = macro_data.get("trend_reason", "データなし")

        # ── Social シグナル + Hype クロスバリデーション ───────────
        _SOCIAL_MAP = {"POSITIVE": +1.0, "NEUTRAL": 0.0, "NEGATIVE": -1.0}
        social_raw_sentiment = social_data.get("sentiment", "NEUTRAL").upper()
        social_hype_score    = float(social_data.get("hype_score", 0.0))
        social_reason_base   = social_data.get("reason", "データなし")

        raw_social_sig  = _SOCIAL_MAP.get(social_raw_sentiment, 0.0)
        social_hype_penalty = False
        social_sig      = raw_social_sig

        # Hypeペナルティ: POSITIVE + 高Hype + FA・Tech 両方の裏付けなし
        if (social_raw_sentiment == "POSITIVE"
                and social_hype_score >= SOCIAL_HYPE_THRESHOLD
                and not (fa_sig > 0.0 and tech_sig >= 0.0)):
            social_sig = -0.5
            social_hype_penalty = True

        social_reason = (
            f"[買い煽りペナルティ hype={social_hype_score:.2f}] {social_reason_base}"
            if social_hype_penalty else social_reason_base
        )

        # ── 加重スコア計算（5 要素、除外エージェント分は再正規化済み） ──
        score = round(
            news_sig    * w["news"]
            + fa_sig    * w["fundamental"]
            + tech_sig  * w["technical"]
            + macro_sig * w["macro"]
            + social_sig * w["social"],
            4,
        )

        # ── マクロブレーキ: NEGATIVE かつ MacroAgent が有効な場合のみ発動 ─
        macro_forced_hold = macro_sig < 0.0 and "macro" not in excluded_keys

        # ── Strong Buy 判定 ───────────────────────────────────────
        is_strong_buy = (
            not macro_forced_hold          # マクロ NEGATIVE → 強制 HOLD
            and score >= STRONG_BUY_SCORE
            and fa_sig   >  0.0            # FA は positive 必須
            and tech_sig >= 0.0            # テクニカルは negative 不可
            and news_sig >= 0.0            # ニュースは negative 不可
        )
        decision = _STRONG_BUY_LABEL if is_strong_buy else _HOLD_LABEL

        # ── シグナル表示 ──────────────────────────────────────────
        sigs    = {
            "news": news_sig, "fundamental": fa_sig,
            "technical": tech_sig, "macro": macro_sig,
            "social": social_sig,
        }
        reasons = {
            "news": news_reason, "fundamental": fa_reason,
            "technical": tech_reason, "macro": macro_reason,
            "social": social_reason,
        }
        icons = {k: ("📈" if v > 0 else "📉" if v < 0 else "➡️") for k, v in sigs.items()}

        _log("シグナル集計:")
        _log(f"  {icons['macro']}      マクロ環境       "
             f"({w['macro']:.0%}) : {macro_sig:+.2f}  → {macro_reason[:45]}"
             + ("  ← ⚠ ブレーキ" if macro_forced_hold else "")
             + ("  [除外]" if "macro" in excluded_keys else ""))
        _log(f"  {icons['news']}        ニュース         "
             f"({w['news']:.0%}) : {news_sig:+.2f}  → {news_reason[:47]}"
             + ("  [除外]" if "news" in excluded_keys else ""))
        _log(f"  {icons['fundamental']} ファンダメンタル "
             f"({w['fundamental']:.0%}) : {fa_sig:+.2f}  → {fa_reason[:47]}"
             + ("  [除外]" if "fundamental" in excluded_keys else ""))
        _log(f"  {icons['technical']}   テクニカル       "
             f"({w['technical']:.0%}) : {tech_sig:+.2f}  → {tech_reason[:47]}"
             + ("  [除外]" if "technical" in excluded_keys else ""))
        _log(f"  {icons['social']}  📱 SNSセンチメント  "
             f"({w['social']:.0%}) : {social_sig:+.2f}"
             f"  [生={raw_social_sig:+.1f} hype={social_hype_score:.2f}]"
             f"  → {social_reason_base[:30]}")
        if social_hype_penalty:
            _log(f"     ⚠️  Hype≥{SOCIAL_HYPE_THRESHOLD} かつ FA/Tech 未確認"
                 f" → 買い煽りペナルティ適用 ({raw_social_sig:+.1f} → {social_sig:+.2f})")
        elif (social_raw_sentiment == "POSITIVE"
              and social_hype_score >= SOCIAL_HYPE_THRESHOLD):
            _log(f"     ✅ Hype={social_hype_score:.2f}≥{SOCIAL_HYPE_THRESHOLD} だが"
                 f" FA+Tech 両方確認済 → ペナルティなし")
        _sep()
        _log(f"加重スコア: {score:+.4f}  (Strong Buy 閾値: {STRONG_BUY_SCORE:.2f})")
        if macro_forced_hold:
            _log("⚠ マクロ NEGATIVE のため強制 HOLD（安全弁）")
        _sep()

        # ── Wiki 過去実績コンテキストの取得 ──────────────────────
        wiki_ctx = _fetch_wiki_context(ticker)
        if wiki_ctx:
            _log("📚 Wiki 過去実績:")
            for line in wiki_ctx.splitlines():
                _log(f"  {line}")
            _sep()

        # ── LLM による根拠テキスト生成 ────────────────────────────
        if mock_mode:
            _log("⚠️  [MOCK] LLM スキップ — ダミー根拠テキストを使用")
            hype_note = (
                f" Social Hype={social_hype_score:.2f}→ペナルティ適用" if social_hype_penalty
                else f" Social Hype={social_hype_score:.2f}→FA+Tech確認済・ペナルティなし"
            )
            rationale = (
                f"[MOCK] 5シグナル総合: score={score:+.4f}"
                f" (FA={fa_sig:+.1f}, Tech={tech_sig:+.1f},"
                f" Macro={macro_sig:+.1f}, News={news_sig:+.1f},"
                f" Social={social_sig:+.2f}).{hype_note}"
            )
        else:
            _log("根拠テキスト生成中 (gemini-2.0-flash)...")
            rationale = self._build_rationale(ticker, decision, score, sigs, reasons,
                                              wiki_context=wiki_ctx)

        # ── 判断通知（発注は RiskAgent 算出後に run_trade_cycle で実行）──
        if is_strong_buy:
            _sep()
            _log("✅ STRONG BUY 確定 → RiskAgent によるポジションサイジング後に発注します...")
        else:
            hold_reason = "マクロ NEGATIVE による強制 HOLD" if macro_forced_hold else "Strong Buy 条件未達"
            _log(f"{hold_reason} → 見送り (HOLD)")
            if not macro_forced_hold:
                _log("  条件チェック:")
                _log(f"    score ≥ {STRONG_BUY_SCORE}  : {score:+.3f} → {'✓' if score >= STRONG_BUY_SCORE else '✗'}")
                _log(f"    FA > 0             : {fa_sig:+.2f} → {'✓' if fa_sig > 0 else '✗'}")
                _log(f"    Tech ≥ 0           : {tech_sig:+.2f} → {'✓' if tech_sig >= 0 else '✗'}")
                _log(f"    News ≥ 0           : {news_sig:+.2f} → {'✓' if news_sig >= 0 else '✗'}")
                if social_hype_penalty:
                    _log(f"    Social Hype penalty: {raw_social_sig:+.1f} → {social_sig:+.2f} (スコア押下)")

        # ── BBS 書き込み ──────────────────────────────────────────
        judgment = {
            "ticker":               ticker,
            "decision":             decision,
            "score":                score,
            "threshold":            STRONG_BUY_SCORE,
            "signals":              sigs,
            "signal_reasons":       reasons,
            "macro_forced_hold":    macro_forced_hold,
            "social_hype_penalty":  social_hype_penalty,
            "social_hype_score":    social_hype_score,
            "rationale":            rationale,
            "order":                None,
            "is_strong_buy":        is_strong_buy,
            "dry_run":              dry_run,
        }
        self.bbs.write(self.NAME, "manager_judgment", judgment)
        _phase_footer()
        return judgment


# =========================================================
# Stage 4 — RiskAgent
# =========================================================

class RiskAgent:
    NAME = "RiskAgent"

    def __init__(self, bbs: BBS):
        self.bbs = bbs

    def run(self, ticker: str = TARGET_TICKER, phase_tag: str = "S4") -> dict:
        _phase_header(phase_tag, self.NAME)
        cfg             = _load_agent_config("risk_agent")
        account_balance = float(cfg.get("params", {}).get("account_balance", 100_000.0))
        _log(f"{ticker} のポジションサイジングを計算中 (口座残高: ${account_balance:,.0f})...")
        _sep()

        try:
            result = _risk_mod.calculate_position(ticker, account_balance=account_balance)
        except Exception as e:
            result = {
                "ticker":             ticker,
                "recommended_shares": 1,
                "stop_loss_price":    0.0,
                "stop_loss_pct":      0.0,
                "reason":             f"計算エラー: {e}",
                "error":              str(e),
            }
            _log(f"エラー: {e}")

        if result.get("error"):
            _log(f"計算エラー: {result['error']} → デフォルト 1株 で継続")
        else:
            _log(f"現在価格     : ${result.get('current_price', 0):.2f}")
            _log(f"ATR(14日)    : ${result.get('atr', 0):.4f}")
            _log(f"ストップロス : ${result.get('stop_loss_price', 0):.2f}  "
                 f"(現在価格より -{result.get('stop_loss_pct', 0):.2f}%)")
            _log(f"利益確定     : ${result.get('take_profit_price', 0):.2f}  "
                 f"(現在価格より +{result.get('take_profit_pct', 0):.2f}%)  ← ATR×4 (RR 1:2)")
            _sep()
            _log(f"リスク許容額 : ${result.get('risk_amount', 0):,.2f}  "
                 f"(口座残高の {_RISK_PCT:.0f}%)")
            _log(f"Fixed Fractional: {result.get('fixed_fractional_shares', 0)} 株")
            _log(f"Kelly Criterion : {result.get('kelly_shares', 0)} 株")
            _sep()

        rec  = result.get("recommended_shares", 1)
        stop = result.get("stop_loss_price", 0.0)
        _log(f"✅ 推奨株数     : {rec} 株  (保守的な方を採用)")
        _log(f"🛑 ストップロス : ${stop:.2f}")
        _sep()
        _log(f"根拠: {result.get('reason', '')[:80]}")

        self.bbs.write(self.NAME, "risk_analysis", result)
        _phase_footer()
        return result


_RISK_PCT = 2  # リスク割合表示用（固定値）


# =========================================================
# オーケストレーション本体
# =========================================================

def _run_mock_stage1(bbs: BBS, ticker: str, excluded_keys: list[str] | None = None) -> None:
    """Stage 1 の各エージェントをモックデータで代替する（API 呼び出しなし）。"""
    excluded_keys = excluded_keys or []
    note = "⚠️  [MOCK] LLM スキップ — ダミーデータを BBS に書き込み"

    def _excl_note(key: str) -> str:
        return "  [アブレーション: 除外済]" if key in excluded_keys else ""

    _phase_header("S1-1/4", "TechnicalAgent")
    _log(note)
    _sep()
    if "technical" in excluded_keys:
        _log("  ⚠️  [アブレーション] TechnicalAgent を除外 → NEUTRAL エントリを書き込み")
        bbs.write("TechnicalAgent", "technical_analysis",
                  {"trend": "neutral", "excluded": True, "trend_reason": "TechnicalAgent 除外済"})
    else:
        data: dict = {**MOCK_BBS_DATA["technical_analysis"], "ticker": ticker}
        _log("テクニカルトレンド判定: 📈 POSITIVE")
        _log(f"根拠: {data['trend_reason']}")
        bbs.write("TechnicalAgent", "technical_analysis", data)
    _phase_footer()

    _phase_header("S1-2/4", "NewsAgent")
    _log(note)
    _sep()
    if "news" in excluded_keys:
        _log("  ⚠️  [アブレーション] NewsAgent を除外 → NEUTRAL エントリを書き込み")
        bbs.write("NewsAgent", "news_analysis",
                  {"articles": [], "avg_sentiment_score": 0.0, "excluded": True,
                   "trend": "neutral", "trend_reason": "NewsAgent 除外済"})
    else:
        articles = [
            {**a, "ticker": ticker, "company": ticker,
             "title": a.get("title", "").replace("AAPL", ticker),
             "reason": a.get("reason", "").replace("AAPL", ticker)}
            for a in MOCK_BBS_DATA["news_analysis"]["articles"]
        ]
        data = {**MOCK_BBS_DATA["news_analysis"], "ticker": ticker, "articles": articles}
        art = articles[0]
        _log(f"📈 [positive ] {art['title']}")
        _log(f"           理由: {art['reason'][:60]}")
        _sep()
        _log("センチメント平均スコア: +1.00  (強気)")
        bbs.write("NewsAgent", "news_analysis", data)
    _phase_footer()

    _phase_header("S1-3/4", "MacroAgent")
    _log(note)
    _sep()
    if "macro" in excluded_keys:
        _log("  ⚠️  [アブレーション] MacroAgent を除外 → NEUTRAL エントリを書き込み")
        bbs.write("MacroAgent", "macro_analysis",
                  {"trend": "neutral", "excluded": True, "trend_reason": "MacroAgent 除外済"})
    else:
        data = {**MOCK_BBS_DATA["macro_analysis"]}
        _log("マクロ環境判定: ➡️  NEUTRAL")
        _log(f"根拠: {data['trend_reason']}")
        bbs.write("MacroAgent", "macro_analysis", data)
    _phase_footer()

    _phase_header("S1-4/4", "SocialAgent")
    _log(note)
    _sep()
    if "social" in excluded_keys:
        _log("  ⚠️  [アブレーション] SocialAgent を除外 → NEUTRAL エントリを書き込み")
        bbs.write("SocialAgent", "social_analysis",
                  {"sentiment": "NEUTRAL", "hype_score": 0.0, "excluded": True,
                   "reason": "SocialAgent 除外済", "post_count": 0})
    else:
        social_mock = {**MOCK_BBS_DATA["social_analysis"], "ticker": ticker}
        hype = social_mock["hype_score"]
        filled = int(hype * 10)
        hype_bar = "█" * filled + "░" * (10 - filled)
        _log(f"データソース   : {social_mock['source']}  (投稿数: {social_mock['post_count']}件)")
        _log(f"センチメント判定: 📈 {social_mock['sentiment']}")
        _log(f"買い煽りスコア  : [{hype_bar}] {hype:.2f}  ⚠️  高Hype警戒 (FA/Tech裏付け必要)")
        _sep()
        _log(f"判定根拠: {social_mock['reason'][:80]}")
        bbs.write("SocialAgent", "social_analysis", social_mock)
    _phase_footer()


def _run_mock_stage2(bbs: BBS, ticker: str) -> None:
    """Stage 2 の FundamentalAgent をモックデータで代替する（API 呼び出しなし）。"""
    _phase_header("S2", "FundamentalAgent")
    _log("⚠️  [MOCK] LLM スキップ — ダミーデータを BBS に書き込み")
    _sep()
    data: dict = {**MOCK_BBS_DATA["fundamental_analysis"], "ticker": ticker}
    _log("ファンダメンタルトレンド判定: 📈 POSITIVE")
    _log(f"根拠: {data['trend_reason']}")
    bbs.write("FundamentalAgent", "fundamental_analysis", data)
    _phase_footer()


def _run_mock_risk(bbs: BBS, ticker: str) -> None:
    """RiskAgent をモックデータで代替する（API 呼び出しなし）。"""
    _phase_header("S4", "RiskAgent")
    _log("⚠️  [MOCK] LLM スキップ — ダミーデータを BBS に書き込み")
    _sep()
    data: dict = {**MOCK_BBS_DATA["risk_analysis"], "ticker": ticker}
    _log(f"現在価格     : ${data['current_price']:.2f}")
    _log(f"ATR(14日)    : ${data['atr']:.4f}")
    _log(f"ストップロス : ${data['stop_loss_price']:.2f}  (-{data['stop_loss_pct']:.2f}%)")
    _log(f"利益確定     : ${data.get('take_profit_price', 0):.2f}  "
         f"(+{data.get('take_profit_pct', 0):.2f}%)  ← ATR×4 (RR 1:2)")
    _sep()
    _log(f"リスク許容額 : ${data['risk_amount']:,.2f}")
    _log(f"Fixed Fractional: {data['fixed_fractional_shares']} 株")
    _log(f"Kelly Criterion : {data['kelly_shares']} 株")
    _sep()
    _log(f"✅ 推奨株数     : {data['recommended_shares']} 株  (保守的な方を採用)")
    _log(f"🛑 ストップロス : ${data['stop_loss_price']:.2f}")
    _log(f"🎯 利益確定     : ${data.get('take_profit_price', 0):.2f}")
    _sep()
    _log(f"根拠: {data['reason'][:80]}")
    bbs.write("RiskAgent", "risk_analysis", data)
    _phase_footer()


def _agent_to_weight_key(agent_name: str) -> str | None:
    """エージェント名をウェイトキーに変換する。"""
    mapping = {
        "technicalagent": "technical",
        "technical":      "technical",
        "newsagent":      "news",
        "news":           "news",
        "macroagent":     "macro",
        "macro":          "macro",
        "socialagent":    "social",
        "social":         "social",
        "fundamentalagent": "fundamental",
        "fundamental":    "fundamental",
    }
    return mapping.get(agent_name.lower())


def _compute_effective_weights(excluded_keys: list[str]) -> dict[str, float]:
    """除外エージェントのウェイトを残存エージェントに比例配分して再正規化する。"""
    excluded_weight = sum(WEIGHTS[k] for k in excluded_keys if k in WEIGHTS)
    if excluded_weight >= 1.0:
        return {k: 0.0 for k in WEIGHTS}
    scale = 1.0 / (1.0 - excluded_weight)
    return {k: (0.0 if k in excluded_keys else round(WEIGHTS[k] * scale, 6)) for k in WEIGHTS}


def _fetch_past_lessons(ticker: str, max_rules: int = 5) -> str:
    """
    Obsidian ログから過去の失敗・成功教訓を抽出し、CriticAgent 用テキストを返す。
    対象: outcome=CLOSED かつ action=SELL のログ（負の損益を優先）
    """
    logs_dir = Path("data/knowledge_base/obsidian_logs")
    if not logs_dir.exists():
        return "（過去ログなし）"

    lessons: list[str] = []
    for log_file in sorted(logs_dir.glob(f"*_{ticker.upper()}_SELL*.md"), reverse=True):
        try:
            text = log_file.read_text(encoding="utf-8")
            if "outcome: CLOSED" not in text:
                continue
            pl_match = re.search(r"profit_loss:\s*([^\n]+)", text)
            pl_str   = pl_match.group(1).strip() if pl_match else "?"
            rule_start = text.find("## 4.")
            if rule_start != -1:
                rule_end = text.find("\n## 5.", rule_start)
                rule_sec = text[rule_start: rule_end if rule_end != -1 else rule_start + 400]
                lessons.append(f"損益 {pl_str}: {rule_sec.strip()}")
        except Exception:
            continue

    if not lessons:
        return "（対象銘柄の過去教訓なし）"
    return "\n\n".join(lessons[:max_rules])


def _fetch_wiki_context(ticker: str, max_trades: int = 5) -> str:
    """
    Wiki ティッカーページから直近 SELL 実績と関連コンセプトを抽出し、
    ManagerAgent の rationale 生成に注入するコンテキストを返す。
    """
    ticker_file = Path("data/knowledge_base/wiki/tickers") / f"{ticker.upper()}.md"
    if not ticker_file.exists():
        return ""

    text = ticker_file.read_text(encoding="utf-8")

    # frontmatter から最終評価を取得
    assessment_m = re.search(r"^assessment:\s*(\w+)", text, re.MULTILINE)
    score_m      = re.search(r"^assessment_score:\s*([\d.+\-]+)", text, re.MULTILINE)
    assessment   = assessment_m.group(1) if assessment_m else "UNKNOWN"
    score_str    = score_m.group(1)      if score_m      else "?"

    # トレード履歴から直近 SELL を重複排除して抽出
    trade_match = re.search(r"## トレード履歴\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    recent_sells: list[str] = []
    if trade_match:
        seen_log: set[str] = set()
        for row in trade_match.group(1).splitlines():
            # 空セルを保持したまま分割（価格・スコアが空の場合がある）
            cols = [c.strip() for c in row.split("|")]
            cells = [c for i, c in enumerate(cols) if 0 < i < len(cols) - 1]
            if len(cells) < 6 or cells[1] != "SELL":
                continue
            entry_date, result, raw_log_key = cells[0], cells[4], cells[5]
            # [[LogName|alias]] 形式のエイリアスを除去して正規化
            log_key = re.sub(r"\|[^\]]*", "", raw_log_key)
            if log_key in seen_log:
                continue
            seen_log.add(log_key)
            recent_sells.append(f"{entry_date}: P&L={result}")
            if len(recent_sells) >= max_trades:
                break

    # 関連コンセプトを重複排除して収集
    concepts_match = re.search(r"## 関連コンセプト\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    unique_concepts: list[str] = []
    if concepts_match:
        seen_cpt: set[str] = set()
        for line in concepts_match.group(1).splitlines():
            m = re.search(r"\[\[concepts/([^\]|]+)", line)
            if m:
                cname = m.group(1)
                if cname not in seen_cpt:
                    seen_cpt.add(cname)
                    unique_concepts.append(cname)

    if not recent_sells and not unique_concepts:
        return ""

    parts = [f"【{ticker} 過去実績】直近評価: {assessment} (score={score_str})"]
    if recent_sells:
        parts.append("直近SELL実績:\n" + "\n".join(f"  {s}" for s in recent_sells))
    if unique_concepts:
        parts.append("関連コンセプト: " + ", ".join(unique_concepts[:8]))
    return "\n".join(parts)


def run_trade_cycle(
    ticker: str             = TARGET_TICKER,
    dry_run: bool           = False,
    notify_line: bool       = False,
    mock_mode: bool         = False,
    hybrid_mode: bool       = False,
    excluded_agents: list[str] | None = None,
) -> dict:
    """
    AAPL スイングトレード分析サイクルをステージゲート方式で実行する。

    Stage 1: TechnicalAgent + NewsAgent + MacroAgent（安価スキャン）
    Gate   : マクロ NEGATIVE → ブレーキ HOLD / Tech・News 双方 NEUTRAL → HOLD
    Stage 2: FundamentalAgent（Gate 通過時のみ）
    Stage 3: ManagerAgent（最終評価 & 発注）

    Args:
        ticker:       対象ティッカー（デフォルト: AAPL）
        dry_run:      True の場合 Alpaca 発注をスキップ（テスト用）
        notify_line:  True の場合、最終判断を LINE 通知
        mock_mode:    True の場合、全 LLM/API 呼び出しをスキップしてダミーデータでフローをテスト
                      (EDGAR 自律取得も無効化)
        hybrid_mode:  True の場合、Stage 1〜4 すべてリアル市場データ・リアル分析を実行し、
                      発注のみスキップ。EDGAR 自律取得（allow_edgar_fetch=True）も有効。
                      学習データ品質向上・本番前検証に推奨。
    """
    excluded_agents = excluded_agents or []
    excluded_keys: list[str] = list({
        k for a in excluded_agents
        if (k := _agent_to_weight_key(a)) is not None
    })
    eff_weights = _compute_effective_weights(excluded_keys)

    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _main_header(ticker, session_id)
    if excluded_keys:
        _log(f"[アブレーション] 除外エージェント: {excluded_agents}  "
             f"→ 有効ウェイト: {eff_weights}")
    if mock_mode:
        _mock_banner()
    elif hybrid_mode:
        _hybrid_banner()

    bbs = BBS(session_id)

    def _write_excluded(agent_name: str, bbs_key: str) -> None:
        bbs.write(agent_name, bbs_key, {"trend": "neutral", "excluded": True,
                                        "trend_reason": f"{agent_name} 除外済"})

    # ── Alpaca クライアント初期化 & portfolio 同期 ──────────────────
    _alpaca: _AlpacaClient | None = None
    if not mock_mode:
        try:
            _alpaca = _AlpacaClient()
            _log("[Portfolio] Alpaca ポジションと portfolio.json を同期中...")
            _sync = _alpaca.sync_portfolio(_PORTFOLIO_PATH)
            _log(f"[Portfolio] 同期完了: Alpaca={_sync['alpaca_positions']} 件  "
                 f"追加={_sync['added']}  除去={_sync['removed']}")
            is_open, market_msg = _alpaca.is_market_open()
            market_icon = "🟢" if is_open else "🔴"
            _log(f"[Market] {market_icon} {market_msg}")
        except Exception as _e:
            _log(f"[Alpaca] 初期化エラー: {_e} — dry_run モードで継続")
            _alpaca = None

    # ── Stage 0: Selling Loop（保有ポジション売却チェック）─────────
    _stage_header(0, "Selling Loop  [ExitAgent]")
    exit_results = ExitAgent(bbs).run(
        mock_mode=mock_mode,
        alpaca_client=_alpaca if not dry_run else None,
        phase_tag="S0",
    )
    if exit_results:
        sell_tickers = [r["ticker"] for r in exit_results if r["action"] == "SELL"]
        if sell_tickers and notify_line:
            for r in [r for r in exit_results if r["action"] == "SELL"]:
                order = r.get("order_result", {})
                send_line_message(
                    f"【ECC 売却実行】{r['ticker']}\n"
                    f"種別: {r['exit_type']}\n"
                    f"理由: {r['reason']}\n"
                    f"損益: {r['pnl_pct']:+.2f}%\n"
                    + (f"注文ID: {order.get('order_id')}" if order.get("order_id") else "")
                )

    # ── Stage 1: 安価シグナルスキャン ────────────────────────────
    _stage_header(1, "安価シグナルスキャン  [Technical + News + Macro + Social]")
    if mock_mode:
        _run_mock_stage1(bbs, ticker, excluded_keys=excluded_keys)
    else:
        if "technical" in excluded_keys:
            _write_excluded("TechnicalAgent", "technical_analysis")
        else:
            TechnicalAgent(bbs).run(ticker, phase_tag="S1-1/4")

        if "news" in excluded_keys:
            _write_excluded("NewsAgent", "news_analysis")
        else:
            NewsAgent(bbs).run(ticker, phase_tag="S1-2/4")

        if "macro" in excluded_keys:
            _write_excluded("MacroAgent", "macro_analysis")
        else:
            MacroAgent(bbs).run(phase_tag="S1-3/4")

        if "social" in excluded_keys:
            _write_excluded("SocialAgent", "social_analysis")
        else:
            SocialAgent(bbs).run(ticker, phase_tag="S1-4/4")

    # ── Gate: マクロブレーキ / 双方 NEUTRAL → HOLD 即終了 ────────
    gate = _gate_check(bbs, ticker)
    _gate_display(gate)

    if gate["skip_fundamental"]:
        # Gate HOLD でも Social シグナルを反映（既に BBS に書き込み済み）
        _social_gate     = bbs.read("social_analysis") or {}
        _sg_sentiment    = _social_gate.get("sentiment", "NEUTRAL").upper()
        _sg_hype         = float(_social_gate.get("hype_score", 0.0))
        _sg_sig_gate_raw = {"POSITIVE": +1.0, "NEUTRAL": 0.0, "NEGATIVE": -1.0}.get(_sg_sentiment, 0.0)
        _sg_sig_gate     = _sg_sig_gate_raw  # Hypeペナルティは FA なし前提でも Gate では中立扱い
        score = round(
            gate["news_signal"]   * eff_weights["news"]
            + gate["tech_signal"] * eff_weights["technical"]
            + gate["macro_signal"] * eff_weights["macro"]
            + _sg_sig_gate * eff_weights["social"],
            4,
        )
        brake_label = "マクロブレーキ" if gate["macro_brake"] else "シグナル不足"
        judgment = {
            "ticker":    ticker,
            "decision":  _HOLD_LABEL,
            "score":     score,
            "threshold": STRONG_BUY_SCORE,
            "signals": {
                "news":        gate["news_signal"],
                "technical":   gate["tech_signal"],
                "macro":       gate["macro_signal"],
                "fundamental": 0.0,
                "social":      _sg_sig_gate,
            },
            "gate_skipped":  True,
            "gate_reason":   gate["reason"],
            "macro_brake":   gate["macro_brake"],
            "rationale":     f"Gate: {gate['reason']}",
            "order":  None,
            "dry_run": dry_run,
        }
        bbs.write("GateAgent", "manager_judgment", judgment)

        _decision_box([
            f"{'─' * (_W - 2)}",
            f"  Gate 判断: HOLD（{brake_label}）",
            f"{'─' * (_W - 2)}",
            f"  銘柄         : {ticker}",
            "  判断         : ⏸  HOLD",
            f"  理由         : {gate['reason'][:55]}",
            "  スキップ     : FundamentalAgent (トークンコスト節約)",
            f"{'─' * (_W - 2)}",
            f"  BBS ログ     : {bbs.path}",
        ])

        if notify_line:
            send_line_message(
                f"【ECC {ticker} 判断】⏸ HOLD\n理由: {gate['reason']}"
            )
        if mock_mode:
            _mock_banner("テスト実行完了（Gate: HOLD）。実際のAPIは一切呼び出されていません。")
        elif hybrid_mode:
            _hybrid_banner("ハイブリッド実行完了（Gate: HOLD）。市場データはリアル、発注はスキップされました。")
        record_id = _training_mod.save_training_record(
            session_id=session_id,
            ticker=ticker,
            bbs_entries=bbs.read_all(),
            judgment=judgment,
            mock_mode=mock_mode,
            hybrid_mode=hybrid_mode,
        )
        _log(f"[学習データ] 保存完了: record_id={record_id}")
        return judgment

    # ── Stage 2: Fundamental 深層分析 ────────────────────────────
    _stage_header(2, "ファンダメンタルズ深層分析  [FundamentalAgent]")
    if "fundamental" in excluded_keys:
        _log("  ⚠️  [アブレーション] FundamentalAgent を除外 → NEUTRAL エントリを書き込み")
        bbs.write("FundamentalAgent", "fundamental_analysis",
                  {"trend": "neutral", "excluded": True, "trend_reason": "FundamentalAgent 除外済"})
    elif mock_mode:
        # mock_mode: 完全モック（API/EDGAR 呼び出しゼロ）
        _run_mock_stage2(bbs, ticker)
    else:
        # hybrid_mode / 通常モード: 新 FundamentalAgent で RAG + EDGAR 自律取得
        # hybrid_mode でも EDGAR 自律取得を有効にし学習データの品質を確保する
        FundamentalAgent(bbs).run(
            ticker,
            phase_tag="S2",
            allow_edgar_fetch=True,   # mock_mode=False の場合は常に有効
        )

    # ── Stage 3: 最終評価 & 発注判断 ─────────────────────────────
    _stage_header(3, "最終評価 & 発注判断  [ManagerAgent]")
    judgment = ManagerAgent(bbs).run(
        ticker, dry_run=dry_run, phase_tag="S3", mock_mode=mock_mode,
        effective_weights=eff_weights, excluded_keys=excluded_keys,
    )

    # ── Stage 4: リスク管理 & ポジションサイジング（STRONG BUY 時のみ）──
    order_result: dict | None = None
    if judgment.get("is_strong_buy"):
        _stage_header(4, "リスク管理 & ポジションサイジング  [RiskAgent]")
        if mock_mode:
            _run_mock_risk(bbs, ticker)
        else:
            RiskAgent(bbs).run(ticker)

        risk_data  = bbs.read("risk_analysis") or {}
        rec_shares = risk_data.get("recommended_shares", 1)

        # ── CriticAgent による監査（Ollama 到達可能時のみ有効）──────
        proceed_with_buy = True
        if not mock_mode and not dry_run and not hybrid_mode:
            _phase_header("S4.5", "CriticAgent")
            _log("ManagerAgent の判断を過去教訓で監査中...")
            _sep()
            _critic_rules = _fetch_past_lessons(ticker)
            _critic       = _CriticAgentImpl()
            _critic_res   = _critic.evaluate_trade(
                ticker          = ticker,
                manager_action  = judgment.get("decision", "HOLD"),
                manager_context = judgment.get("rationale", ""),
                retrieved_rules = _critic_rules,
            )
            _is_fallback = "API障害" in _critic_res.get("critique_reason", "") or \
                           "フォールバック" in _critic_res.get("critique_reason", "")
            _cd = _critic_res.get("critic_decision", "HOLD")
            _icon_c = "✅" if _cd == "APPROVE" else ("⚠️" if _is_fallback else "❌")
            _log(f"{_icon_c} 判定: {_cd}  理由: {_critic_res.get('critique_reason','')[:65]}")

            if _cd == "OVERRIDE":
                _log("❌ CriticAgent OVERRIDE → 発注キャンセル")
                proceed_with_buy = False
                judgment["critic_override"]   = True
                judgment["critic_reason"]     = _critic_res.get("critique_reason")
            elif _is_fallback:
                _log("⚠️  Ollama 未接続（フォールバック） → CriticAgent スキップ、発注継続")
                judgment["critic_override"]   = False
                judgment["critic_reason"]     = _critic_res.get("critique_reason")
            else:
                _log(f"✅ {_cd} → BUY 継続")
            bbs.write("CriticAgent", "critic_judgment", _critic_res)
            _phase_footer()

        # ── 発注 ────────────────────────────────────────────────
        _sep()
        _log(f"Alpaca に {ticker} {rec_shares}株 買い注文を送信します...")
        if not proceed_with_buy:
            _log("  CriticAgent OVERRIDE のため発注スキップ")
            order_result = {"skipped": True, "skip_reason": "CriticAgent OVERRIDE"}
        elif dry_run or mock_mode or hybrid_mode:
            label = "hybrid_mode" if hybrid_mode else ("mock_mode" if mock_mode else "dry_run")
            _log(f"  ({label}=True のため実際の発注はスキップ)")
            order_result = {
                "dry_run": True, "mock": mock_mode or hybrid_mode,
                "symbol": ticker, "qty": rec_shares, "side": "buy",
            }
        elif _alpaca is not None:
            order_result = _alpaca.place_buy(ticker, rec_shares)
            if order_result.get("success"):
                _log(f"  ✅ 注文完了: order_id={order_result.get('order_id')}")
                _log(f"     status  : {order_result.get('status')}")
                _log(f"     symbol  : {order_result.get('symbol')} × {order_result.get('qty')} 株")
            elif order_result.get("skipped"):
                _log(f"  ⏭  注文スキップ: {order_result.get('skip_reason')}")
            else:
                _log(f"  ❌ 発注エラー: {order_result.get('error')}")
        else:
            # Alpaca 初期化失敗時のフォールバック
            try:
                order_result = _alpaca_mod.place_market_order(ticker, rec_shares, "buy")
                _log(f"  注文完了 (fallback): order_id={order_result.get('order_id')}")
            except Exception as e:
                order_result = {"error": str(e)}
                _log(f"  発注エラー: {e}")

        judgment["order"] = order_result
        bbs.write("ManagerAgent", "manager_judgment", judgment)

        # ── ポートフォリオ登録（注文成功 or dry_run/mock 時のみ）──
        _order_ok = (
            order_result.get("success")                   # live 成功
            or order_result.get("dry_run")                # dry_run / mock
        )
        if _order_ok:
            try:
                _entry_price = risk_data.get("current_price", 0.0)
                _stop_price  = risk_data.get("stop_loss_price")
                _fill_price  = order_result.get("fill_price")  # Alpaca 約定価格
                _actual_entry = _fill_price or _entry_price
                _buy_log = _ObsidianLogger().save_log({
                    "ticker":  ticker,
                    "action":  "BUY",
                    "context": (
                        f"{judgment.get('rationale', '(根拠なし)')}\n"
                        + (
                            f"--- Alpaca 注文 ---\n"
                            f"注文ID: {order_result.get('order_id', 'N/A')}\n"
                            f"ステータス: {order_result.get('status', 'N/A')}\n"
                            f"約定価格: ${_fill_price:.2f}" if _fill_price else ""
                        )
                    ),
                    "tags": ["entry", ticker.lower(), session_id],
                })
                _log(f"  [Obsidian] 購入ログ保存: {_buy_log.name}")
                # ATRベースのTP価格を優先。取得できなかった場合のみ+10%フォールバック
                _atr_tp = risk_data.get("take_profit_price")
                _target = _atr_tp if _atr_tp else (
                    round(_actual_entry * 1.10, 2) if _actual_entry else None
                )
                _portfolio_add(
                    ticker          = ticker,
                    entry_price     = _actual_entry,
                    shares          = rec_shares,
                    target_price    = _target,
                    stop_loss_price = _stop_price,
                    buy_log_file    = _buy_log.name,
                    thesis          = judgment.get("rationale", ""),
                )
                _log(f"  [Portfolio] {ticker} ×{rec_shares} を portfolio.json に登録しました")
            except Exception as e:
                _log(f"  [Portfolio] 登録エラー（ログは保存済）: {e}")

        elif order_result and order_result.get("skipped") and judgment.get("critic_override"):
            # CriticAgent OVERRIDE → シャドウ・ロギング（発注なしでも記録を残す）
            try:
                _cr_reason = (
                    judgment.get("critic_reason")
                    or order_result.get("skip_reason", "CriticAgent OVERRIDE")
                )
                _price = risk_data.get("current_price", "N/A")
                _atr   = risk_data.get("atr", "N/A")
                _sl    = risk_data.get("stop_loss_price", "N/A")
                _tp    = risk_data.get("take_profit_price", "N/A")
                _skip_log = _ObsidianLogger().save_log({
                    "ticker":  ticker,
                    "action":  "SKIPPED",
                    "outcome": "OVERRIDE",
                    "context": (
                        f"スコア: {judgment.get('score', 0):+.4f}  "
                        f"(閾値: {STRONG_BUY_SCORE})\n"
                        f"{judgment.get('rationale', '(根拠なし)')}"
                    ),
                    "root_cause": _cr_reason,
                    "risk_summary": (
                        f"現在価格: ${_price}  / ATR(14): ${_atr}\n"
                        f"ストップロス: ${_sl}  / 利益確定: ${_tp}\n"
                        f"推奨株数: {rec_shares}株"
                    ),
                    "rule_for_future": (
                        "CriticAgentの拒否判断と実際のその後の株価推移を照合し、"
                        "過去教訓の適切性を定期的に検証すること。"
                    ),
                    "profit_loss": "N/A",
                    "tags": ["skipped", "critic_override", ticker.lower(), session_id],
                })
                _log(f"  [Obsidian] SKIPPED ログ保存: {_skip_log.name}")
            except Exception as e:
                _log(f"  [Obsidian] SKIPPED ログ保存エラー: {e}")

    # ── 最終結果表示 ─────────────────────────────────────────────
    decision   = judgment.get("decision", _HOLD_LABEL)
    score      = judgment.get("score", 0.0)
    order      = judgment.get("order") or {}
    risk_data  = bbs.read("risk_analysis") or {}
    rec_shares = risk_data.get("recommended_shares", BUY_QTY)
    stop_price = risk_data.get("stop_loss_price")
    stop_pct   = risk_data.get("stop_loss_pct", 0)
    icon       = "🚀" if decision == _STRONG_BUY_LABEL else "⏸"

    order_line = (
        f"  Alpaca 注文  : {ticker} × {rec_shares} 株  "
        f"[{order.get('status', order.get('error', '-'))}]"
        if order else
        "  Alpaca 注文  : なし（見送り）"
    )
    box_lines = [
        f"{'─' * (_W - 2)}",
        "  ManagerAgent 最終決断",
        f"{'─' * (_W - 2)}",
        f"  銘柄         : {ticker}",
        f"  判断         : {icon}  {decision}",
        f"  加重スコア   : {score:+.4f}  (Strong Buy 閾値: {STRONG_BUY_SCORE:.2f})",
        f"  根拠         : {judgment.get('rationale', '')[:60]}",
        f"{'─' * (_W - 2)}",
        order_line,
    ]
    if stop_price:
        box_lines.append(f"  ストップロス : ${stop_price:.2f}  (-{stop_pct:.2f}%)")
    box_lines += [
        f"{'─' * (_W - 2)}",
        f"  BBS ログ     : {bbs.path}",
    ]
    _decision_box(box_lines)

    # ── LINE 通知（オプション）──────────────────────────────────
    if notify_line:
        msg = (
            f"【ECC {ticker} 判断】{icon} {decision}\n"
            f"スコア: {score:+.4f}\n"
            f"根拠: {judgment.get('rationale', '')[:100]}"
        )
        send_line_message(msg)
        print("\n[LINE] 通知送信完了。")

    if mock_mode:
        _mock_banner("テスト実行完了。実際のAPIは一切呼び出されていません。")
    elif hybrid_mode:
        _hybrid_banner("ハイブリッド実行完了。市場データはリアル、発注はスキップされました。")
    record_id = _training_mod.save_training_record(
        session_id=session_id,
        ticker=ticker,
        bbs_entries=bbs.read_all(),
        judgment=judgment,
        mock_mode=mock_mode,
        hybrid_mode=hybrid_mode,
    )
    _log(f"[学習データ] 保存完了: record_id={record_id}")
    return judgment


# =========================================================
# ウォッチリストサイクル（複数銘柄を順番に分析）
# =========================================================

def _watchlist_summary(results: list[dict]) -> None:
    """全銘柄の判断を一覧テーブルで表示する。"""
    bar = "─" * (_W - 2)
    print(f"\n╔{'═' * _W}╗")
    print(f"║  {'ウォッチリスト 分析サマリー':^{_W - 2}}  ║")
    print(f"╠{'═' * _W}╣")
    print(f"║  {'#':>3}  {'Ticker':<6}  {'Decision':<14}  {'Score':>7}  {'根拠 (60文字)':}")
    print(f"╠{'═' * _W}╣")
    icons = {"STRONG BUY": "🚀", "HOLD": "⏸", "SELL": "📉"}
    for i, r in enumerate(results, 1):
        decision = r.get("decision", "HOLD")
        score    = r.get("score", 0.0)
        rationale = r.get("rationale", "")[:45]
        ticker   = r.get("ticker", "-")
        icon     = icons.get(decision, "❓")
        line = f"║  {i:>3}  {ticker:<6}  {icon} {decision:<12}  {score:>+7.4f}  {rationale}"
        print(line.ljust(_W + 1) + "║")
    print(f"╚{'═' * _W}╝\n")


def run_watchlist_cycle(
    tickers:         list[str],
    dry_run:         bool = False,
    notify_line:     bool = False,
    mock_mode:       bool = False,
    hybrid_mode:     bool = False,
    excluded_agents: list[str] | None = None,
) -> list[dict]:
    """
    複数銘柄を順番に run_trade_cycle() で分析し、結果を集約して返す。

    Args:
        tickers:         分析対象ティッカーのリスト
        dry_run:         True の場合 Alpaca 発注をスキップ
        notify_line:     True の場合、全銘柄サマリーを 1 通の LINE に集約
        mock_mode:       True の場合、全 LLM/API 呼び出しをスキップ
        hybrid_mode:     True の場合、発注のみスキップ（リアル分析）
        excluded_agents: 除外するエージェント名のリスト

    Returns:
        各銘柄の judgment dict のリスト（ticker キーを付与済み）
    """
    results: list[dict] = []

    bar = "◆" * (_W + 2)
    print(f"\n{bar}")
    print(f"  📋  [WATCHLIST]  {len(tickers)} 銘柄を順番に分析します")
    for i, t in enumerate(tickers, 1):
        print(f"    {i}. {t}")
    print(f"{bar}\n")

    for ticker in tickers:
        try:
            result = run_trade_cycle(
                ticker          = ticker,
                dry_run         = dry_run,
                notify_line     = False,   # 個別通知を抑制し、後でまとめて送る
                mock_mode       = mock_mode,
                hybrid_mode     = hybrid_mode,
                excluded_agents = excluded_agents,
            )
        except Exception as e:
            _log(f"[Watchlist] {ticker} の分析中にエラー: {e} — スキップします")
            result = {"decision": "HOLD", "score": 0.0, "rationale": f"エラー: {e}"}

        result["ticker"] = ticker
        results.append(result)

    _watchlist_summary(results)

    if notify_line:
        buy_list  = [r for r in results if r.get("decision") == "STRONG BUY"]
        hold_list = [r for r in results if r.get("decision") == "HOLD"]
        lines = ["【ECC ウォッチリスト 分析完了】"]
        if buy_list:
            lines.append("🚀 STRONG BUY:")
            for r in buy_list:
                lines.append(f"  {r['ticker']}  スコア {r.get('score', 0):+.4f}")
        lines.append(f"⏸ HOLD: {', '.join(r['ticker'] for r in hold_list)}")
        send_line_message("\n".join(lines))
        print("\n[LINE] ウォッチリストサマリー通知送信完了。")

    return results


# =========================================================
# デーモンモード（24時間自動取引ループ）
# =========================================================

DAEMON_INTERVAL_SECS = 3_600  # 開場中の次回評価までのデフォルト間隔（1時間）


def _daemon_header(
    tickers: list[str],
    interval_secs: int,
    use_screener: bool,
    screener_top_n: int,
) -> None:
    bar = "◆" * (_W + 2)
    h   = interval_secs // 3600
    m   = (interval_secs % 3600) // 60
    print(f"\n{bar}")
    print(f"  🤖  [DAEMON MODE]  24時間自動取引ボット起動  🤖")
    if use_screener:
        print(f"  モード              : S&P500 スクリーニング → 上位 {screener_top_n} 銘柄を AI 分析")
    else:
        print(f"  対象ティッカー      : {', '.join(tickers)}")
    print(f"  開場中インターバル  : {h}h {m:02d}m  ({interval_secs}秒)")
    print(f"  停止方法            : Ctrl+C")
    print(f"{bar}\n")


def run_daemon(
    ticker:          str            = TARGET_TICKER,
    tickers:         list[str] | None = None,
    notify_line:     bool           = False,
    mock_mode:       bool           = False,
    hybrid_mode:     bool           = False,
    excluded_agents: list[str] | None = None,
    interval_secs:   int            = DAEMON_INTERVAL_SECS,
    use_screener:    bool           = False,
    screener_top_n:  int            = 5,
) -> None:
    """
    デーモンモード: 市場開閉に合わせて自動でスリープ/実行を繰り返す無限ループ。

    - 開場中  : スクリーニング（オプション）→ ウォッチリストサイクルを実行 → interval_secs 後に再評価
    - 閉場中  : Alpaca から next_open を取得 → その時刻まで冬眠（翌日にキャッシュ無効化）
    - エラー時: 60 秒後にリトライ

    Args:
        ticker:         単一銘柄指定（tickers/use_screener 未指定時の後方互換）
        tickers:        固定ウォッチリスト（指定時は use_screener を無視）
        use_screener:   True の場合、毎サイクル S&P500 をスクリーニングして銘柄選出
        screener_top_n: スクリーニングで選出する銘柄数
    """
    # 固定ウォッチリスト（tickers 指定時）/ スクリーナー自動選出 / 単一銘柄 の優先順位
    _fixed_tickers: list[str] | None = tickers  # None のときはスクリーナーまたは単一銘柄
    _daemon_header(
        tickers        = _fixed_tickers or [f"S&P500 Top-{screener_top_n}"],
        interval_secs  = interval_secs,
        use_screener   = use_screener and not _fixed_tickers,
        screener_top_n = screener_top_n,
    )

    while True:
        # ── 市場状態チェック ─────────────────────────────────────
        try:
            _alpaca_chk = _AlpacaClient()
            is_open, market_msg = _alpaca_chk.is_market_open()
        except Exception as e:
            _log(f"[Daemon] Alpaca 接続エラー: {e} → 60秒後にリトライ")
            time.sleep(60)
            continue

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'━' * (_W + 2)}")
        print(f"  [Daemon] {now_str}  市場状態: {market_msg}")
        print(f"{'━' * (_W + 2)}")

        if is_open:
            # ── 銘柄リスト決定 ───────────────────────────────────
            if _fixed_tickers:
                effective_tickers = _fixed_tickers
            elif use_screener:
                # スクリーナー: use_cache=True で同日中はキャッシュを再利用
                _log("[Daemon] S&P500 スクリーニングを実行します...")
                try:
                    screened = _screener_mod.screen_sp500(
                        top_n     = screener_top_n,
                        use_cache = True,
                        verbose   = True,
                    )
                    effective_tickers = [s["ticker"] for s in screened]
                except Exception as e:
                    _log(f"[Daemon] スクリーナーエラー: {e} → {TARGET_TICKER} にフォールバック")
                    effective_tickers = [TARGET_TICKER]
                # スクリーナー結果が空の場合もフォールバック
                if not effective_tickers:
                    _log(f"[Daemon] スクリーナー結果 0 件 → {TARGET_TICKER} にフォールバック")
                    effective_tickers = [TARGET_TICKER]
            else:
                effective_tickers = [ticker]

            # ── 開場中: ウォッチリストサイクル実行 ───────────────
            try:
                if len(effective_tickers) == 1:
                    run_trade_cycle(
                        ticker          = effective_tickers[0],
                        dry_run         = False,
                        notify_line     = notify_line,
                        mock_mode       = mock_mode,
                        hybrid_mode     = hybrid_mode,
                        excluded_agents = excluded_agents,
                    )
                else:
                    run_watchlist_cycle(
                        tickers         = effective_tickers,
                        dry_run         = False,
                        notify_line     = notify_line,
                        mock_mode       = mock_mode,
                        hybrid_mode     = hybrid_mode,
                        excluded_agents = excluded_agents,
                    )
            except KeyboardInterrupt:
                raise
            except Exception as e:
                _log(f"[Daemon] パイプライン実行エラー: {e}")

            next_check = datetime.datetime.now() + datetime.timedelta(seconds=interval_secs)
            h = interval_secs // 3600
            m = (interval_secs % 3600) // 60
            _log(
                f"[Daemon] 次回評価まで {h}h {m:02d}m スリープします "
                f"(再開: {next_check.strftime('%Y-%m-%d %H:%M:%S')})"
            )
            time.sleep(interval_secs)

        else:
            # ── 閉場中: 次回開場まで冬眠 → 翌日にキャッシュ無効化 ──
            sleep_secs = _alpaca_chk.get_next_open_seconds()
            wake_dt    = datetime.datetime.now() + datetime.timedelta(seconds=sleep_secs)
            wake_str   = wake_dt.strftime("%Y-%m-%d %H:%M:%S")
            h = sleep_secs // 3600
            m = (sleep_secs % 3600) // 60
            print(f"\n╔{'═' * _W}╗")
            print(f"║  [Daemon] 市場閉場中。".ljust(_W + 1) + "║")
            print(f"║  次回開場時刻の {wake_str} までスリープします。".ljust(_W + 1) + "║")
            print(f"║  ({h}h {m:02d}m 後に自動起動)".ljust(_W + 1) + "║")
            print(f"╚{'═' * _W}╝\n")
            time.sleep(sleep_secs)
            # 翌日開場前にスクリーナーキャッシュを無効化（新鮮な銘柄リストを取得させる）
            if use_screener and not _fixed_tickers:
                _screener_mod.invalidate_cache()


# =========================================================
# CLI エントリポイント
# =========================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="ECC スイングトレード自律エンジン",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── 銘柄指定 ─────────────────────────────────────────────────
    ticker_group = parser.add_mutually_exclusive_group()
    ticker_group.add_argument(
        "--ticker", default=TARGET_TICKER, help="単一銘柄の分析対象ティッカー",
    )
    ticker_group.add_argument(
        "--tickers", nargs="+", metavar="TICKER",
        help="複数銘柄を指定（例: --tickers AAPL MSFT NVDA）。"
             "指定した銘柄を順番に AI 分析する（ウォッチリストモード）。",
    )
    ticker_group.add_argument(
        "--screen", action="store_true",
        help=(
            "S&P500 スクリーニングモード: LLM を使わずテクニカルスコアで上位銘柄を絞り込み、"
            "そのまま AI 分析まで実行する。"
        ),
    )

    # ── スクリーニング設定 ───────────────────────────────────────
    parser.add_argument(
        "--top-n", type=int, default=5, metavar="N",
        help="--screen / デーモン+スクリーニング時の選出銘柄数（デフォルト: 5）",
    )
    parser.add_argument(
        "--screen-only", action="store_true",
        help="スクリーニング結果の表示のみ行い、AI 分析は実行しない（確認用）",
    )

    # ── 実行オプション ───────────────────────────────────────────
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Alpaca 発注をスキップしてログのみ出力",
    )
    parser.add_argument(
        "--notify-line", action="store_true",
        help="最終判断を LINE に通知",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="モックモード: LLM/API 呼び出しをスキップしてシステムフローをテスト (トークン消費ゼロ)",
    )
    parser.add_argument(
        "--hybrid", action="store_true",
        help=(
            "ハイブリッドモード: 全 Stage でリアル市場データ・リアル分析を実行し発注のみスキップ。"
            "学習データ品質向上・本番前検証に推奨。"
        ),
    )
    parser.add_argument(
        "--exclude", nargs="+", default=[], metavar="AGENT",
        help="除外するエージェント名（例: SocialAgent TechnicalAgent）。"
             "アブレーション実験用。",
    )

    # ── デーモンモード ───────────────────────────────────────────
    parser.add_argument(
        "--daemon", "--auto", action="store_true", dest="daemon",
        help=(
            "デーモンモード: 24時間稼働の自動取引ループ。"
            "--screen と組み合わせると毎サイクル S&P500 をスクリーニングして自動選出。"
        ),
    )
    parser.add_argument(
        "--interval", type=int, default=DAEMON_INTERVAL_SECS, metavar="SECONDS",
        help="デーモンモード: 市場開場中の評価間隔（秒, デフォルト: 3600=1時間）",
    )

    args = parser.parse_args()

    # ── 組み合わせバリデーション ──────────────────────────────────
    if args.screen_only and args.daemon:
        parser.error("--screen-only は --daemon と同時に指定できません。")

    # ── screen-only: スクリーニング結果のみ表示して終了 ──────────
    if args.screen_only:
        print("\n[Screen-Only モード] S&P500 をスクリーニングします...\n")
        results = _screener_mod.screen_sp500(
            top_n     = args.top_n,
            use_cache = True,
            verbose   = True,
        )
        print(f"\n上位 {len(results)} 銘柄:")
        for i, r in enumerate(results, 1):
            print(f"  {i}. {r['ticker']:<6}  スコア {r['score']:>2}  {r['reason']}")
        raise SystemExit(0)

    # ── daemon モード ─────────────────────────────────────────────
    if args.daemon:
        _fixed_tickers = args.tickers
        run_daemon(
            ticker         = args.ticker,
            tickers        = _fixed_tickers,
            notify_line    = args.notify_line,
            mock_mode      = args.mock,
            hybrid_mode    = args.hybrid,
            excluded_agents= args.exclude,
            interval_secs  = args.interval,
            use_screener   = args.screen,
            screener_top_n = args.top_n,
        )

    # ── screen モード（1回実行） ──────────────────────────────────
    elif args.screen:
        print("\n[Screen モード] S&P500 をスクリーニング中...\n")
        screened = _screener_mod.screen_sp500(
            top_n     = args.top_n,
            use_cache = True,
            verbose   = True,
        )
        if not screened:
            print("スクリーニング結果が 0 件でした。終了します。")
            raise SystemExit(1)
        effective_tickers = [s["ticker"] for s in screened]
        run_watchlist_cycle(
            tickers         = effective_tickers,
            dry_run         = args.dry_run,
            notify_line     = args.notify_line,
            mock_mode       = args.mock,
            hybrid_mode     = args.hybrid,
            excluded_agents = args.exclude,
        )

    # ── tickers モード（複数銘柄固定ウォッチリスト） ──────────────
    elif args.tickers:
        run_watchlist_cycle(
            tickers         = args.tickers,
            dry_run         = args.dry_run,
            notify_line     = args.notify_line,
            mock_mode       = args.mock,
            hybrid_mode     = args.hybrid,
            excluded_agents = args.exclude,
        )

    # ── 単一銘柄モード（従来動作） ────────────────────────────────
    else:
        run_trade_cycle(
            ticker          = args.ticker,
            dry_run         = args.dry_run,
            notify_line     = args.notify_line,
            mock_mode       = args.mock,
            hybrid_mode     = args.hybrid,
            excluded_agents = args.exclude,
        )
