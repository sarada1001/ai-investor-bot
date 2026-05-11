"""
tools/live_trading_gate.py — ライブ取引二段階認証ゲート

ALPACA_PAPER_TRADING=false に設定しただけでは実弾取引は有効にならない。
「環境変数の変更」＋「意思ファイルの明示的な作成」の 2 要素が揃って初めて
ライブ取引の発注が通過する。

有効化コマンド（インタラクティブウィザード）:
    python main.py --enable-live

有効期限:
    24 時間。毎取引日の開始前に再認証が必要。

意思ファイル: data/live_trading_enabled.json
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_INTENT_PATH  = Path(__file__).resolve().parent.parent / "data" / "live_trading_enabled.json"
_EXPIRY_HOURS = 24
_IS_PAPER     = os.getenv("ALPACA_PAPER_TRADING", "True").lower() != "false"
_KEY_SUFFIX   = os.getenv("APCA_API_KEY_ID", "")[-4:] or "????"


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
        """発注前に呼び出す。allowed=False なら注文を送信しないこと。"""
        if _IS_PAPER:
            return LiveGateResult(
                allowed=True, reason="ペーパーモード（安全）",
                is_live=False,
            )

        # ── ペーパーモード OFF → 意思ファイルを確認 ──────────────
        if not _INTENT_PATH.exists():
            reason = (
                "ライブ取引は無効です。有効化するには:\n"
                "  python main.py --enable-live\n"
                f"  ファイルが存在しません: {_INTENT_PATH}"
            )
            logger.error("[LiveTradingGate] ❌ 意思ファイルなし")
            return LiveGateResult(allowed=False, reason=reason, is_live=True)

        try:
            intent = json.loads(_INTENT_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            reason = f"意思ファイルの読み込み失敗: {e} — python main.py --enable-live で再作成してください"
            logger.error("[LiveTradingGate] ❌ %s", reason)
            return LiveGateResult(allowed=False, reason=reason, is_live=True)

        if not intent.get("enabled"):
            reason = "意思ファイルの enabled=false です。python main.py --enable-live で再有効化してください"
            logger.error("[LiveTradingGate] ❌ enabled=false")
            return LiveGateResult(allowed=False, reason=reason, is_live=True)

        # ── 有効期限チェック ─────────────────────────────────────
        enabled_at_str = intent.get("enabled_at", "")
        try:
            enabled_at = datetime.fromisoformat(enabled_at_str)
            expires_at = enabled_at + timedelta(hours=_EXPIRY_HOURS)
            if datetime.now() > expires_at:
                reason = (
                    f"ライブ取引の認証が失効しました（有効期限: {expires_at:%Y-%m-%d %H:%M:%S}）\n"
                    "  python main.py --enable-live で再認証してください"
                )
                logger.error("[LiveTradingGate] ❌ 認証失効")
                return LiveGateResult(
                    allowed=False, reason=reason, is_live=True,
                    expires_at=expires_at.isoformat(),
                )
        except ValueError:
            reason = f"意思ファイルの enabled_at が不正です: '{enabled_at_str}'"
            logger.error("[LiveTradingGate] ❌ %s", reason)
            return LiveGateResult(allowed=False, reason=reason, is_live=True)

        # ── API キー末尾一致チェック ─────────────────────────────
        file_suffix = intent.get("key_suffix", "")
        if file_suffix != _KEY_SUFFIX:
            reason = (
                f"API キーが意思ファイルと一致しません "
                f"(ファイル: ****{file_suffix} / 現在: ****{_KEY_SUFFIX})\n"
                "  python main.py --enable-live で再認証してください"
            )
            logger.error("[LiveTradingGate] ❌ APIキー不一致")
            return LiveGateResult(allowed=False, reason=reason, is_live=True)

        logger.info(
            "[LiveTradingGate] ✅ ライブ取引認証済み (期限: %s / key: ****%s)",
            expires_at.strftime("%Y-%m-%d %H:%M"), _KEY_SUFFIX,
        )
        return LiveGateResult(
            allowed=True, is_live=True,
            reason=f"ライブ取引認証済み (期限: {expires_at:%Y-%m-%d %H:%M})",
            expires_at=expires_at.isoformat(),
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
