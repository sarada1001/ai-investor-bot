# テストスイート内訳レポート

生成日時（UTC）: 2026-07-02T11:00:25+00:00

**このファイルは `scripts/gen_test_report.py` による自動生成です。手動編集しないこと。**

## サマリー

- 総テスト数: 334
- PASSED: 332
- FAILED/ERROR: 2
- SKIPPED: 0
- 成功率: 99.4%
- 全件成功（all_passing）: `False`

## ファイル別内訳

| ファイル | テスト数 | PASSED | FAILED/ERROR | SKIPPED |
|---|---|---|---|---|
| `tests/test_agent_exam.py` | 8 | 8 | 0 | 0 |
| `tests/test_alpaca_client_limit.py` | 13 | 13 | 0 | 0 |
| `tests/test_bbs_schema.py` | 36 | 36 | 0 | 0 |
| `tests/test_circuit_breaker.py` | 7 | 7 | 0 | 0 |
| `tests/test_critic_agent_fallback.py` | 12 | 12 | 0 | 0 |
| `tests/test_dip_detector.py` | 19 | 19 | 0 | 0 |
| `tests/test_edgar_staleness.py` | 13 | 13 | 0 | 0 |
| `tests/test_exit_agent_fallback.py` | 15 | 15 | 0 | 0 |
| `tests/test_gate_check.py` | 6 | 6 | 0 | 0 |
| `tests/test_intervention.py` | 22 | 22 | 0 | 0 |
| `tests/test_liquidity_agent.py` | 37 | 37 | 0 | 0 |
| `tests/test_live_trading_gate.py` | 5 | 5 | 0 | 0 |
| `tests/test_monitor.py` | 19 | 19 | 0 | 0 |
| `tests/test_notify.py` | 17 | 17 | 0 | 0 |
| `tests/test_research_mode.py` | 10 | 10 | 0 | 0 |
| `tests/test_screener_pure.py` | 33 | 31 | 2 | 0 |
| `tests/test_signal_scorer.py` | 46 | 46 | 0 | 0 |
| `tests/test_trade_guard.py` | 6 | 6 | 0 | 0 |
| `tests/test_training_data_collector.py` | 10 | 10 | 0 | 0 |

## 現在の失敗テスト

- `tests/test_screener_pure.py::TestCalcRsi::test_all_up_returns_high_rsi — FAILED`
- `tests/test_screener_pure.py::TestCalcRsi::test_too_short_series_returns_50 — FAILED`
