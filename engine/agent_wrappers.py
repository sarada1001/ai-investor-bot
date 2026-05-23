"""engine/agent_wrappers.py — Stage 0〜4 エージェントラッパークラス群"""

from __future__ import annotations

import yaml
from pathlib import Path

from skills.llm_factory import get_llm_instance, is_ollama_active

import skills.news_monitor           as _news_mod
import skills.technical_calc         as _tech_mod
import skills.macro_monitor          as _macro_mod
import skills.social_monitor         as _social_mod
import skills.risk_calculator        as _risk_mod
from agents.fundamental_agent import FundamentalAgent as _FundamentalAgentImpl
from agents.exit_agent        import ExitAgent        as _ExitAgentImpl

from engine.bbs       import BBS
from engine.constants import (
    TARGET_TICKER, STRONG_BUY_SCORE, WEIGHTS, SIGNAL_MAP,
    SOCIAL_HYPE_THRESHOLD, _STRONG_BUY_LABEL, _HOLD_LABEL, _W,
)
from engine.display import _log, _sep, _phase_header, _phase_footer

_RISK_PCT    = 2  # リスク割合表示用（固定値）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # engine/ → プロジェクトルート


def _load_agent_config(agent_name: str) -> dict:
    path = _PROJECT_ROOT / ".agents" / f"{agent_name}.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── シグナル変換ヘルパー ──────────────────────────────────────

def _trend_to_signal(trend_str: str) -> float:
    return SIGNAL_MAP.get((trend_str or "neutral").lower(), 0.0)


def _extract_news_signal(news_data: dict, ticker: str) -> tuple[float, str]:
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


def _gate_check(bbs: BBS, ticker: str) -> dict:
    tech_data  = bbs.read("technical_analysis") or {}
    news_data  = bbs.read("news_analysis")       or {}
    macro_data = bbs.read("macro_analysis")      or {}

    tech_sig             = _trend_to_signal(tech_data.get("trend", "neutral"))
    news_sig, _          = _extract_news_signal(news_data, ticker)
    # MacroAgent が SUSPENDED (shadow mode) の場合はブレーキを発動させない
    macro_is_shadow = bool(macro_data.get("suspended"))
    macro_sig       = 0.0 if macro_is_shadow else _trend_to_signal(macro_data.get("trend", "neutral"))

    macro_brake  = macro_sig < 0.0
    signals_flat = tech_sig <= 0.0 and news_sig <= 0.0
    skip         = macro_brake or signals_flat

    if macro_brake:
        reason = "マクロ環境 NEGATIVE → 市場全体がリスクオフ（ブレーキ発動）"
    elif signals_flat:
        reason = "Tech・News 両シグナルが NEUTRAL 以下 → Fundamental スキップ（コスト節約）"
    else:
        reason = "少なくとも 1 シグナルが POSITIVE → Stage 2 (Fundamental) へ進む"

    return {
        "tech_signal":        tech_sig,
        "news_signal":        news_sig,
        "macro_signal":       macro_sig,
        "macro_brake":        macro_brake,
        "macro_is_suspended": macro_is_shadow,
        "skip_fundamental":   skip,
        "reason":             reason,
    }


def _gate_display(gate: dict) -> None:
    skip        = gate["skip_fundamental"]
    tech_sig    = gate["tech_signal"]
    news_sig    = gate["news_signal"]
    macro_sig   = gate["macro_signal"]
    macro_brake = gate["macro_brake"]

    def _icon(v: float) -> str:
        return "📈" if v > 0 else "📉" if v < 0 else "➡️"

    def _label(v: float) -> str:
        return "positive" if v > 0 else "negative" if v < 0 else "neutral"

    macro_is_suspended = gate.get("macro_is_suspended", False)
    macro_note = (
        "  ← 🔴 SUSPENDED (neutral 扱い)" if macro_is_suspended
        else ("  ← ⚠ ブレーキ発動！" if macro_brake else "")
    )
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


# ── Stage 1-A: TechnicalAgent ────────────────────────────────

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
        rsi  = ind.get("rsi",   {})
        macd = ind.get("macd",  {})
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


# ── Stage 1-B: NewsAgent ─────────────────────────────────────

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


# ── Stage 1-C: MacroAgent ────────────────────────────────────

class MacroAgent:
    NAME = "MacroAgent"

    def __init__(self, bbs: BBS):
        self.bbs  = bbs
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


# ── Stage 1-D: SocialAgent ───────────────────────────────────

