"""
engine/bbs_schema.py — BBS共有データのdataclassスキーマ定義

各エージェントがBBSに書き込むJSONデータを型定義し、
KeyError・型不一致による連鎖バグを防止する。

使用例:
    ta = TechnicalAnalysis(trend="positive", trend_reason="MACD golden cross")
    bbs.write("TechnicalAgent", "technical_analysis", ta)

    raw = bbs.read("technical_analysis")
    ta  = TechnicalAnalysis.from_dict(raw)  # 型安全なデシリアライズ

設計方針:
    - from_dict() は必ず .get() + デフォルト値を使用 → KeyError が発生しない
    - to_dict() は canonical な最小フィールドを返す
    - 既存コードとの後方互換: BBS.write() は dict も dataclass も受け入れる
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ──────────────────────────────────────────────
# Stage 1: 分析エージェント出力スキーマ
# ──────────────────────────────────────────────

@dataclass
class TechnicalAnalysis:
    """TechnicalAgent → BBS['technical_analysis']"""
    trend:        str = "neutral"
    trend_reason: str = ""

    def to_dict(self) -> dict:
        return {"trend": self.trend, "trend_reason": self.trend_reason}

    @classmethod
    def from_dict(cls, data: dict) -> "TechnicalAnalysis":
        return cls(
            trend        = str(data.get("trend", "neutral")),
            trend_reason = str(data.get("trend_reason", "")),
        )


@dataclass
class NewsArticle:
    """NewsAnalysis.articles の各要素"""
    ticker:    str = ""
    company:   str = ""
    sentiment: str = "neutral"
    title:     str = ""
    reason:    str = ""

    def to_dict(self) -> dict:
        return {
            "ticker":    self.ticker,
            "company":   self.company,
            "sentiment": self.sentiment,
            "title":     self.title,
            "reason":    self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NewsArticle":
        return cls(
            ticker    = str(data.get("ticker",    "")),
            company   = str(data.get("company",   "")),
            sentiment = str(data.get("sentiment", "neutral")),
            title     = str(data.get("title",     "")),
            reason    = str(data.get("reason",    "")),
        )


@dataclass
class NewsAnalysis:
    """NewsAgent → BBS['news_analysis']"""
    articles:            list[NewsArticle] = field(default_factory=list)
    avg_sentiment_score: float             = 0.0

    def to_dict(self) -> dict:
        return {
            "articles":            [a.to_dict() for a in self.articles],
            "avg_sentiment_score": self.avg_sentiment_score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NewsAnalysis":
        raw_articles = data.get("articles", [])
        articles = [
            NewsArticle.from_dict(a) if isinstance(a, dict) else a
            for a in (raw_articles if isinstance(raw_articles, list) else [])
        ]
        return cls(
            articles            = articles,
            avg_sentiment_score = float(data.get("avg_sentiment_score", 0.0)),
        )


@dataclass
class MacroAnalysis:
    """MacroAgent → BBS['macro_analysis']"""
    trend:          str  = "neutral"
    trend_reason:   str  = ""
    signal_summary: str  = ""
    suspended:      bool = False

    def to_dict(self) -> dict:
        return {
            "trend":          self.trend,
            "trend_reason":   self.trend_reason,
            "signal_summary": self.signal_summary,
            "suspended":      self.suspended,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MacroAnalysis":
        return cls(
            trend          = str(data.get("trend", "neutral")),
            trend_reason   = str(data.get("trend_reason", "")),
            signal_summary = str(data.get("signal_summary", "")),
            suspended      = bool(data.get("suspended", False)),
        )


@dataclass
class SocialAnalysis:
    """SocialAgent → BBS['social_analysis']"""
    sentiment:  str   = "NEUTRAL"
    hype_score: float = 0.0
    reason:     str   = ""
    source:     str   = ""
    post_count: int   = 0

    def to_dict(self) -> dict:
        return {
            "sentiment":  self.sentiment,
            "hype_score": self.hype_score,
            "reason":     self.reason,
            "source":     self.source,
            "post_count": self.post_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SocialAnalysis":
        return cls(
            sentiment  = str(data.get("sentiment",  "NEUTRAL")),
            hype_score = float(data.get("hype_score", 0.0)),
            reason     = str(data.get("reason",     "")),
            source     = str(data.get("source",     "")),
            post_count = int(data.get("post_count", 0)),
        )


@dataclass
class FundamentalAnalysis:
    """FundamentalAgent → BBS['fundamental_analysis']"""
    trend:        str = "neutral"
    trend_reason: str = ""

    def to_dict(self) -> dict:
        return {"trend": self.trend, "trend_reason": self.trend_reason}

    @classmethod
    def from_dict(cls, data: dict) -> "FundamentalAnalysis":
        return cls(
            trend        = str(data.get("trend", "neutral")),
            trend_reason = str(data.get("trend_reason", "")),
        )


# ──────────────────────────────────────────────
# Stage 2-4: 判断・リスク・監査スキーマ
# ──────────────────────────────────────────────

@dataclass
class RiskAnalysis:
    """RiskAgent → BBS['risk_analysis']"""
    ticker:              str   = ""
    recommended_shares:  int   = 1
    stop_loss_price:     float = 0.0
    take_profit_price:   float = 0.0
    current_price:       float = 0.0
    atr:                 float = 0.0
    risk_amount:         float = 0.0
    reason:              str   = ""

    def to_dict(self) -> dict:
        return {
            "ticker":             self.ticker,
            "recommended_shares": self.recommended_shares,
            "stop_loss_price":    self.stop_loss_price,
            "take_profit_price":  self.take_profit_price,
            "current_price":      self.current_price,
            "atr":                self.atr,
            "risk_amount":        self.risk_amount,
            "reason":             self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RiskAnalysis":
        return cls(
            ticker             = str(data.get("ticker",             "")),
            recommended_shares = int(data.get("recommended_shares", 1)),
            stop_loss_price    = float(data.get("stop_loss_price",   0.0)),
            take_profit_price  = float(data.get("take_profit_price", 0.0)),
            current_price      = float(data.get("current_price",     0.0)),
            atr                = float(data.get("atr",               0.0)),
            risk_amount        = float(data.get("risk_amount",       0.0)),
            reason             = str(data.get("reason",             "")),
        )


@dataclass
class ManagerJudgment:
    """ManagerAgent → BBS['manager_judgment']"""
    ticker:              str        = ""
    decision:            str        = "HOLD"
    score:               float      = 0.0
    threshold:           float      = 0.0
    signals:             dict       = field(default_factory=dict)
    signal_reasons:      dict       = field(default_factory=dict)
    macro_forced_hold:   bool       = False
    social_hype_penalty: bool       = False
    social_hype_score:   float      = 0.0
    rationale:           str        = ""
    order:               dict | None = None
    is_strong_buy:       bool       = False
    dry_run:             bool       = False

    def to_dict(self) -> dict:
        return {
            "ticker":              self.ticker,
            "decision":            self.decision,
            "score":               self.score,
            "threshold":           self.threshold,
            "signals":             self.signals,
            "signal_reasons":      self.signal_reasons,
            "macro_forced_hold":   self.macro_forced_hold,
            "social_hype_penalty": self.social_hype_penalty,
            "social_hype_score":   self.social_hype_score,
            "rationale":           self.rationale,
            "order":               self.order,
            "is_strong_buy":       self.is_strong_buy,
            "dry_run":             self.dry_run,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ManagerJudgment":
        return cls(
            ticker              = str(data.get("ticker",              "")),
            decision            = str(data.get("decision",            "HOLD")),
            score               = float(data.get("score",             0.0)),
            threshold           = float(data.get("threshold",         0.0)),
            signals             = dict(data.get("signals",            {})),
            signal_reasons      = dict(data.get("signal_reasons",     {})),
            macro_forced_hold   = bool(data.get("macro_forced_hold",  False)),
            social_hype_penalty = bool(data.get("social_hype_penalty",False)),
            social_hype_score   = float(data.get("social_hype_score", 0.0)),
            rationale           = str(data.get("rationale",           "")),
            order               = data.get("order"),
            is_strong_buy       = bool(data.get("is_strong_buy",      False)),
            dry_run             = bool(data.get("dry_run",            False)),
        )


@dataclass
class CriticJudgment:
    """CriticAgent → BBS['critic_judgment']"""
    critic_decision: str = "HOLD"
    revised_action:  str = "HOLD"
    critique_reason: str = ""
    llm_source:      str = "unknown"

    def to_dict(self) -> dict:
        return {
            "critic_decision": self.critic_decision,
            "revised_action":  self.revised_action,
            "critique_reason": self.critique_reason,
            "llm_source":      self.llm_source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CriticJudgment":
        return cls(
            critic_decision = str(data.get("critic_decision", "HOLD")),
            revised_action  = str(data.get("revised_action",  "HOLD")),
            critique_reason = str(data.get("critique_reason", "")),
            llm_source      = str(data.get("llm_source",      "unknown")),
        )


@dataclass
class ExitResult:
    """ExitAgent の個別ポジション評価結果"""
    ticker:        str   = ""
    action:        str   = "HOLD"
    exit_type:     str   = "CONTINUE"
    reason:        str   = ""
    entry_price:   float = 0.0
    current_price: float = 0.0
    pnl_pct:       float = 0.0
    shares:        int   = 0
    entry_date:    str   = ""
    buy_log_file:  str   = ""

    def to_dict(self) -> dict:
        return {
            "ticker":        self.ticker,
            "action":        self.action,
            "exit_type":     self.exit_type,
            "reason":        self.reason,
            "entry_price":   self.entry_price,
            "current_price": self.current_price,
            "pnl_pct":       self.pnl_pct,
            "shares":        self.shares,
            "entry_date":    self.entry_date,
            "buy_log_file":  self.buy_log_file,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExitResult":
        return cls(
            ticker        = str(data.get("ticker",        "")),
            action        = str(data.get("action",        "HOLD")),
            exit_type     = str(data.get("exit_type",     "CONTINUE")),
            reason        = str(data.get("reason",        "")),
            entry_price   = float(data.get("entry_price",   0.0)),
            current_price = float(data.get("current_price", 0.0)),
            pnl_pct       = float(data.get("pnl_pct",       0.0)),
            shares        = int(data.get("shares",        0)),
            entry_date    = str(data.get("entry_date",    "")),
            buy_log_file  = str(data.get("buy_log_file",  "")),
        )


@dataclass
class ExitDecisions:
    """ExitAgent → BBS['exit_decisions']"""
    date:    str             = ""
    results: list[ExitResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "date":    self.date,
            "results": [r.to_dict() for r in self.results],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExitDecisions":
        raw_results = data.get("results", [])
        results = [
            ExitResult.from_dict(r) if isinstance(r, dict) else r
            for r in (raw_results if isinstance(raw_results, list) else [])
        ]
        return cls(
            date    = str(data.get("date", "")),
            results = results,
        )


# ──────────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────────

def is_bbs_dataclass(obj: object) -> bool:
    """BBSスキーマのdataclassインスタンスか判定する。"""
    return hasattr(obj, "to_dict") and callable(getattr(obj, "to_dict"))
