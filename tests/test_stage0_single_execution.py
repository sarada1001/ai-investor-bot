"""tests/test_stage0_single_execution.py — Stage 0（Selling Loop）実行回数テスト

## なぜこのテストが必要か

Stage 0 は `run_trade_cycle()` の内部にあり、`run_watchlist_cycle()` が
銘柄ごとに `run_trade_cycle()` を呼ぶため、**分析銘柄数だけ ExitAgent の
判定が重複していた**（保有3銘柄 × 分析5銘柄 = 同一内容の thesis LLM 判定を
15回）。Gemini 無料枠 20 req/day の75%を重複判定が食い潰し、本番で
RESOURCE_EXHAUSTED (429) が多発してルールベースフォールバックに落ちていた。

保有ポジションの評価はポートフォリオ単位であり分析対象銘柄に依存しないため、
1サイクル1回で十分。ただし**減らしてよいのは重複だけ**で、
保有監視が実行されない経路を作ってはならない。このテストは両方を固定する。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


# ── ExitAgent 実装のスタブ（呼び出し回数を数える） ────────────────────

def _sample_decision() -> dict:
    return {
        "ticker":        "NOC",
        "action":        "HOLD",
        "exit_type":     "CONTINUE",
        "reason":        "利確/損切り条件未達・Thesis 継続 (+1.00%)",
        "entry_price":   100.0,
        "current_price": 101.0,
        "pnl_pct":       1.0,
        "shares":        1,
        "entry_date":    "2026-08-01",
        "buy_log_file":  "",
    }


class _CountingExitImpl:
    """agents.exit_agent.ExitAgent の差し替え。run() の呼び出しを記録する。"""

    calls: list[dict] = []

    def __init__(self, bbs) -> None:
        self.bbs = bbs

    def run(self, mock_mode: bool = False, alpaca_client=None) -> list[dict]:
        type(self).calls.append({"mock_mode": mock_mode, "alpaca_client": alpaca_client})
        results = [_sample_decision()]
        self.bbs.write("ExitAgent", "exit_decisions", {
            "date": "2026-08-13", "results": results,
        })
        return results


class _StubManagerAgent:
    """ManagerAgent の差し替え。

    本物は __init__ で LLM インスタンスを生成するため（mock_mode でも生成する）、
    Ollama / Gemini の可用性でテスト結果が揺れる。Stage 0 の実行回数には
    無関係なので HOLD 固定のスタブに置き換える。
    """

    def __init__(self, bbs) -> None:
        self.bbs = bbs

    def run(self, ticker: str = "AAPL", **kwargs) -> dict:
        judgment = {
            "ticker":        ticker,
            "decision":      "HOLD",
            "score":         0.0,
            "threshold":     0.60,
            "is_strong_buy": False,
            "signals":       {"news": 0.0, "technical": 0.0, "macro": 0.0,
                              "fundamental": 0.0, "social": 0.0, "liquidity": 0.0},
            "rationale":     "テストスタブ",
            "order":         None,
        }
        self.bbs.write("ManagerAgent", "manager_judgment", judgment)
        return judgment


@pytest.fixture(autouse=True)
def stub_manager_agent(monkeypatch):
    import engine.trade_cycle as _tc

    monkeypatch.setattr(_tc, "ManagerAgent", _StubManagerAgent)


@pytest.fixture
def counting_exit_agent(monkeypatch):
    """ExitAgent 実装を差し替え、呼び出し記録リストを返す。"""
    import engine.agent_wrappers as _wrappers

    _CountingExitImpl.calls = []
    monkeypatch.setattr(_wrappers, "_ExitAgentImpl", _CountingExitImpl)
    return _CountingExitImpl.calls


def _exit_decision_entries(tmp_path) -> list[dict]:
    """tmp 配下の全 BBS セッションファイルから exit_decisions エントリを集める。"""
    entries: list[dict] = []
    for path in sorted((tmp_path / "bbs").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        entries += [e for e in data["entries"] if e["key"] == "exit_decisions"]
    return entries


# ── 複数銘柄モード（--screen / --tickers） ────────────────────────────

class TestWatchlistCycle:
    def test_exit_agent_runs_once_for_multiple_tickers(self, counting_exit_agent):
        """3銘柄を分析しても ExitAgent の判定は1回だけ。"""
        from engine.runner import run_watchlist_cycle

        run_watchlist_cycle(
            tickers   = ["AAPL", "MSFT", "NVDA"],
            dry_run   = True,
            mock_mode = True,
        )

        assert len(counting_exit_agent) == 1, (
            f"Stage 0 が {len(counting_exit_agent)} 回実行された。"
            f" 銘柄数に比例して重複している可能性がある"
        )

    def test_every_ticker_session_records_exit_decisions(
        self, counting_exit_agent, isolate_state_files,
    ):
        """判定は1回でも、各銘柄の BBS には exit_decisions が1件ずつ残る。"""
        from engine.runner import run_watchlist_cycle

        run_watchlist_cycle(
            tickers   = ["AAPL", "MSFT", "NVDA"],
            dry_run   = True,
            mock_mode = True,
        )

        sessions = sorted((isolate_state_files / "bbs").glob("*.json"))
        assert sessions, "BBS セッションファイルが生成されていない"

        for path in sessions:
            data    = json.loads(path.read_text(encoding="utf-8"))
            entries = [e for e in data["entries"] if e["key"] == "exit_decisions"]
            assert len(entries) == 1, f"{path.name}: exit_decisions が {len(entries)} 件"
            assert entries[0]["agent"]           == "ExitAgent"
            assert entries[0]["data"]["results"] == [_sample_decision()]


# ── 単一銘柄モード（--ticker / デーモン単一銘柄） ──────────────────────

class TestSingleTickerCycle:
    def test_exit_agent_still_runs(self, counting_exit_agent, isolate_state_files):
        """単一銘柄モードでも Stage 0 は実行される（監視漏れを作らない）。"""
        from engine.trade_cycle import run_trade_cycle

        run_trade_cycle(ticker="AAPL", dry_run=True, mock_mode=True)

        assert len(counting_exit_agent) == 1
        assert len(_exit_decision_entries(isolate_state_files)) == 1

    def test_passed_results_are_not_re_evaluated(
        self, counting_exit_agent, isolate_state_files,
    ):
        """exit_results を渡した場合は再判定せず、BBS への転記だけ行う。"""
        from engine.trade_cycle import run_trade_cycle

        run_trade_cycle(
            ticker       = "AAPL",
            dry_run      = True,
            mock_mode    = True,
            exit_results = [_sample_decision()],
        )

        assert counting_exit_agent == []
        entries = _exit_decision_entries(isolate_state_files)
        assert len(entries) == 1
        assert entries[0]["data"]["results"] == [_sample_decision()]

    def test_empty_portfolio_result_is_not_treated_as_missing(
        self, counting_exit_agent,
    ):
        """保有ゼロの結果（空リスト）を渡しても Stage 0 を再実行しない。"""
        from engine.trade_cycle import run_trade_cycle

        run_trade_cycle(
            ticker       = "AAPL",
            dry_run      = True,
            mock_mode    = True,
            exit_results = [],
        )

        assert counting_exit_agent == []


# ── フォールバック & 発注抑制 ─────────────────────────────────────────

class TestExitStageFailureFallback:
    def test_falls_back_to_per_ticker_stage0(self, counting_exit_agent):
        """サイクル冒頭の Stage 0 が失敗したら、銘柄ごとの Stage 0 で監視を継続する。"""
        import engine.runner as _runner

        def _boom(*args, **kwargs):
            raise RuntimeError("Alpaca 一時障害")

        with patch.object(_runner, "run_exit_stage", side_effect=_boom):
            _runner.run_watchlist_cycle(
                tickers   = ["AAPL", "MSFT"],
                dry_run   = True,
                mock_mode = True,
            )

        assert len(counting_exit_agent) == 2, "監視が欠落している（銘柄ごとの再実行が働いていない）"


class TestResearchModeOrderSuppression:
    def test_research_mode_passes_no_alpaca_client(self, monkeypatch):
        """research_mode では Stage 0 に Alpaca クライアントを渡さない（実売り注文の禁止）。"""
        import engine.runner as _runner

        recorded: dict = {}

        def _fake_exit_stage(bbs=None, mock_mode=False, alpaca_client=None, notify_line=False):
            recorded.update(alpaca_client=alpaca_client, notify_line=notify_line)
            return []

        monkeypatch.setattr(_runner, "init_alpaca_and_sync", lambda mock_mode=False: object())
        monkeypatch.setattr(_runner, "run_exit_stage", _fake_exit_stage)
        monkeypatch.setattr(_runner, "run_trade_cycle", lambda **kwargs: {"decision": "HOLD"})

        _runner.run_watchlist_cycle(
            tickers       = ["AAPL"],
            dry_run       = False,
            notify_line   = True,
            research_mode = True,
        )

        assert recorded["alpaca_client"] is None
        assert recorded["notify_line"] is False

    def test_live_mode_passes_alpaca_client(self, monkeypatch):
        """通常モード（dry_run=False）では従来通り Alpaca クライアントを渡す。"""
        import engine.runner as _runner

        sentinel = object()
        recorded: dict = {}

        def _fake_exit_stage(bbs=None, mock_mode=False, alpaca_client=None, notify_line=False):
            recorded.update(alpaca_client=alpaca_client, notify_line=notify_line)
            return []

        monkeypatch.setattr(_runner, "init_alpaca_and_sync", lambda mock_mode=False: sentinel)
        monkeypatch.setattr(_runner, "run_exit_stage", _fake_exit_stage)
        monkeypatch.setattr(_runner, "run_trade_cycle", lambda **kwargs: {"decision": "HOLD"})

        _runner.run_watchlist_cycle(tickers=["AAPL"], dry_run=False, notify_line=True)

        assert recorded["alpaca_client"] is sentinel
        assert recorded["notify_line"] is True
