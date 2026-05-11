"""
tools/circuit_breaker.py — ポートフォリオ全体のドローダウン自動停止機構

状態機械:
  OPEN      → 通常稼働（新規 BUY 許可）
  SOFT_TRIP → 日次ドローダウン ≥ -5%  → 当日の新規 BUY を禁止（翌日自動リセット）
  HARD_TRIP → 高値比ドローダウン ≥ -10% → 全 BUY を禁止（manual_reset(level="hard") で解除）

状態は data/circuit_breaker_state.json に永続化されるため、
プロセス再起動後も当日中は tripped 状態が維持される。

使用例:
    cb = CircuitBreaker()
    result = cb.check(current_equity=9500.0)
    if not result.buy_allowed:
        print(f"Circuit tripped: {result.status} — {result.reason}")
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "circuit_breaker_state.json"

DAILY_DRAWDOWN_LIMIT = -0.05   # -5%  → SOFT_TRIP
TOTAL_DRAWDOWN_LIMIT = -0.10   # -10% → HARD_TRIP


@dataclass
class CircuitCheckResult:
    status:             str    # "OPEN" | "SOFT_TRIP" | "HARD_TRIP"
    buy_allowed:        bool
    daily_pnl_pct:      float  # 当日基準比（%）
    total_drawdown_pct: float  # 高値比（%）
    reason:             str


class CircuitBreaker:
    """
    Alpaca 口座の日次・累計ドローダウンを監視し、
    閾値超過で新規 BUY を自動停止するサーキットブレーカー。
    """

    def __init__(self, state_path: Path = _STATE_PATH) -> None:
        self._path  = state_path
        self._state = self._load()

    # ─────────────────────────────────────────────────────────
    # 内部: 状態管理
    # ─────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                with self._path.open(encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("[CircuitBreaker] 状態ファイル読み込み失敗: %s — リセット", e)
        return self._default_state(equity=None)

    def _default_state(self, equity: float | None) -> dict:
        return {
            "status":                "OPEN",
            "daily_baseline_equity": equity,
            "high_water_mark":       equity,
            "last_reset_date":       str(date.today()),
            "last_updated":          datetime.now().isoformat(),
            "trip_reason":           None,
            "trip_time":             None,
            "daily_pnl_pct":         0.0,
            "total_drawdown_pct":    0.0,
        }

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

    def _reset_daily_if_needed(self, current_equity: float) -> None:
        """新しい取引日の開始時に日次ベースラインをリセットする。"""
        today_str = str(date.today())
        if self._state.get("last_reset_date") == today_str:
            return

        logger.info("[CircuitBreaker] 新しい取引日 — 日次ベースラインをリセット")
        prev_hwm    = self._state.get("high_water_mark") or current_equity
        prev_status = self._state.get("status", "OPEN")

        self._state = self._default_state(equity=current_equity)
        self._state["high_water_mark"] = max(prev_hwm, current_equity)

        # HARD_TRIP は手動解除まで引き継ぐ
        if prev_status == "HARD_TRIP":
            self._state["status"]      = "HARD_TRIP"
            self._state["trip_reason"] = "前日からの HARD_TRIP 継続（manual_reset(level='hard') で解除）"

        self._save()

    # ─────────────────────────────────────────────────────────
    # 公開 API
    # ─────────────────────────────────────────────────────────

    def check(self, current_equity: float) -> CircuitCheckResult:
        """
        現在の口座残高を受け取り、ドローダウンを計算してステータスを更新する。

        Args:
            current_equity: Alpaca から取得した現在の口座残高 (USD)

        Returns:
            CircuitCheckResult
        """
        self._reset_daily_if_needed(current_equity)

        # 初回起動: baseline / hwm を設定
        if self._state.get("daily_baseline_equity") is None:
            self._state["daily_baseline_equity"] = current_equity
            logger.info("[CircuitBreaker] 日次ベースライン初期設定: $%.2f", current_equity)
        if self._state.get("high_water_mark") is None:
            self._state["high_water_mark"] = current_equity

        baseline  = self._state["daily_baseline_equity"]
        hwm       = self._state["high_water_mark"]

        # 高値更新
        if current_equity > hwm:
            self._state["high_water_mark"] = current_equity
            hwm = current_equity

        # ドローダウン計算
        daily_pnl = (current_equity - baseline) / baseline if baseline > 0 else 0.0
        total_dd  = (current_equity - hwm)       / hwm      if hwm > 0      else 0.0

        self._state["daily_pnl_pct"]      = round(daily_pnl, 6)
        self._state["total_drawdown_pct"] = round(total_dd,  6)
        self._state["last_updated"]       = datetime.now().isoformat()

        current_status = self._state.get("status", "OPEN")

        # ── HARD_TRIP 判定（優先）
        if total_dd <= TOTAL_DRAWDOWN_LIMIT:
            if current_status != "HARD_TRIP":
                reason = (
                    f"高値比ドローダウン {total_dd*100:.2f}% が閾値 "
                    f"{TOTAL_DRAWDOWN_LIMIT*100:.0f}% を超過 "
                    f"(HWM=${hwm:.2f} → 現在=${current_equity:.2f})"
                )
                self._state["status"]      = "HARD_TRIP"
                self._state["trip_reason"] = reason
                self._state["trip_time"]   = datetime.now().isoformat()
                logger.error("[CircuitBreaker] 🚨 HARD_TRIP 発動: %s", reason)

        # ── SOFT_TRIP 判定（OPEN の場合のみ遷移）
        elif daily_pnl <= DAILY_DRAWDOWN_LIMIT and current_status == "OPEN":
            reason = (
                f"日次ドローダウン {daily_pnl*100:.2f}% が閾値 "
                f"{DAILY_DRAWDOWN_LIMIT*100:.0f}% を超過 "
                f"(本日開始=${baseline:.2f} → 現在=${current_equity:.2f})"
            )
            self._state["status"]      = "SOFT_TRIP"
            self._state["trip_reason"] = reason
            self._state["trip_time"]   = datetime.now().isoformat()
            logger.warning("[CircuitBreaker] ⚡ SOFT_TRIP 発動: %s", reason)

        # SOFT_TRIP は日中の回復でも解除しない（翌日リセットまで維持）
        # HARD_TRIP は manual_reset(level="hard") でのみ解除可能

        self._save()

        status      = self._state["status"]
        buy_allowed = status == "OPEN"
        reason_out  = (
            self._state.get("trip_reason")
            or f"正常稼働 (日次{daily_pnl*100:+.2f}% / 高値比{total_dd*100:+.2f}%)"
        )
        return CircuitCheckResult(
            status=status,
            buy_allowed=buy_allowed,
            daily_pnl_pct=round(daily_pnl * 100, 2),
            total_drawdown_pct=round(total_dd * 100, 2),
            reason=reason_out,
        )

    def manual_reset(self, level: str = "soft") -> str:
        """
        SOFT_TRIP / HARD_TRIP を手動で解除する。

        Args:
            level: "soft" → SOFT_TRIP のみ解除
                   "hard" → HARD_TRIP を含めて OPEN に戻す

        Returns:
            解除後のステータス文字列
        """
        current = self._state.get("status", "OPEN")
        if level == "hard" and current == "HARD_TRIP":
            self._state.update({"status": "OPEN", "trip_reason": None, "trip_time": None})
            logger.warning("[CircuitBreaker] ⚠️  HARD_TRIP を手動解除。取引を再開します。")
        elif level == "soft" and current == "SOFT_TRIP":
            self._state.update({"status": "OPEN", "trip_reason": None, "trip_time": None})
            logger.info("[CircuitBreaker] SOFT_TRIP を手動解除。")
        else:
            logger.info("[CircuitBreaker] 解除対象なし (現在: %s, 指定 level: %s)", current, level)
        self._save()
        return self._state["status"]

    @property
    def status(self) -> str:
        return self._state.get("status", "OPEN")

    @property
    def state(self) -> dict:
        return dict(self._state)
