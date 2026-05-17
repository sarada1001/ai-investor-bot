"""
scripts/live_reset.py

本番切り替え時に一度だけ実行するデータリセットスクリプト。
ペーパートレードの残留データ（portfolio、circuit_breaker、trade_guard）を
クリーンな初期状態に戻す。

実行タイミング: Alpaca Live口座に入金確認後、.env をライブキーに切り替えた後。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PORTFOLIO_PATH       = BASE / "data" / "portfolio.json"
CIRCUIT_BREAKER_PATH = BASE / "data" / "circuit_breaker_state.json"
TRADE_GUARD_PATH     = BASE / "data" / "trade_guard_state.json"


def _confirm(msg: str) -> bool:
    ans = input(f"{msg} [yes/no]: ").strip().lower()
    return ans == "yes"


def reset_portfolio() -> None:
    existing = json.loads(PORTFOLIO_PATH.read_text()) if PORTFOLIO_PATH.exists() else {}
    positions = existing.get("positions", [])
    if positions:
        print(f"  現在のポジション: {[p['ticker'] for p in positions]}")
    data = {
        "schema_version": "1.0",
        "updated_at": datetime.now().isoformat(),
        "_doc": existing.get("_doc", {}),
        "positions": [],
    }
    PORTFOLIO_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"  ✅ portfolio.json をリセットしました（{len(positions)} ポジション削除）")


def reset_circuit_breaker() -> None:
    data = {
        "status": "OPEN",
        "daily_baseline_equity": None,
        "high_water_mark": None,
        "last_reset_date": None,
        "last_updated": datetime.now().isoformat(),
        "trip_reason": None,
        "trip_time": None,
        "daily_pnl_pct": 0.0,
        "total_drawdown_pct": 0.0,
    }
    CIRCUIT_BREAKER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print("  ✅ circuit_breaker_state.json をリセットしました（ベースラインは次回起動時にAlpacaから自動取得）")


def reset_trade_guard() -> None:
    data = {
        "daily_buy_count": 0,
        "last_reset_date": None,
        "buy_log": [],
    }
    TRADE_GUARD_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print("  ✅ trade_guard_state.json をリセットしました")


def main() -> None:
    print("=" * 60)
    print("  本番切り替え用データリセット")
    print("=" * 60)
    print()
    print("⚠️  このスクリプトは以下を削除します:")
    print("   - portfolio.json のすべてのポジション（ペーパー取引分）")
    print("   - circuit_breaker の $101,580 ベースライン")
    print("   - trade_guard の当日発注カウント")
    print()
    print("⚠️  実行前に確認してください:")
    print("   1. Alpaca Live口座への入金が確認済みであること")
    print("   2. .env の API キーがライブキーに変更済みであること")
    print("   3. ALPACA_PAPER_TRADING=False になっていること")
    print()

    if not _confirm("本当にリセットしますか？"):
        print("キャンセルしました。")
        sys.exit(0)

    print()
    print("リセット中...")
    reset_portfolio()
    reset_circuit_breaker()
    reset_trade_guard()
    print()
    print("完了。次のステップ:")
    print("  1. python scripts/test_live_connection.py --verbose")
    print("  2. python scripts/preflight_check.py --verbose")
    print("  3. python main.py --enable-live")
    print("  4. python main.py --screen --dry-run  （最終確認）")


if __name__ == "__main__":
    main()
