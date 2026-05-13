"""tests/test_agent_exam.py — evaluate_agent() の新評価ロジックのテスト"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.run_agent_exam import (
    AgentExamResult,
    VirtualTrade,
    evaluate_agent,
    VIX_CALM_THRESHOLD,
    PASS_MIN_TRADES,
    PASS_MIN_WIN_RATE,
)


def _make_trade(win: bool, macro_ok: bool) -> VirtualTrade:
    return VirtualTrade(
        ticker="AAPL",
        entry_date="2026-01-01",
        exit_date="2026-01-10",
        entry_price=150.0,
        exit_price=160.0 if win else 140.0,
        atr=2.0,
        sl_price=146.0,
        tp_price=158.0,
        exit_reason="TAKE_PROFIT" if win else "STOP_LOSS",
        pnl_pct=6.67 if win else -6.67,
        win=win,
        macro_ok=macro_ok,
    )


class TestVixThreshold:
    def test_vix_threshold_is_25(self):
        assert VIX_CALM_THRESHOLD == 25.0, f"VIX閾値が25でない: {VIX_CALM_THRESHOLD}"


class TestEvaluateAgentAllTrades:
    """MacroAgent が全トレードで評価されることを確認する。"""

    def test_macro_agent_uses_all_trades(self):
        """macro_ok=False のトレードも含めて評価される。"""
        # 全28トレード: macro_ok=True が5件のみ（旧ロジックでは評価不足になる）
        trades = [_make_trade(win=True,  macro_ok=True)  for _ in range(5)]
        trades += [_make_trade(win=True,  macro_ok=False) for _ in range(11)]
        trades += [_make_trade(win=False, macro_ok=False) for _ in range(12)]

        result = evaluate_agent("MacroAgent", trades)

        assert result.total_trades == 28, "全28件で評価すべき"
        assert result.status == "ACTIVE"  # 16/28 = 57% >= 50%

    def test_macro_agent_suspended_with_old_logic_but_active_with_new(self):
        """旧ロジックでは SUSPENDED になるが新ロジックでは ACTIVE になるケース。"""
        # macro_ok=True: 19件 → 旧ロジックだと取引数不足でSUSPENDED
        # 全体: 25件（勝率56%）→ 新ロジックでACTIVE
        trades = [_make_trade(win=True,  macro_ok=True)  for _ in range(10)]
        trades += [_make_trade(win=False, macro_ok=True)  for _ in range(9)]
        trades += [_make_trade(win=True,  macro_ok=False) for _ in range(4)]
        trades += [_make_trade(win=False, macro_ok=False) for _ in range(2)]

        result = evaluate_agent("MacroAgent", trades)

        assert result.total_trades == 25
        assert result.status == "ACTIVE"

    def test_other_agents_not_affected(self):
        """他エージェントは従来通り全トレードで評価され macro_ok_rate=0。"""
        trades = [_make_trade(win=True, macro_ok=False) for _ in range(15)]
        trades += [_make_trade(win=False, macro_ok=True) for _ in range(10)]

        for agent_name in ["TechnicalAgent", "NewsAgent", "SocialAgent", "FundamentalAgent"]:
            result = evaluate_agent(agent_name, trades)
            assert result.total_trades == 25
            assert result.macro_ok_rate is None, f"{agent_name} に macro_ok_rate が設定されてはいけない"


class TestMacroOkRateReference:
    """macro_ok_rate が参考指標として正しく計算されることを確認する。"""

    def test_macro_ok_rate_computed_for_macro_agent(self):
        trades = [_make_trade(win=True,  macro_ok=True)  for _ in range(3)]
        trades += [_make_trade(win=False, macro_ok=True)  for _ in range(7)]
        trades += [_make_trade(win=True,  macro_ok=False) for _ in range(10)]
        trades += [_make_trade(win=True,  macro_ok=False) for _ in range(10)]
        # macro_ok: 10件中3勝 = 30%  /  全体: 30件中23勝 = 76.7%

        result = evaluate_agent("MacroAgent", trades)

        assert result.macro_ok_rate == pytest.approx(0.3, abs=0.01)
        assert result.win_rate > 0.5  # 全体勝率は合格

    def test_macro_ok_rate_none_when_no_macro_ok_trades(self):
        """macro_ok=True のトレードがない場合 macro_ok_rate=None になる（未定義）。"""
        trades = [_make_trade(win=True, macro_ok=False) for _ in range(25)]
        result = evaluate_agent("MacroAgent", trades)
        assert result.macro_ok_rate is None

    def test_macro_ok_rate_zero_when_all_macro_ok_trades_lost(self):
        """macro_ok=True の全トレードが負けた場合 macro_ok_rate=0.0（定義済みの値）。"""
        trades = [_make_trade(win=False, macro_ok=True)  for _ in range(5)]
        trades += [_make_trade(win=True,  macro_ok=False) for _ in range(20)]
        result = evaluate_agent("MacroAgent", trades)
        assert result.macro_ok_rate == 0.0


class TestSuspendedWhenInsufficientTrades:
    def test_suspended_when_too_few_total_trades(self):
        """全トレードが20件未満でも SUSPENDED になる。"""
        trades = [_make_trade(win=True, macro_ok=True) for _ in range(19)]
        result = evaluate_agent("MacroAgent", trades)
        assert result.status == "SUSPENDED"
        assert result.total_trades == 19


