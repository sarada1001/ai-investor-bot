#!/usr/bin/env python3
"""
scripts/test_live_connection.py — Alpaca ライブ API 疎通確認スクリプト

【安全保証】このスクリプトは一切の注文・取引を行いません。
  - 呼び出しは read-only エンドポイントのみ（get_account / get_all_positions）
  - TradingClient の paper=False モードで接続確認のみ実施
  - 注文送信コード（submit_order）は含まれていません

環境変数（優先順）:
  1. APCA_API_KEY_ID_LIVE    + APCA_API_SECRET_KEY_LIVE  （ライブ専用キー推奨）
  2. APCA_API_KEY_ID         + APCA_API_SECRET_KEY        （フォールバック）

Usage:
    python scripts/test_live_connection.py
    python scripts/test_live_connection.py --verbose
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPT_DIR   = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

_W = 64  # 表示幅


def _resolve_keys() -> tuple[str, str, str]:
    """
    ライブ用 API キーを環境変数から解決する。

    Returns:
        (api_key, secret_key, source_label)
    """
    live_key    = os.getenv("APCA_API_KEY_ID_LIVE", "")
    live_secret = os.getenv("APCA_API_SECRET_KEY_LIVE", "")

    if live_key and live_secret:
        return live_key, live_secret, "APCA_API_KEY_ID_LIVE / APCA_API_SECRET_KEY_LIVE"

    fallback_key    = os.getenv("APCA_API_KEY_ID", "")
    fallback_secret = os.getenv("APCA_API_SECRET_KEY", "")

    if fallback_key and fallback_secret:
        return fallback_key, fallback_secret, "APCA_API_KEY_ID / APCA_API_SECRET_KEY (フォールバック)"

    return "", "", ""


def _mask(value: str) -> str:
    """APIキーを先頭4文字+マスクで表示する。"""
    if not value:
        return "(未設定)"
    return value[:4] + "*" * (len(value) - 4)


def run_connectivity_check(verbose: bool = False) -> bool:
    """
    Alpaca ライブ API に接続し、アカウント情報とポジションを表示する。

    Returns:
        True: 接続成功 / False: 接続失敗
    """
    print()
    print("=" * _W)
    print("  Alpaca ライブ API 疎通確認スクリプト")
    print("  【注文は一切行いません — Read-only】")
    print("=" * _W)

    # ── キー解決 ──────────────────────────────────────────────
    api_key, secret_key, key_source = _resolve_keys()

    if not api_key or not secret_key:
        print()
        print("  [ERROR] API キーが見つかりません。")
        print("  以下のいずれかを .env に設定してください:")
        print("    APCA_API_KEY_ID_LIVE    + APCA_API_SECRET_KEY_LIVE")
        print("    APCA_API_KEY_ID         + APCA_API_SECRET_KEY")
        print()
        return False

    print(f"\n  キーソース  : {key_source}")
    print(f"  API Key     : {_mask(api_key)}")
    print(f"  Secret Key  : {_mask(secret_key)}")
    print(f"  Mode        : LIVE (paper=False)")

    # ── Alpaca クライアント初期化（live） ─────────────────────
    try:
        from alpaca.trading.client import TradingClient
    except ImportError:
        print("\n  [ERROR] alpaca-py がインストールされていません。")
        print("  pip install alpaca-py を実行してください。")
        return False

    print("\n  接続中...")
    try:
        client = TradingClient(api_key, secret_key, paper=False)
    except Exception as exc:
        print(f"  [ERROR] TradingClient 初期化失敗: {exc}")
        return False

    # ── アカウント情報取得（read-only） ───────────────────────
    try:
        account = client.get_account()
    except Exception as exc:
        print(f"\n  [FAIL] アカウント取得失敗: {exc}")
        print()
        print("  考えられる原因:")
        print("    - API キーがペーパー用（ライブキーと混在）")
        print("    - キーの権限が不足している")
        print("    - Alpaca アカウントが未アクティブ")
        print()
        return False

    # ── ポジション取得（read-only） ───────────────────────────
    positions: list = []
    try:
        positions = client.get_all_positions()
    except Exception as exc:
        print(f"  [WARN] ポジション取得失敗: {exc}")

    # ── 結果表示 ──────────────────────────────────────────────
    print()
    print("─" * _W)
    print("  ◆ アカウント情報")
    print("─" * _W)
    print(f"  ステータス      : {account.status}")
    print(f"  現金            : ${float(account.cash):,.2f}")
    print(f"  買付余力        : ${float(account.buying_power):,.2f}")
    print(f"  ポートフォリオ  : ${float(account.equity):,.2f}")
    print(f"  通貨            : {account.currency}")
    print(f"  PDT フラグ      : {getattr(account, 'pattern_day_trader', 'N/A')}")

    if verbose:
        print()
        print("─" * _W)
        print("  ◆ アカウント詳細")
        print("─" * _W)
        print(f"  ID              : {account.id}")
        print(f"  取引停止        : {getattr(account, 'trading_blocked', 'N/A')}")
        print(f"  出金停止        : {getattr(account, 'transfers_blocked', 'N/A')}")
        print(f"  アカウント停止  : {getattr(account, 'account_blocked', 'N/A')}")

    print()
    print("─" * _W)
    print(f"  ◆ 保有ポジション ({len(positions)} 件)")
    print("─" * _W)

    if not positions:
        print("  (保有なし)")
    else:
        print(f"  {'ティッカー':<10} {'数量':>8} {'評価額':>14} {'未実現損益':>14}")
        print(f"  {'─' * 50}")
        for pos in positions:
            qty      = float(getattr(pos, 'qty', 0))
            mkt_val  = float(getattr(pos, 'market_value', 0))
            unreal   = float(getattr(pos, 'unrealized_pl', 0))
            sym      = str(getattr(pos, 'symbol', '?'))
            print(f"  {sym:<10} {qty:>8.2f} ${mkt_val:>12,.2f} ${unreal:>+12,.2f}")

    print()
    print("=" * _W)
    print("  [OK] Alpaca ライブ API 疎通確認: 成功")
    print("  ※ このスクリプトは注文を一切送信していません")
    print("=" * _W)
    print()

    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Alpaca ライブ API 疎通確認（注文なし・read-only）"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="アカウント詳細情報も表示する",
    )
    args = parser.parse_args()

    success = run_connectivity_check(verbose=args.verbose)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
