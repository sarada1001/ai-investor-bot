"""tests/test_liquidity_agent.py — LiquidityAgent ユニットテスト

テスト対象:
  - MoomooFetcher: モックデータ取得
  - skills.liquidity_monitor: VTA計算ロジック（_calc_book_ratios, _calc_score, _generate_verbal_annotation）
  - engine.agent_wrappers.LiquidityAgent: BBS書き込みを含むエンドツーエンド
"""

from __future__ import annotations

import pytest

from engine.fetchers.moomoo_fetcher import (
    MoomooFetcher,
    OrderBook,
    OrderLevel,
    CapitalFlow,
    CapitalFlowTier,
)
from skills.liquidity_monitor import (
    _calc_book_ratios,
    _calc_score,
    _generate_verbal_annotation,
    analyze_liquidity,
)


# ──────────────────────────────────────────────
# フィクスチャ
# ──────────────────────────────────────────────

@pytest.fixture
def bullish_book() -> OrderBook:
    """Bid優勢（65%）の強気板。"""
    return OrderBook(
        asks=[OrderLevel(price=185.1 + i * 0.1, size=100 + i * 20) for i in range(5)],
        bids=[OrderLevel(price=184.9 - i * 0.1, size=300 - i * 20) for i in range(5)],
    )


@pytest.fixture
def bearish_book() -> OrderBook:
    """Ask優勢（63%）の弱気板。"""
    return OrderBook(
        asks=[OrderLevel(price=185.1 + i * 0.1, size=300 - i * 20) for i in range(5)],
        bids=[OrderLevel(price=184.9 - i * 0.1, size=100 + i * 10) for i in range(5)],
    )


@pytest.fixture
def neutral_book() -> OrderBook:
    """均等（50/50）の中立板。"""
    return OrderBook(
        asks=[OrderLevel(price=185.1 + i * 0.1, size=200) for i in range(5)],
        bids=[OrderLevel(price=184.9 - i * 0.1, size=200) for i in range(5)],
    )


@pytest.fixture
def bullish_flow() -> CapitalFlow:
    """大口+$5M買い越し。"""
    return CapitalFlow(
        super_large = CapitalFlowTier(inflow=3_000_000, outflow=1_000_000),
        large       = CapitalFlowTier(inflow=2_000_000, outflow=  800_000),
        medium      = CapitalFlowTier(inflow=  800_000, outflow=  700_000),
        small       = CapitalFlowTier(inflow=  400_000, outflow=  500_000),
    )


@pytest.fixture
def bearish_flow() -> CapitalFlow:
    """大口-$5M売り越し。"""
    return CapitalFlow(
        super_large = CapitalFlowTier(inflow=1_000_000, outflow=3_500_000),
        large       = CapitalFlowTier(inflow=  800_000, outflow=2_300_000),
        medium      = CapitalFlowTier(inflow=  700_000, outflow=  900_000),
        small       = CapitalFlowTier(inflow=  600_000, outflow=  400_000),
    )


# ──────────────────────────────────────────────
# MoomooFetcher テスト
# ──────────────────────────────────────────────

class TestMoomooFetcher:
    def test_mock_order_book_returns_correct_levels(self):
        fetcher = MoomooFetcher(ticker="AAPL", use_mock=True)
        book = fetcher.get_order_book(levels=5)
        assert len(book.asks) == 5
        assert len(book.bids) == 5

    def test_mock_order_book_asks_sorted_ascending(self):
        """Ask板は価格が低い順（最良Ask先頭）。"""
        fetcher = MoomooFetcher(ticker="AAPL", use_mock=True)
        book = fetcher.get_order_book(levels=5)
        prices = [a.price for a in book.asks]
        assert prices == sorted(prices), "Ask板は昇順であること"

    def test_mock_order_book_bids_sorted_descending(self):
        """Bid板は価格が高い順（最良Bid先頭）。"""
        fetcher = MoomooFetcher(ticker="AAPL", use_mock=True)
        book = fetcher.get_order_book(levels=5)
        prices = [b.price for b in book.bids]
        assert prices == sorted(prices, reverse=True), "Bid板は降順であること"

    def test_mock_capital_flow_has_four_tiers(self):
        fetcher = MoomooFetcher(ticker="AAPL", use_mock=True)
        flow = fetcher.get_capital_flow()
        assert flow.super_large is not None
        assert flow.large is not None
        assert flow.medium is not None
        assert flow.small is not None

    def test_mock_bullish_scenario_positive_net(self):
        """AAPLは強気シナリオ → 大口純流入が正。"""
        fetcher = MoomooFetcher(ticker="AAPL", use_mock=True)
        flow = fetcher.get_capital_flow()
        net_large = flow.super_large.net + flow.large.net
        assert net_large > 0, "AAPL強気シナリオは大口買い越しであること"

    def test_mock_bearish_scenario_negative_net(self):
        """INTCは弱気シナリオ → 大口純流入が負。"""
        fetcher = MoomooFetcher(ticker="INTC", use_mock=True)
        flow = fetcher.get_capital_flow()
        net_large = flow.super_large.net + flow.large.net
        assert net_large < 0, "INTC弱気シナリオは大口売り越しであること"

    def test_mock_neutral_scenario_near_zero(self):
        """未定義ティッカーは中立シナリオ → 純流入がほぼゼロ。"""
        fetcher = MoomooFetcher(ticker="XYZ_UNKNOWN", use_mock=True)
        flow = fetcher.get_capital_flow()
        net_large = flow.super_large.net + flow.large.net
        assert abs(net_large) < 500_000, "中立シナリオは大口フローがほぼ均衡であること"

    def test_live_mode_raises_not_implemented(self):
        fetcher = MoomooFetcher(ticker="AAPL", use_mock=False)
        with pytest.raises(NotImplementedError):
            fetcher.get_order_book()

    def test_to_dict_structure(self):
        fetcher = MoomooFetcher(ticker="AAPL", use_mock=True)
        book = fetcher.get_order_book(levels=3)
        d = book.to_dict()
        assert "asks" in d and "bids" in d
        assert all("price" in a and "size" in a for a in d["asks"])


