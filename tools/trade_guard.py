"""
tools/trade_guard.py — 発注ガードレール

BUY 発注前に以下の 3 条件をすべて確認し、1 つでも失敗したら発注をブロックする。

  1. 日次 BUY 上限      : 1日あたりの最大発注回数 (デフォルト: 3 回)
  2. 同時保有銘柄数上限 : ポートフォリオの最大銘柄数 (デフォルト: 5 銘柄)
  3. ポジション比率上限 : 1 銘柄あたり口座資産の最大割合 (デフォルト: 20%)

設定ファイル : data/trade_guards.json  (存在しない場合はデフォルト値を使用)
状態ファイル : data/trade_guard_state.json  (日次 BUY カウントを永続化)

使用例:
    guard = TradeGuard()
    result = guard.check_pre_buy(
        ticker="AAPL",
        order_value=3000.0,    # rec_shares × current_price
        account_equity=15000.0,
        open_positions=3,
    )
    if not result.allowed:
        print(f"発注ブロック: {result.reason}")
    else:
        # 注文を送信して成功したら記録
        guard.record_buy("AAPL")
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path("data/trade_guards.json")
_STATE_PATH  = Path("data/trade_guard_state.json")

_DEFAULT_CONFIG: dict = {
    "max_daily_buys":   3,     # 1 日あたり最大 BUY 発注回数
    "max_positions":    5,     # 同時保有最大銘柄数
    "max_position_pct": 0.20,  # 1 銘柄あたり口座資産の最大割合
}


@dataclass
class GuardCheckResult:
    allowed:          bool
    reason:           str
    daily_buys:       int
    max_daily_buys:   int
    open_positions:   int
    max_positions:    int
    position_pct:     float   # 今回の注文の口座比率 (0.0〜1.0)
    max_position_pct: float


class TradeGuard:
    """
    BUY 発注前のガードレールチェックと日次カウント管理。
    状態は data/trade_guard_state.json に永続化される。
    """

    def __init__(
        self,
        config_path: Path = _CONFIG_PATH,
        state_path:  Path = _STATE_PATH,
    ) -> None:
        self._config_path = config_path
        self._state_path  = state_path
        self._config      = self._load_config()
        self._state       = self._load_state()

    # ─────────────────────────────────────────────────────────
    # 内部: 設定 / 状態
    # ─────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        cfg = dict(_DEFAULT_CONFIG)
        if self._config_path.exists():
            try:
                with self._config_path.open(encoding="utf-8") as f:
                    loaded = json.load(f)
                    # _doc キーは除外してマージ
                    cfg.update({k: v for k, v in loaded.items() if not k.startswith("_")})
            except Exception as e:
                logger.warning("[TradeGuard] 設定読み込み失敗: %s — デフォルト値を使用", e)
        return cfg

    def _load_state(self) -> dict:
        if self._state_path.exists():
            try:
                with self._state_path.open(encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("[TradeGuard] 状態読み込み失敗: %s — リセット", e)
        return self._default_state()

    def _default_state(self) -> dict:
        return {
            "daily_buy_count": 0,
            "last_reset_date": str(date.today()),
            "buy_log":         [],
        }

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._state_path.open("w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

    def _reset_daily_if_needed(self) -> None:
        today_str = str(date.today())
        if self._state.get("last_reset_date") != today_str:
            logger.info("[TradeGuard] 新しい取引日 — 日次 BUY カウントをリセット")
            self._state = self._default_state()
            self._save_state()

    # ─────────────────────────────────────────────────────────
    # 公開 API
    # ─────────────────────────────────────────────────────────

    def check_pre_buy(
        self,
        ticker:         str,
        order_value:    float,   # rec_shares × current_price (USD)
        account_equity: float,   # Alpaca 口座の equity (USD)
        open_positions: int,     # 現在の保有銘柄数
    ) -> GuardCheckResult:
        """
        BUY 発注前にすべてのガードレールを確認する。

        Returns:
            GuardCheckResult — allowed=False の場合は発注しない
        """
        self._reset_daily_if_needed()

        max_daily   = self._config["max_daily_buys"]
        max_pos     = self._config["max_positions"]
        max_pct     = self._config["max_position_pct"]
        daily_count = self._state.get("daily_buy_count", 0)
        pos_pct     = (order_value / account_equity) if account_equity > 0 else 0.0

        # ── チェック 1: 日次 BUY 上限
        if daily_count >= max_daily:
            reason = (
                f"日次 BUY 上限に到達 ({daily_count}/{max_daily} 回) "
                f"— 本日の追加発注を停止"
            )
            logger.warning("[TradeGuard] ❌ %s", reason)
            return GuardCheckResult(
                allowed=False, reason=reason,
                daily_buys=daily_count, max_daily_buys=max_daily,
                open_positions=open_positions, max_positions=max_pos,
                position_pct=round(pos_pct, 4), max_position_pct=max_pct,
            )

        # ── チェック 2: 同時保有銘柄数上限
        if open_positions >= max_pos:
            reason = (
                f"同時保有上限に到達 ({open_positions}/{max_pos} 銘柄) "
                f"— {ticker} の追加保有を停止"
            )
            logger.warning("[TradeGuard] ❌ %s", reason)
            return GuardCheckResult(
                allowed=False, reason=reason,
                daily_buys=daily_count, max_daily_buys=max_daily,
                open_positions=open_positions, max_positions=max_pos,
                position_pct=round(pos_pct, 4), max_position_pct=max_pct,
            )

        # ── チェック 3: ポジション比率上限
        if pos_pct > max_pct:
            reason = (
                f"ポジション比率上限超過: {ticker} の注文金額 "
                f"${order_value:,.0f} = 口座の {pos_pct*100:.1f}% "
                f"(上限: {max_pct*100:.0f}%)"
            )
            logger.warning("[TradeGuard] ❌ %s", reason)
            return GuardCheckResult(
                allowed=False, reason=reason,
                daily_buys=daily_count, max_daily_buys=max_daily,
                open_positions=open_positions, max_positions=max_pos,
                position_pct=round(pos_pct, 4), max_position_pct=max_pct,
            )

        # ── 全チェック通過
        reason = (
            f"全ガードレール通過: 日次 {daily_count + 1}/{max_daily} 回  "
            f"保有 {open_positions + 1}/{max_pos} 銘柄  "
            f"注文比率 {pos_pct * 100:.1f}%/{max_pct * 100:.0f}%"
        )
        logger.info("[TradeGuard] ✅ %s", reason)
        return GuardCheckResult(
            allowed=True, reason=reason,
            daily_buys=daily_count, max_daily_buys=max_daily,
            open_positions=open_positions, max_positions=max_pos,
            position_pct=round(pos_pct, 4), max_position_pct=max_pct,
        )

    def record_buy(self, ticker: str) -> None:
        """BUY 注文の成功を記録する（日次カウントを +1）。"""
        self._reset_daily_if_needed()
        self._state["daily_buy_count"] = self._state.get("daily_buy_count", 0) + 1
        self._state["buy_log"].append({
            "ticker": ticker.upper(),
            "time":   datetime.now().isoformat(),
        })
        self._save_state()
        logger.info(
            "[TradeGuard] BUY 記録: %s  本日 %d 回目 / 上限 %d 回",
            ticker, self._state["daily_buy_count"], self._config["max_daily_buys"],
        )

    @property
    def config(self) -> dict:
        return dict(self._config)

    @property
    def daily_buy_count(self) -> int:
        self._reset_daily_if_needed()
        return self._state.get("daily_buy_count", 0)