class SocialAgent:
    NAME = "SocialAgent"

    def __init__(self, bbs: BBS):
        self.bbs = bbs

    def run(self, ticker: str = TARGET_TICKER, phase_tag: str = "S1-4/4") -> dict:
        import os as _os
        _mock_enabled = _os.getenv("SOCIAL_USE_MOCK", "false").lower() == "true"
        _source_note = (
            "mock_reddit_wsb (SOCIAL_USE_MOCK=true)"
            if _mock_enabled
            else "中立モード (SOCIAL_USE_MOCK=false / SNS API 未接続)"
        )
        _phase_header(phase_tag, self.NAME)
        _log(f"{ticker} のSNSセンチメントを分析中 ({_source_note})...")
        _sep()

        try:
            result = _social_mod.fetch_social_sentiment(ticker, hype_mode=True)
        except Exception as e:
            result = {
                "ticker": ticker, "sentiment": "NEUTRAL", "hype_score": 0.0,
                # hype_score=0.0: エラー時は「不明」として最も安全な中立扱い。
                # 0.5 にするとUIのhypeバーが半分表示になり誤解を招くため 0.0 に修正。
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


# ── Stage 0: ExitAgent ───────────────────────────────────────

class ExitAgent:
    NAME = "ExitAgent"

    def __init__(self, bbs: BBS) -> None:
        self.bbs = bbs

    def run(
        self,
        mock_mode: bool = False,
        alpaca_client   = None,
        phase_tag: str  = "S0",
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


# ── Stage 2: FundamentalAgent ────────────────────────────────

class FundamentalAgent:
    NAME = "FundamentalAgent"

    def __init__(self, bbs: BBS):
        self.bbs = bbs

    def run(
        self,
        ticker: str             = TARGET_TICKER,
        phase_tag: str          = "S2",
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

        if not allow_edgar_fetch:
            fa._fetch_from_edgar = lambda t: {          # type: ignore[method-assign]
                "ticker": t, "form": "", "chunks_added": 0,
                "error": "EDGAR fetch disabled (allow_edgar_fetch=False)",
            }

        result = fa.analyze(ticker)

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


# ── Stage 3: ManagerAgent ────────────────────────────────────

class ManagerAgent:
    NAME = "ManagerAgent"

    def __init__(self, bbs: BBS):
        self.bbs  = bbs
        self._llm = get_llm_instance()

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
        ticker: str                     = TARGET_TICKER,
        dry_run: bool                   = False,
        phase_tag: str                  = "S3",
        mock_mode: bool                 = False,
        effective_weights: dict | None  = None,
        excluded_keys: list[str] | None = None,
    ) -> dict:
        from engine.trade_helpers import _fetch_wiki_context  # avoid circular at module level

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

        news_sig, news_reason = _extract_news_signal(news_data, ticker)
        tech_sig              = _trend_to_signal(tech_data.get("trend", "neutral"))
        tech_reason           = tech_data.get("trend_reason", "データなし")
        fa_sig                = _trend_to_signal(fa_data.get("trend", "neutral"))
        fa_reason             = fa_data.get("trend_reason", "データなし")
        macro_sig             = _trend_to_signal(macro_data.get("trend", "neutral"))
        macro_reason          = macro_data.get("trend_reason", "データなし")

        _SOCIAL_MAP          = {"POSITIVE": +1.0, "NEUTRAL": 0.0, "NEGATIVE": -1.0}
        social_raw_sentiment = social_data.get("sentiment", "NEUTRAL").upper()
        social_hype_score    = float(social_data.get("hype_score", 0.0))
        social_reason_base   = social_data.get("reason", "データなし")

        raw_social_sig      = _SOCIAL_MAP.get(social_raw_sentiment, 0.0)
        social_hype_penalty = False
        social_sig          = raw_social_sig

        if (social_raw_sentiment == "POSITIVE"
                and social_hype_score >= SOCIAL_HYPE_THRESHOLD
                and not (fa_sig > 0.0 and tech_sig >= 0.0)):
            social_sig          = -0.5
            social_hype_penalty = True

        social_reason = (
            f"[買い煽りペナルティ hype={social_hype_score:.2f}] {social_reason_base}"
            if social_hype_penalty else social_reason_base
        )

        score = round(
            news_sig     * w["news"]
            + fa_sig     * w["fundamental"]
            + tech_sig   * w["technical"]
            + macro_sig  * w["macro"]
            + social_sig * w["social"],
            4,
        )

        macro_forced_hold = macro_sig < 0.0 and "macro" not in excluded_keys

        is_strong_buy = (
            not macro_forced_hold
            and score    >= STRONG_BUY_SCORE
            and fa_sig   >  0.0
            and tech_sig >= 0.0
            and news_sig >= 0.0
        )
        decision = _STRONG_BUY_LABEL if is_strong_buy else _HOLD_LABEL

        sigs    = {
            "news": news_sig, "fundamental": fa_sig,
            "technical": tech_sig, "macro": macro_sig, "social": social_sig,
        }
        reasons = {
            "news": news_reason, "fundamental": fa_reason,
            "technical": tech_reason, "macro": macro_reason, "social": social_reason,
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

        wiki_ctx = _fetch_wiki_context(ticker)
        if wiki_ctx:
            _log("📚 Wiki 過去実績:")
            for line in wiki_ctx.splitlines():
                _log(f"  {line}")
            _sep()

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
            _llm_backend = "ollama" if is_ollama_active() else "gemini"
            _log(f"根拠テキスト生成中 ({_llm_backend})...")
            rationale = self._build_rationale(ticker, decision, score, sigs, reasons,
                                              wiki_context=wiki_ctx)

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

        judgment = {
            "ticker":              ticker,
            "decision":            decision,
            "score":               score,
            "threshold":           STRONG_BUY_SCORE,
            "signals":             sigs,
            "signal_reasons":      reasons,
            "macro_forced_hold":   macro_forced_hold,
            "social_hype_penalty": social_hype_penalty,
            "social_hype_score":   social_hype_score,
            "rationale":           rationale,
            "order":               None,
            "is_strong_buy":       is_strong_buy,
            "dry_run":             dry_run,
        }
        self.bbs.write(self.NAME, "manager_judgment", judgment)
        _phase_footer()
        return judgment


# ── Stage 4: RiskAgent ───────────────────────────────────────

class RiskAgent:
    NAME = "RiskAgent"

    def __init__(self, bbs: BBS):
        self.bbs = bbs

    def run(self, ticker: str = TARGET_TICKER, phase_tag: str = "S4",
            account_equity: float | None = None) -> dict:
        _phase_header(phase_tag, self.NAME)
        cfg             = _load_agent_config("risk_agent")
        _cfg_balance    = float(cfg.get("params", {}).get("account_balance", 100_000.0))
        _max_budget     = cfg.get("params", {}).get("max_budget")
        account_balance = account_equity if account_equity and account_equity > 0 else _cfg_balance
        if _max_budget:
            account_balance = min(account_balance, float(_max_budget))
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