# ──────────────────────────────────────────────
# _calc_book_ratios テスト
# ──────────────────────────────────────────────

class TestCalcBookRatios:
    def test_empty_book_returns_neutral(self):
        book = OrderBook(asks=[], bids=[])
        ask_r, bid_r = _calc_book_ratios(book)
        assert ask_r == 0.5
        assert bid_r == 0.5

    def test_ratios_sum_to_one(self, bullish_book):
        ask_r, bid_r = _calc_book_ratios(bullish_book)
        assert abs(ask_r + bid_r - 1.0) < 1e-6

    def test_bid_dominant_in_bullish_book(self, bullish_book):
        ask_r, bid_r = _calc_book_ratios(bullish_book)
        assert bid_r > ask_r, "強気板ではBid比率がAsk比率を上回ること"
        assert bid_r > 0.55, "強気板ではBid比率が55%超であること"

    def test_ask_dominant_in_bearish_book(self, bearish_book):
        ask_r, bid_r = _calc_book_ratios(bearish_book)
        assert ask_r > bid_r, "弱気板ではAsk比率がBid比率を上回ること"
        assert ask_r > 0.55, "弱気板ではAsk比率が55%超であること"

    def test_neutral_book_near_half(self, neutral_book):
        ask_r, bid_r = _calc_book_ratios(neutral_book)
        assert abs(ask_r - 0.5) < 0.01
        assert abs(bid_r - 0.5) < 0.01


# ──────────────────────────────────────────────
# _calc_score テスト
# ──────────────────────────────────────────────

class TestCalcScore:
    def test_bullish_book_and_flow_gives_positive_score(self):
        score = _calc_score(bid_ratio=0.65, net_large_inflow=5_000_000)
        assert score > 0.0

    def test_bearish_book_and_flow_gives_negative_score(self):
        score = _calc_score(bid_ratio=0.35, net_large_inflow=-5_000_000)
        assert score < 0.0

    def test_neutral_inputs_give_near_zero(self):
        score = _calc_score(bid_ratio=0.5, net_large_inflow=0.0)
        assert abs(score) < 0.01

    def test_score_clamped_to_minus_one_plus_one(self):
        """極端な値を入力してもスコアが[-1, +1]に収まること。"""
        score_max = _calc_score(bid_ratio=1.0, net_large_inflow=100_000_000)
        score_min = _calc_score(bid_ratio=0.0, net_large_inflow=-100_000_000)
        assert -1.0 <= score_max <= 1.0
        assert -1.0 <= score_min <= 1.0

    def test_bid_dominant_alone_gives_moderate_positive(self):
        """板Bid優勢だが資金フロー中立 → 穏やかなプラス。"""
        score = _calc_score(bid_ratio=0.7, net_large_inflow=0.0)
        assert 0.0 < score <= 0.5

    def test_flow_at_scale_without_book_bias_is_half_max(self):
        """flow_score=+1.0, book_score=0.0 → combined = 0.5。"""
        score = _calc_score(bid_ratio=0.5, net_large_inflow=5_000_000)
        assert abs(score - 0.5) < 0.01


# ──────────────────────────────────────────────
# _generate_verbal_annotation テスト
# ──────────────────────────────────────────────

