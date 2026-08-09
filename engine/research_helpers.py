"""engine/research_helpers.py — 研究用実験基盤ユーティリティ

介入実験・説明信頼性スコア算出のための純関数群。
既存の実運用フローには一切影響しない（research_mode フラグで制御）。

主要関数:
    _normalize_bbs_snapshot()       — BBS → 研究用標準化スナップショット
    _identify_hold_causes()          — HOLDを引き起こしたエージェントの特定
    _flip_bbs_snapshot()             — エージェントのスコアを反転（介入実験用）
    _compute_decision_from_signals() — BBSスナップショットから判断を再計算する純関数
    _compute_gate_from_snapshot()    — Gate判定をスナップショットから再計算する純関数
    _save_hold_case()                — HOLDケースを hold_cases.jsonl に追記
"""

from __future__ import annotations

import copy
import datetime
import json
from pathlib import Path

from engine.constants import STRONG_BUY_SCORE, WEIGHTS

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RESEARCH_DIR = _PROJECT_ROOT / "data" / "research"

# ── エージェント名 ↔ weight_key ↔ BBS_key の対応表 ─────────────────────
_AGENT_TO_WEIGHT_KEY: dict[str, str] = {
    "TechnicalAgent":   "technical",
    "NewsAgent":        "news",
    "MacroAgent":       "macro",
    "SocialAgent":      "social",
    "FundamentalAgent": "fundamental",
    "LiquidityAgent":   "liquidity",
}
_WEIGHT_KEY_TO_BBS_KEY: dict[str, str] = {
    "technical":   "technical_analysis",
    "news":        "news_analysis",
    "macro":       "macro_analysis",
    "social":      "social_analysis",
    "fundamental": "fundamental_analysis",
    "liquidity":   "liquidity_analysis",
}
# weight_key → AgentName (逆引き)
_WEIGHT_KEY_TO_AGENT: dict[str, str] = {v: k for k, v in _AGENT_TO_WEIGHT_KEY.items()}

_ALL_WEIGHT_KEYS: list[str] = list(WEIGHTS.keys())


# ── ローカルシグナルユーティリティ（agent_wrappers の循環依存を回避）─────

