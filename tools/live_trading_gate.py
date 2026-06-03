"""
tools/live_trading_gate.py — ライブ取引認証ゲート

ALPACA_PAPER_TRADING=false かつ APCA_API_KEY_ID が .env に設定されていれば
自動的にライブ取引が許可される（cron / デーモン完全自動運用対応）。

旧来の手動認証ウィザード（--enable-live）は後方互換のため残存するが、
check() による発注判定には使用しない。

意思ファイル: data/live_trading_enabled.json（旧互換用、現在は参照しない）
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

_INTENT_PATH  = Path(__file__).resolve().parent.parent / "data" / "live_trading_enabled.json"
_EXPIRY_HOURS = 24  # 旧互換用（check() では使用しない）
_IS_PAPER     = os.getenv("ALPACA_PAPER_TRADING", "True").lower() != "false"
_API_KEY      = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY", "")
_KEY_SUFFIX   = _API_KEY[-4:] if _API_KEY else "????"


@dataclass
class LiveGateResult:
    allowed:    bool
    reason:     str
    is_live:    bool         # True = live モード（ペーパーではない）
    expires_at: str | None = None
    key_suffix: str | None = None


class LiveTradingGate:
    """
    ライブ取引の発注可否を判定する 2 段階認証ゲート。

    通過条件（すべて満たす必要あり）:
      1. ALPACA_PAPER_TRADING=false
      2. data/live_trading_enabled.json が存在する
      3. enabled=true かつ 24 時間未失効
      4. key_suffix が現在の API キー末尾 4 桁と一致
    """

    def check(self) -> LiveGateResult:
        """
        発注前に呼び出す。allowed=False なら注文を送信しないこと。

        判定ロジック:
          - ペーパーモード → 常に通過
          - ライブモード   → .env に APCA_API_KEY_ID が設定されていれば通過
                            （手動 --enable-live / 24h有効期限チェックは不要）
        """
        if _IS_PAPER:
            return LiveGateResult(
                allowed=True, reason="ペーパーモード（安全）",
                is_live=False,
            )

        # ── ライブモード: APIキーが設定されていれば自動許可 ──────
        if not _API_KEY:
            reason = (
                "⚠️  Alpaca API キーが未設定です。\n"
                "  .env に APCA_API_KEY_ID と APCA_API_SECRET_KEY を設定してください。\n"
                "  .env.example を参照してください。"
            )
            logger.error("[LiveTradingGate] ❌ APIキー未設定")
            return LiveGateResult(allowed=False, reason=reason, is_live=True)

        logger.info(
            "[LiveTradingGate] ✅ ライブ取引許可 (key: ****%s)", _KEY_SUFFIX,
        )
        return LiveGateResult(
            allowed=True, is_live=True,
            reason=f"ライブ取引 APIキー確認済 (****{_KEY_SUFFIX})",
            key_suffix=_KEY_SUFFIX,
        )

    # ─────────────────────────────────────────────────────────────
    # ウィザード
    # ─────────────────────────────────────────────────────────────

    @classmethod
    def enable_wizard(cls) -> None:
        """
        インタラクティブな有効化ウィザード。
        python main.py --enable-live から呼び出される。
        """
        _W = 64
        _line = "=" * _W

        print(f"\n{_line}")
        print("  ⚠️  ライブ取引有効化ウィザード")
        print(_line)

        if _IS_PAPER:
            print("\n  [ERROR] ALPACA_PAPER_TRADING が 'false' になっていません。")
            print("  .env を編集して ALPACA_PAPER_TRADING=false を設定してから")
            print("  再度このコマンドを実行してください。\n")
            raise SystemExit(1)

        if not os.getenv("APCA_API_KEY_ID"):
            print("\n  [ERROR] APCA_API_KEY_ID が設定されていません。.env を確認してください。\n")
            raise SystemExit(1)

        print("\n  ⚠️  本操作により実際の資金を使った取引が有効になります。")
        print(f"  対象 API Key : ****{_KEY_SUFFIX}")
        print(f"  有効期限     : {_EXPIRY_HOURS} 時間（毎取引日に再認証が必要）")
        print("\n  キャンセルするには Ctrl+C を押してください。")
        print(f"\n{'-' * _W}")
        print('  確認のため "CONFIRM LIVE TRADING" と入力してください:')
        print(f"{'-' * _W}")

        try:
            answer = input("  > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  キャンセルしました。\n")
            raise SystemExit(0)

        if answer != "CONFIRM LIVE TRADING":
            print(f"\n  ❌ 入力が一致しません (入力: '{answer}')")
            print("  有効化をキャンセルしました。\n")
            raise SystemExit(1)

        now        = datetime.now()
        expires_at = now + timedelta(hours=_EXPIRY_HOURS)
        intent = {
            "enabled":    True,
            "enabled_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "key_suffix": _KEY_SUFFIX,
            "confirmed_by": "human",
        }
        _INTENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _INTENT_PATH.write_text(json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"\n  ✅ ライブ取引を有効化しました。")
        print(f"     有効期限 : {expires_at:%Y-%m-%d %H:%M:%S}")
        print(f"     API Key  : ****{_KEY_SUFFIX}")
        print(f"     設定ファイル: {_INTENT_PATH}\n")

    @classmethod
    def disable(cls) -> None:
        """意思ファイルを削除してライブ取引を即座に無効化する。"""
        if _INTENT_PATH.exists():
            _INTENT_PATH.unlink()
            logger.warning("[LiveTradingGate] ライブ取引を無効化しました（意思ファイルを削除）")
            print(f"  ✅ ライブ取引を無効化しました ({_INTENT_PATH} を削除)\n")
        else:
            print("  ライブ取引の意思ファイルは存在しません（既に無効状態）。\n")

    @property
    def is_live(self) -> bool:
        return not _IS_PAPER