class TestGenerateVerbalAnnotation:
    def test_returns_non_empty_string(self):
        result = _generate_verbal_annotation(
            ticker="AAPL", ask_ratio=0.35, bid_ratio=0.65,
            net_large_inflow=5_000_000, net_small_inflow=-70_000, score=0.65,
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_bullish_annotation_contains_buy_keywords(self):
        """強気シナリオのアノテーションに買い越し・Bid優勢の記述があること。"""
        result = _generate_verbal_annotation(
            ticker="AAPL", ask_ratio=0.35, bid_ratio=0.65,
            net_large_inflow=5_500_000, net_small_inflow=-70_000, score=0.65,
        )
        assert "買い越し" in result
        assert "Bid側" in result

    def test_bearish_annotation_contains_sell_keywords(self):
        """弱気シナリオのアノテーションに売り越し・Ask優勢の記述があること。"""
        result = _generate_verbal_annotation(
            ticker="INTC", ask_ratio=0.62, bid_ratio=0.38,
            net_large_inflow=-4_800_000, net_small_inflow=200_000, score=-0.62,
        )
        assert "売り越し" in result
        assert "Ask側" in result

    def test_neutral_annotation_contains_neutral_keyword(self):
        """中立シナリオのアノテーションに中立・均等の記述があること。"""
        result = _generate_verbal_annotation(
            ticker="SPY", ask_ratio=0.51, bid_ratio=0.49,
            net_large_inflow=50_000, net_small_inflow=-10_000, score=0.02,
        )
        assert "均等" in result or "中立" in result

    def test_small_flow_not_mentioned_below_threshold(self):
        """小口フローが閾値($200k)以下の場合は言及しないこと。"""
        result = _generate_verbal_annotation(
            ticker="AAPL", ask_ratio=0.4, bid_ratio=0.6,
            net_large_inflow=3_000_000, net_small_inflow=100_000, score=0.5,
        )
        # net_small_inflow=100_000 < 200_000 → 小口言及なし
        assert "小口" not in result

    def test_strong_buy_conclusion_at_high_score(self):
        result = _generate_verbal_annotation(
            ticker="NVDA", ask_ratio=0.3, bid_ratio=0.7,
            net_large_inflow=7_000_000, net_small_inflow=300_000, score=0.7,
        )
        assert "強い買い圧力" in result

    def test_strong_sell_conclusion_at_low_score(self):
        result = _generate_verbal_annotation(
            ticker="BA", ask_ratio=0.7, bid_ratio=0.3,
            net_large_inflow=-7_000_000, net_small_inflow=-300_000, score=-0.7,
        )
        assert "強い売り圧力" in result


# ──────────────────────────────────────────────
# analyze_liquidity テスト（統合）
# ──────────────────────────────────────────────

class TestAnalyzeLiquidity:
    def test_returns_required_keys(self):
        """必須キーがすべて返されること。"""
        result = analyze_liquidity("AAPL", use_mock=True)
        required_keys = {
            "ticker", "verbal_annotation", "ask_ratio", "bid_ratio",
            "net_large_inflow", "net_small_inflow", "pressure", "score", "data_source",
        }
        assert required_keys.issubset(result.keys())

    def test_ticker_preserved(self):
        result = analyze_liquidity("MSFT", use_mock=True)
        assert result["ticker"] == "MSFT"

    def test_data_source_is_mock(self):
        result = analyze_liquidity("AAPL", use_mock=True)
        assert result["data_source"] == "mock"

    def test_score_in_valid_range(self):
        for ticker in ["AAPL", "INTC", "XYZ_NEUTRAL"]:
            result = analyze_liquidity(ticker, use_mock=True)
            assert -1.0 <= result["score"] <= 1.0, f"{ticker}: scoreが範囲外"

    def test_ratios_sum_to_one(self):
        result = analyze_liquidity("AAPL", use_mock=True)
        total = result["ask_ratio"] + result["bid_ratio"]
        assert abs(total - 1.0) < 1e-4

    def test_bullish_ticker_has_buy_dominant_pressure(self):
        """AAPLは強気シナリオ → pressure = 'buy_dominant'。"""
        result = analyze_liquidity("AAPL", use_mock=True)
        assert result["pressure"] == "buy_dominant"

    def test_bearish_ticker_has_sell_dominant_pressure(self):
        """INTCは弱気シナリオ → pressure = 'sell_dominant'。"""
        result = analyze_liquidity("INTC", use_mock=True)
        assert result["pressure"] == "sell_dominant"

    def test_verbal_annotation_is_non_empty_string(self):
        result = analyze_liquidity("AAPL", use_mock=True)
        assert isinstance(result["verbal_annotation"], str)
        assert len(result["verbal_annotation"]) > 20

    def test_verbal_annotation_written_to_bbs(self):
        """LiquidityAgent が BBSに正しく書き込むこと。"""
        from engine.bbs import BBS
        from engine.agent_wrappers import LiquidityAgent

        bbs = BBS(session_id="test_liquidity_001")
        agent = LiquidityAgent(bbs)
        result = agent.run(ticker="AAPL", phase_tag="TEST")

        bbs_entry = bbs.read("liquidity_analysis")
        assert bbs_entry is not None, "BBSに liquidity_analysis が書き込まれていること"
        assert "verbal_annotation" in bbs_entry
        assert "score" in bbs_entry
        assert bbs_entry["ticker"] == "AAPL"

    def test_bbs_entry_matches_agent_return(self):
        """BBSのエントリがagentの返り値と一致すること。"""
        from engine.bbs import BBS
        from engine.agent_wrappers import LiquidityAgent

        bbs = BBS(session_id="test_liquidity_002")
        agent = LiquidityAgent(bbs)
        result = agent.run(ticker="MSFT", phase_tag="TEST")

        bbs_entry = bbs.read("liquidity_analysis")
        assert bbs_entry["score"]             == result["score"]
        assert bbs_entry["verbal_annotation"] == result["verbal_annotation"]
        assert bbs_entry["pressure"]          == result["pressure"]
