"""engine/mock_helpers.py — LLM/API 呼び出しをスキップするモック Stage 実行"""

from __future__ import annotations

from engine.bbs      import BBS
from engine.constants import MOCK_BBS_DATA
from engine.display   import _log, _sep, _phase_header, _phase_footer
from agents.audit_agent import COACHING_PROMPTS as _COACHING_PROMPTS


def _run_mock_stage1(
    bbs: BBS,
    ticker: str,
    excluded_keys:  list[str] | None = None,
    suspended_keys: list[str] | None = None,
) -> None:
    """Stage 1 の各エージェントをモックデータで代替する（API 呼び出しなし）。"""
    excluded_keys  = excluded_keys  or []
    suspended_keys = suspended_keys or []
    note = "⚠️  [MOCK] LLM スキップ — ダミーデータを BBS に書き込み"

    def _write_shadow_mock(bbs_key: str, agent_name: str, shadow_data: dict) -> None:
        coaching = _COACHING_PROMPTS.get(agent_name, "")
        bbs.write("AuditAgent", f"shadow_{bbs_key}", {
            **shadow_data, "_shadow_mode": True, "_coaching_prompt": coaching,
        })
        bbs.write(agent_name, bbs_key, {
            "trend": "neutral", "suspended": True,
            "trend_reason": f"{agent_name} SUSPENDED (shadow mode) — ウェイト=0",
        })

    _phase_header("S1-1/5", "TechnicalAgent")
    _log(note)
    _sep()
    if "technical" in excluded_keys:
        _log("  ⚠️  [アブレーション] TechnicalAgent を除外 → NEUTRAL エントリを書き込み")
        bbs.write("TechnicalAgent", "technical_analysis",
                  {"trend": "neutral", "excluded": True, "trend_reason": "TechnicalAgent 除外済"})
    elif "technical" in suspended_keys:
        _log("  🔴 [SUSPENDED] TechnicalAgent — Shadow Mode (本番スコア影響なし)")
        shadow_data: dict = {**MOCK_BBS_DATA["technical_analysis"], "ticker": ticker}
        _write_shadow_mock("technical_analysis", "TechnicalAgent", shadow_data)
    else:
        data: dict = {**MOCK_BBS_DATA["technical_analysis"], "ticker": ticker}
        _log("テクニカルトレンド判定: 📈 POSITIVE")
        _log(f"根拠: {data['trend_reason']}")
        bbs.write("TechnicalAgent", "technical_analysis", data)
    _phase_footer()

    _phase_header("S1-2/5", "NewsAgent")
    _log(note)
    _sep()
    if "news" in excluded_keys:
        _log("  ⚠️  [アブレーション] NewsAgent を除外 → NEUTRAL エントリを書き込み")
        bbs.write("NewsAgent", "news_analysis",
                  {"articles": [], "avg_sentiment_score": 0.0, "excluded": True,
                   "trend": "neutral", "trend_reason": "NewsAgent 除外済"})
    elif "news" in suspended_keys:
        _log("  🔴 [SUSPENDED] NewsAgent — Shadow Mode (本番スコア影響なし)")
        articles = [
            {**a, "ticker": ticker, "company": ticker,
             "title": a.get("title", "").replace("AAPL", ticker),
             "reason": a.get("reason", "").replace("AAPL", ticker)}
            for a in MOCK_BBS_DATA["news_analysis"]["articles"]
        ]
        shadow_news = {**MOCK_BBS_DATA["news_analysis"], "ticker": ticker, "articles": articles}
        _write_shadow_mock("news_analysis", "NewsAgent", shadow_news)
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

    _phase_header("S1-3/5", "MacroAgent")
    _log(note)
    _sep()
    if "macro" in excluded_keys:
        _log("  ⚠️  [アブレーション] MacroAgent を除外 → NEUTRAL エントリを書き込み")
        bbs.write("MacroAgent", "macro_analysis",
                  {"trend": "neutral", "excluded": True, "trend_reason": "MacroAgent 除外済"})
    elif "macro" in suspended_keys:
        _log("  🔴 [SUSPENDED] MacroAgent — Shadow Mode (本番スコア影響なし)")
        shadow_macro = {**MOCK_BBS_DATA["macro_analysis"]}
        _write_shadow_mock("macro_analysis", "MacroAgent", shadow_macro)
    else:
        data = {**MOCK_BBS_DATA["macro_analysis"]}
        _log("マクロ環境判定: ➡️  NEUTRAL")
        _log(f"根拠: {data['trend_reason']}")
        bbs.write("MacroAgent", "macro_analysis", data)
    _phase_footer()

    _phase_header("S1-4/5", "SocialAgent")
    _log(note)
    _sep()
    if "social" in excluded_keys:
        _log("  ⚠️  [アブレーション] SocialAgent を除外 → NEUTRAL エントリを書き込み")
        bbs.write("SocialAgent", "social_analysis",
                  {"sentiment": "NEUTRAL", "hype_score": 0.0, "excluded": True,
                   "reason": "SocialAgent 除外済", "post_count": 0})
    elif "social" in suspended_keys:
        _log("  🔴 [SUSPENDED] SocialAgent — Shadow Mode (本番スコア影響なし)")
        shadow_social = {**MOCK_BBS_DATA["social_analysis"], "ticker": ticker}
        _write_shadow_mock("social_analysis", "SocialAgent", shadow_social)
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

    _phase_header("S1-5/5", "LiquidityAgent")
    _log("⚠️  [MOCK] Python 演算スキップ — ダミーデータを BBS に書き込み")
    _sep()
    if "liquidity" in excluded_keys:
        _log("  ⚠️  [アブレーション] LiquidityAgent を除外 → NEUTRAL エントリを書き込み")
        bbs.write("LiquidityAgent", "liquidity_analysis",
                  {"verbal_annotation": "LiquidityAgent 除外済", "score": 0.0,
                   "ask_ratio": 0.5, "bid_ratio": 0.5, "net_large_inflow": 0.0,
                   "net_small_inflow": 0.0, "pressure": "neutral",
                   "excluded": True, "data_source": "excluded"})
    elif "liquidity" in suspended_keys:
        _log("  🔴 [SUSPENDED] LiquidityAgent — Shadow Mode (本番スコア影響なし)")
        shadow_liq = {**MOCK_BBS_DATA["liquidity_analysis"], "ticker": ticker}
        _write_shadow_mock("liquidity_analysis", "LiquidityAgent", shadow_liq)
    else:
        liq_mock = {**MOCK_BBS_DATA["liquidity_analysis"], "ticker": ticker}
        score   = liq_mock["score"]
        filled  = int((score + 1.0) / 2.0 * 10)
        s_bar   = "█" * filled + "░" * (10 - filled)
        s_icon  = "📈" if score > 0 else "📉" if score < 0 else "➡️"
        _log(f"データソース    : {liq_mock['data_source']}")
        _log(f"Ask/Bid 比率    : Ask {liq_mock['ask_ratio']:.0%}  /  Bid {liq_mock['bid_ratio']:.0%}")
        _log(f"大口純流入額    : ${liq_mock['net_large_inflow']:+,.0f}")
        _sep()
        _log(f"Verbal Annotation:")
        _log(f"  {liq_mock['verbal_annotation'][:90]}")
        _sep()
        _log(f"流動性シグナル  : {s_icon} [{s_bar}] {score:+.4f}  (pressure: {liq_mock['pressure']})")
        bbs.write("LiquidityAgent", "liquidity_analysis", liq_mock)
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
