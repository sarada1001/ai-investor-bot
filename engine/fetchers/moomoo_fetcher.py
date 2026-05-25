"""engine/fetchers/moomoo_fetcher.py — Moomoo (Futu OpenAPI) データフェッチャー

本番APIは後日実装予定。現時点ではインターフェースとモック（ダミーデータ）を提供する。

取得データ:
  - OrderBook : Ask/Bid の気配値・数量（最良5本値）
  - CapitalFlow: 超大口・大口・中口・小口の資金流入出（当日累計 USD）

使用例:
    fetcher = MoomooFetcher(ticker="AAPL", use_mock=True)
    book = fetcher.get_order_book()    # OrderBook dataclass
    flow = fetcher.get_capital_flow()  # CapitalFlow dataclass

本番接続時（将来実装）:
    import futu
    fetcher = MoomooFetcher(ticker="AAPL", use_mock=False)
    # OpenD ゲートウェイ経由で実データを取得する実装に差し替える
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# ──────────────────────────────────────────────
# データ型定義
# ──────────────────────────────────────────────

@dataclass
class OrderLevel:
    """気配値の1本値（価格 + 数量）。"""
    price: float
    size:  int


@dataclass
class OrderBook:
    """
    Ask / Bid の最良 N 本値。

    asks: 売り板（価格が低い順、最良ASKが先頭）
    bids: 買い板（価格が高い順、最良BIDが先頭）
    """
    asks: list[OrderLevel] = field(default_factory=list)
    bids: list[OrderLevel] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "asks": [{"price": a.price, "size": a.size} for a in self.asks],
            "bids": [{"price": b.price, "size": b.size} for b in self.bids],
        }


@dataclass
class CapitalFlowTier:
    """資金フローの1ティア（inflow / outflow 単位: USD）。"""
    inflow:  float
    outflow: float

    @property
    def net(self) -> float:
        return self.inflow - self.outflow


@dataclass
class CapitalFlow:
    """
    当日の資金フロー集計（超大口〜小口の4ティア）。

    各ティアの一般的な定義:
      super_large : 注文1件 $500k 超（機関投資家・ヘッジファンド）
      large       : 注文1件 $100k〜$500k
      medium      : 注文1件 $10k〜$100k
      small       : 注文1件 $10k 未満（個人投資家）
    """
    super_large: CapitalFlowTier = field(default_factory=lambda: CapitalFlowTier(0.0, 0.0))
    large:       CapitalFlowTier = field(default_factory=lambda: CapitalFlowTier(0.0, 0.0))
    medium:      CapitalFlowTier = field(default_factory=lambda: CapitalFlowTier(0.0, 0.0))
    small:       CapitalFlowTier = field(default_factory=lambda: CapitalFlowTier(0.0, 0.0))

    def to_dict(self) -> dict:
        return {
            tier: {"inflow": getattr(self, tier).inflow, "outflow": getattr(self, tier).outflow}
            for tier in ("super_large", "large", "medium", "small")
        }


# ──────────────────────────────────────────────
# フェッチャーインターフェース (Protocol)
# ──────────────────────────────────────────────

@runtime_checkable
class LiquidityFetcher(Protocol):
    """板情報・資金フローを取得するフェッチャーのインターフェース（将来の差し替え用）。"""

    def get_order_book(self, levels: int = 5) -> OrderBook: ...
    def get_capital_flow(self) -> CapitalFlow: ...


# ──────────────────────────────────────────────
# モックデータセット（再現性のため定数）
# ──────────────────────────────────────────────

# 強気シナリオ: 大口買い越し + Bid側優勢
_MOCK_BULLISH: dict[str, dict] = {
    "order_book": {
        "asks": [
            {"price": 185.10, "size": 100},
            {"price": 185.20, "size": 200},
            {"price": 185.30, "size": 150},
            {"price": 185.40, "size":  80},
            {"price": 185.50, "size": 120},
        ],
        "bids": [
            {"price": 184.90, "size": 320},
            {"price": 184.80, "size": 280},
            {"price": 184.70, "size": 240},
            {"price": 184.60, "size": 200},
            {"price": 184.50, "size": 180},
        ],
    },
    "capital_flow": {
        "super_large": {"inflow": 5_200_000, "outflow": 1_800_000},
        "large":       {"inflow": 3_100_000, "outflow": 1_400_000},
        "medium":      {"inflow": 1_050_000, "outflow":   820_000},
        "small":       {"inflow":   480_000, "outflow":   550_000},
    },
}

# 弱気シナリオ: 大口売り越し + Ask側優勢
_MOCK_BEARISH: dict[str, dict] = {
    "order_book": {
        "asks": [
            {"price": 185.10, "size": 350},
            {"price": 185.20, "size": 290},
            {"price": 185.30, "size": 230},
            {"price": 185.40, "size": 180},
            {"price": 185.50, "size": 150},
        ],
        "bids": [
            {"price": 184.90, "size":  90},
            {"price": 184.80, "size": 110},
            {"price": 184.70, "size": 130},
            {"price": 184.60, "size":  80},
            {"price": 184.50, "size": 100},
        ],
    },
    "capital_flow": {
        "super_large": {"inflow": 1_200_000, "outflow": 4_800_000},
        "large":       {"inflow": 1_000_000, "outflow": 3_200_000},
        "medium":      {"inflow":   700_000, "outflow": 1_100_000},
        "small":       {"inflow":   600_000, "outflow":   400_000},
    },
}

# 中立シナリオ: 資金フロー拮抗 + 板ほぼ均等
_MOCK_NEUTRAL: dict[str, dict] = {
    "order_book": {
        "asks": [
            {"price": 185.10, "size": 190},
            {"price": 185.20, "size": 210},
            {"price": 185.30, "size": 175},
            {"price": 185.40, "size": 160},
            {"price": 185.50, "size": 140},
        ],
        "bids": [
            {"price": 184.90, "size": 210},
            {"price": 184.80, "size": 195},
            {"price": 184.70, "size": 180},
            {"price": 184.60, "size": 165},
            {"price": 184.50, "size": 145},
        ],
    },
    "capital_flow": {
        "super_large": {"inflow": 2_500_000, "outflow": 2_400_000},
        "large":       {"inflow": 1_800_000, "outflow": 1_750_000},
        "medium":      {"inflow":   900_000, "outflow":   850_000},
        "small":       {"inflow":   500_000, "outflow":   510_000},
    },
}

# ティッカー別モックシナリオ割り当て（テスト再現性のため固定）
_TICKER_SCENARIO: dict[str, dict] = {
    "AAPL": _MOCK_BULLISH, "MSFT": _MOCK_BULLISH, "NVDA": _MOCK_BULLISH,
    "GOOGL": _MOCK_BULLISH, "META": _MOCK_BULLISH, "AMZN": _MOCK_BULLISH,
    "TSLA": _MOCK_BEARISH, "INTC": _MOCK_BEARISH, "BA": _MOCK_BEARISH, "DIS": _MOCK_BEARISH,
}


def _get_mock_scenario(ticker: str) -> dict:
    """ティッカーに対応するモックシナリオを返す（未定義は中立）。"""
    return _TICKER_SCENARIO.get(ticker.upper(), _MOCK_NEUTRAL)


# ──────────────────────────────────────────────
# MoomooFetcher 実装
# ──────────────────────────────────────────────

class MoomooFetcher:
    """
    Moomoo (Futu OpenAPI) データフェッチャー。

    Parameters
    ----------
    ticker   : 取得対象銘柄シンボル
    use_mock : True = モックデータを返す（デフォルト）
               False = 将来の本番API実装用（現在は未実装、NotImplementedError を発生）
    """

    def __init__(self, ticker: str, use_mock: bool | None = None) -> None:
        self.ticker   = ticker.upper()
        # 環境変数 MOOMOO_USE_MOCK=false で将来の本番モードに切り替え可能
        _env_mock     = os.getenv("MOOMOO_USE_MOCK", "true").lower()
        self.use_mock = use_mock if use_mock is not None else (_env_mock != "false")

    # ── Public API ────────────────────────────────────────

    def get_order_book(self, levels: int = 5) -> OrderBook:
        """
        Ask / Bid の最良 levels 本値を取得する。

        Returns
        -------
        OrderBook
            asks: 売り板（価格が低い順）
            bids: 買い板（価格が高い順）
        """
        if self.use_mock:
            return self._mock_order_book(levels)
        return self._live_order_book(levels)

    def get_capital_flow(self) -> CapitalFlow:
        """
        当日の資金フロー集計（超大口〜小口）を取得する。

        Returns
        -------
        CapitalFlow
            4ティア（super_large / large / medium / small）の inflow・outflow（USD）
        """
        if self.use_mock:
            return self._mock_capital_flow()
        return self._live_capital_flow()

    # ── Mock implementations ─────────────────────────────

    def _mock_order_book(self, levels: int) -> OrderBook:
        scenario = _get_mock_scenario(self.ticker)
        raw_asks = scenario["order_book"]["asks"][:levels]
        raw_bids = scenario["order_book"]["bids"][:levels]
        return OrderBook(
            asks=[OrderLevel(price=a["price"], size=a["size"]) for a in raw_asks],
            bids=[OrderLevel(price=b["price"], size=b["size"]) for b in raw_bids],
        )

    def _mock_capital_flow(self) -> CapitalFlow:
        scenario = _get_mock_scenario(self.ticker)
        cf       = scenario["capital_flow"]
        return CapitalFlow(
            super_large = CapitalFlowTier(**cf["super_large"]),
            large       = CapitalFlowTier(**cf["large"]),
            medium      = CapitalFlowTier(**cf["medium"]),
            small       = CapitalFlowTier(**cf["small"]),
        )

    # ── Live implementations (将来の本番実装) ────────────

    def _live_order_book(self, levels: int) -> OrderBook:  # noqa: ARG002
        raise NotImplementedError(
            "Moomoo本番API未実装。use_mock=True を使用するか、"
            "futu-api をインストールして OpenD ゲートウェイ経由で実装してください。"
        )

    def _live_capital_flow(self) -> CapitalFlow:
        raise NotImplementedError(
            "Moomoo本番API未実装。use_mock=True を使用するか、"
            "futu-api をインストールして OpenD ゲートウェイ経由で実装してください。"
        )