def _safe_float(val: object, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        f = float(val)
        return default if f != f else round(max(-1.0, min(1.0, f)), 4)
    except (TypeError, ValueError):
        return default


_SIGNAL_MAP_LOCAL: dict[str, float] = {
    "positive": +1.0, "neutral": 0.0, "negative": -1.0,
}


def _prefer_score_local(data: dict, trend_key: str = "trend") -> float:
    """agent_wrappers._prefer_score の等価実装（循環依存回避のため複製）。"""
    score_raw = data.get("score")
    if score_raw is not None:
        return _safe_float(score_raw)
    return _SIGNAL_MAP_LOCAL.get((data.get(trend_key) or "neutral").lower(), 0.0)


def _extract_news_score_local(news_data: dict, ticker: str) -> float:
    """agent_wrappers._extract_news_signal の等価実装（スコアのみ返す）。"""
    articles = news_data.get("articles", [])
    target = [
        a for a in articles
        if a.get("ticker", "").upper() == ticker.upper()
        or a.get("company", "").upper() == ticker.upper()
    ]
    if not target:
        target = articles
    if not target:
        return 0.0
    scores: list[float] = []
    for a in target:
        if "score" in a and a["score"] is not None:
            scores.append(_safe_float(a["score"]))
        else:
            scores.append(_SIGNAL_MAP_LOCAL.get(a.get("sentiment", "neutral"), 0.0))
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def _numeric_to_signal_str(score: float, upper: bool = False) -> str:
    s = "positive" if score > 0.0 else "negative" if score < 0.0 else "neutral"
    return s.upper() if upper else s


def _flip_signal_str(signal: str) -> str:
    """positive↔negative を反転。neutral はそのまま。元の大文字/小文字を保持。"""
    s = signal.strip()
    low = s.lower()
    if low == "positive":
        flipped = "negative"
    elif low == "negative":
        flipped = "positive"
    else:
        return s  # neutral / unknown → 変更なし
    if s.isupper():
        return flipped.upper()
    if s[:1].isupper():
        return flipped.capitalize()
    return flipped


# ── BBS スナップショット作成 ─────────────────────────────────────────────

def _normalize_bbs_snapshot(bbs: object, ticker: str) -> dict:
    """
    BBSから全エージェントのデータを読み込み、研究用標準化スナップショットを作成する。

    各エージェントデータに以下のフィールドを付与（反転介入の基準値）:
        _score  (float) : ManagerAgentが使用するシグナル値（-1.0〜+1.0）
        _signal (str)   : 人間可読なシグナル文字列（positive/negative/neutral
                          または POSITIVE/NEGATIVE/NEUTRAL）
    """
    read = bbs.read  # type: ignore[attr-defined]

    # ── TechnicalAgent ──
    tech_data = dict(read("technical_analysis") or {})
    tech_score = _prefer_score_local(tech_data)
    tech_signal = tech_data.get("trend", "neutral")
    tech_data["_score"]  = tech_score
    tech_data["_signal"] = tech_signal

    # ── NewsAgent（記事は最大3件に制限） ──
    news_data = dict(read("news_analysis") or {})
    news_data["articles"] = (news_data.get("articles") or [])[:3]
    news_score = _extract_news_score_local(news_data, ticker)
    news_signal = _numeric_to_signal_str(news_score)
    news_data["_score"]  = news_score
    news_data["_signal"] = news_signal

    # ── MacroAgent ──
    macro_data = dict(read("macro_analysis") or {})
    macro_score = _prefer_score_local(macro_data)
    macro_signal = macro_data.get("trend", "neutral")
    macro_data["_score"]  = macro_score
    macro_data["_signal"] = macro_signal

    # ── SocialAgent ──
    social_data = dict(read("social_analysis") or {})
    social_score = _safe_float(social_data.get("score", 0.0))
    social_signal = social_data.get("sentiment", "NEUTRAL")
    social_data["_score"]  = social_score
    social_data["_signal"] = social_signal

    # ── FundamentalAgent ──
    fa_data = dict(read("fundamental_analysis") or {})
    fa_score = _prefer_score_local(fa_data)
    fa_signal = fa_data.get("trend", "neutral")
    fa_data["_score"]  = fa_score
    fa_data["_signal"] = fa_signal

    # ── LiquidityAgent ──
    liq_data = dict(read("liquidity_analysis") or {})
    liq_score = _safe_float(liq_data.get("score", 0.0))
    liq_signal = liq_data.get("pressure", "neutral")
    liq_data["_score"]  = liq_score
    liq_data["_signal"] = liq_signal

    return {
        "technical_analysis":   tech_data,
        "news_analysis":        news_data,
        "macro_analysis":       macro_data,
        "social_analysis":      social_data,
        "fundamental_analysis": fa_data,
        "liquidity_analysis":   liq_data,
    }


# ── HOLD 原因エージェントの特定 ──────────────────────────────────────────

def _identify_hold_causes(
    sigs: dict[str, float],
    weights: dict[str, float],
    macro_forced_hold: bool,
) -> dict:
    """
    HOLDを引き起こしたエージェントを特定する。

    Returns:
        {
            "primary":      str,        # 最大の原因エージェント名
            "contributing": list[str],  # 負の寄与エージェント名リスト（primary含む）
        }
    """
    if macro_forced_hold:
        return {"primary": "MacroAgent", "contributing": ["MacroAgent"]}

    # ウェイト付き寄与度（負 = HOLD に寄与）
    contributions: dict[str, float] = {}
    for wkey in _ALL_WEIGHT_KEYS:
        agent_name = _WEIGHT_KEY_TO_AGENT.get(wkey, wkey)
        contributions[agent_name] = sigs.get(wkey, 0.0) * weights.get(wkey, 0.0)

    # 負の寄与エージェント（最も負 = 先頭）
    contributing = sorted(
        [name for name, contrib in contributions.items() if contrib < 0.0],
        key=lambda n: contributions[n],
    )

    if not contributing:
        # 全シグナルが非負でも閾値未達の場合 → 最小寄与を primary に
        primary = min(contributions, key=lambda n: contributions[n])
        contributing = [primary]
    else:
        primary = contributing[0]

    return {"primary": primary, "contributing": contributing}


# ── BBS スナップショットの反転 ────────────────────────────────────────────

def _flip_bbs_snapshot(snapshot: dict, weight_key: str) -> dict:
    """
    指定エージェントの _score を符号反転させたスナップショットのコピーを返す。
    _signal, trend, sentiment 等の関連フィールドも一貫して更新する。

    不変性: _flip_bbs_snapshot(_flip_bbs_snapshot(s, k), k)["X"]["_score"] == s["X"]["_score"]
    neutral (0.0) の反転結果は 0.0 のまま。
    """
    bbs_key = _WEIGHT_KEY_TO_BBS_KEY.get(weight_key)
    if not bbs_key:
        return snapshot

    result = copy.deepcopy(snapshot)
    entry: dict = result.get(bbs_key, {})
    if not entry:
        return result

    # _score 反転
    entry["_score"] = round(-float(entry.get("_score", 0.0)), 4)

    # _signal 反転
    entry["_signal"] = _flip_signal_str(str(entry.get("_signal", "neutral")))

    # raw フィールドも更新（ManagerAgentが参照するフィールド）
    if "trend" in entry:
        entry["trend"] = _flip_signal_str(entry["trend"])
    if "sentiment" in entry:  # SocialAgent: POSITIVE/NEGATIVE/NEUTRAL
        entry["sentiment"] = _flip_signal_str(entry["sentiment"])
    if "score" in entry and isinstance(entry["score"], (int, float)):
        entry["score"] = round(-float(entry["score"]), 4)
    if "avg_sentiment_score" in entry:
        entry["avg_sentiment_score"] = round(-float(entry["avg_sentiment_score"]), 4)

    # NewsAgent: 記事スコア・センチメントを反転
    for art in entry.get("articles", []):
        if isinstance(art.get("score"), (int, float)):
            art["score"] = round(-float(art["score"]), 4)
        if "sentiment" in art:
            art["sentiment"] = _flip_signal_str(art["sentiment"])

    # LiquidityAgent: pressure を反転
    if "pressure" in entry:
        _pressure_flip = {"buy_dominant": "sell_dominant", "sell_dominant": "buy_dominant"}
        entry["pressure"] = _pressure_flip.get(entry["pressure"], entry["pressure"])

    result[bbs_key] = entry
    return result


# ── 判断の純計算（APIコールなし）─────────────────────────────────────────

def _compute_decision_from_signals(
    bbs_snapshot: dict,
    ticker: str,
    weights: dict[str, float],
    excluded_keys: list[str] | None = None,
) -> dict:
    """
    BBSスナップショットから ManagerAgent と同等のスコア計算を行う純関数。
    APIコール・LLM呼び出しなし。介入実験専用。

    スナップショットの各エントリの _score フィールドを使用する。
    excluded_keys に含まれるエージェントのシグナルは 0.0 として扱う。

    Returns:
        {
            "decision":          "STRONG BUY" | "HOLD",
            "score":             float,
            "macro_forced_hold": bool,
            "signals":           dict[str, float],
            "is_strong_buy":     bool,
        }
    """
    excluded = set(excluded_keys or [])

    def _get(bbs_k: str, wkey: str) -> float:
        if wkey in excluded:
            return 0.0
        return float(bbs_snapshot.get(bbs_k, {}).get("_score", 0.0))

    tech_sig  = _get("technical_analysis",   "technical")
    news_sig  = _get("news_analysis",        "news")
    macro_sig = _get("macro_analysis",       "macro")
    fa_sig    = _get("fundamental_analysis", "fundamental")
    soc_sig   = _get("social_analysis",      "social")
    liq_sig   = _get("liquidity_analysis",   "liquidity")

    sigs = {
        "technical":   tech_sig,
        "news":        news_sig,
        "macro":       macro_sig,
        "fundamental": fa_sig,
        "social":      soc_sig,
        "liquidity":   liq_sig,
    }

    score = round(sum(sigs[k] * weights.get(k, 0.0) for k in sigs), 4)
    macro_forced_hold = macro_sig < 0.0 and "macro" not in excluded

    is_strong_buy = (
        not macro_forced_hold
        and score    >= STRONG_BUY_SCORE
        and fa_sig   >  0.0
        and tech_sig >= 0.0
        and news_sig >= 0.0
    )

    return {
        "decision":          "STRONG BUY" if is_strong_buy else "HOLD",
        "score":             score,
        "macro_forced_hold": macro_forced_hold,
        "signals":           sigs,
        "is_strong_buy":     is_strong_buy,
    }


def _compute_decision_research(
    bbs_snapshot: dict,
    ticker: str,
    weights: dict[str, float],
    excluded_keys: list[str] | None = None,
) -> dict:
    """
    研究用緩和条件での判断計算（APIコールなし・介入実験専用）。

    _compute_decision_from_signals との違い:
        - fundamental > 0.0 必須条件なし
        - tech_sig >= 0.0 / news_sig >= 0.0 の条件なし
        - 加重スコア > threshold のみで STRONG BUY 判定

    実運用コードには使用しないこと。
    """
    excluded = set(excluded_keys or [])

    def _get(bbs_k: str, wkey: str) -> float:
        if wkey in excluded:
            return 0.0
        return float(bbs_snapshot.get(bbs_k, {}).get("_score", 0.0))

    tech_sig  = _get("technical_analysis",   "technical")
    news_sig  = _get("news_analysis",        "news")
    macro_sig = _get("macro_analysis",       "macro")
    fa_sig    = _get("fundamental_analysis", "fundamental")
    soc_sig   = _get("social_analysis",      "social")
    liq_sig   = _get("liquidity_analysis",   "liquidity")

    sigs = {
        "technical":   tech_sig,
        "news":        news_sig,
        "macro":       macro_sig,
        "fundamental": fa_sig,
        "social":      soc_sig,
        "liquidity":   liq_sig,
    }

    score = round(sum(sigs[k] * weights.get(k, 0.0) for k in sigs), 4)
    macro_forced_hold = macro_sig < 0.0 and "macro" not in excluded

    # 緩和条件: macro 強制 HOLD でなく、加重スコアが閾値超えなら STRONG BUY
    is_strong_buy = not macro_forced_hold and score > STRONG_BUY_SCORE

    return {
        "decision":          "STRONG BUY" if is_strong_buy else "HOLD",
        "score":             score,
        "macro_forced_hold": macro_forced_hold,
        "signals":           sigs,
        "is_strong_buy":     is_strong_buy,
        "research_relaxed":  True,
    }


def _compute_gate_from_snapshot(
    bbs_snapshot: dict,
    excluded_keys: list[str] | None = None,
) -> dict:
    """
    Stage 1 Gate チェックをスナップショットから再計算する純関数（Gate HOLD 介入用）。

    Returns:
        {
            "skip_fundamental": bool,
            "macro_brake":      bool,
            "tech_signal":      float,
            "news_signal":      float,
            "macro_signal":     float,
        }
    """
    excluded = set(excluded_keys or [])

    tech_sig  = 0.0 if "technical" in excluded else float(bbs_snapshot.get("technical_analysis",  {}).get("_score", 0.0))
    news_sig  = 0.0 if "news"      in excluded else float(bbs_snapshot.get("news_analysis",        {}).get("_score", 0.0))
    macro_sig = 0.0 if "macro"     in excluded else float(bbs_snapshot.get("macro_analysis",       {}).get("_score", 0.0))

    macro_brake  = macro_sig < 0.0
    signals_flat = tech_sig <= 0.0 and news_sig <= 0.0

    return {
        "skip_fundamental": macro_brake or signals_flat,
        "macro_brake":      macro_brake,
        "tech_signal":      tech_sig,
        "news_signal":      news_sig,
        "macro_signal":     macro_sig,
    }


# ── HOLD ケースの保存 ────────────────────────────────────────────────────

def _save_hold_case(
    ticker: str,
    judgment: dict,
    bbs: object | None,
    hold_type: str,
    effective_weights: dict[str, float],
    *,
    bbs_snapshot: dict | None = None,
) -> str:
    """
    HOLDケースを data/research/hold_cases.jsonl に追記する。

    Args:
        ticker:            対象銘柄ティッカー
        judgment:          ManagerAgent/GateAgent の判断 dict
        bbs:               BBS インスタンス（スナップショット取得用）。
                           bbs_snapshot を直接渡す場合は None でよい。
        hold_type:         "gate" | "manager"
        effective_weights: 実際に使用されたウェイト dict
        bbs_snapshot:      事前に構築済みのスナップショット dict（省略時は bbs から生成）

    Returns:
        case_id (str)
    """
    _RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    if bbs_snapshot is None:
        bbs_snapshot = _normalize_bbs_snapshot(bbs, ticker)

    macro_hold = bool(
        judgment.get("macro_forced_hold")
        or judgment.get("macro_brake")
        or judgment.get("macro_is_suspended", False)
    )
    causes = _identify_hold_causes(
        sigs=judgment.get("signals", {}),
        weights=effective_weights,
        macro_forced_hold=macro_hold,
    )

    case_id = f"{ticker}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    case: dict = {
        "case_id":              case_id,
        "ticker":               ticker,
        "date":                 datetime.date.today().isoformat(),
        "session_id":           getattr(bbs, "session_id", None) or judgment.get("session_id", "unknown"),
        "hold_type":            hold_type,
        "decision":             judgment.get("decision", "HOLD"),
        "score":                judgment.get("score", 0.0),
        "threshold":            judgment.get("threshold", STRONG_BUY_SCORE),
        "macro_forced_hold":    macro_hold,
        "signals":              judgment.get("signals", {}),
        "weights":              effective_weights,
        "primary_reason_agent": causes["primary"],
        "contributing_agents":  causes["contributing"],
        "explanation":          judgment.get("rationale", judgment.get("gate_reason", "")),
        "gate_reason":          judgment.get("gate_reason"),
        "bbs_snapshot":         bbs_snapshot,
    }

    hold_cases_file = _RESEARCH_DIR / "hold_cases.jsonl"
    with hold_cases_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")

    return case_id
