"""
alpaca_client.py — Alpaca Paper Trading 統合クライアント

提供機能:
  AlpacaClient.is_market_open()      — 米国市場の開閉チェック（次回開場時刻付き）
  AlpacaClient.has_position(symbol)  — 保有チェック（二重注文防止）
  AlpacaClient.has_open_order(symbol)— 未決済注文チェック（二重注文防止）
  AlpacaClient.get_position_qty(sym) — 保有株数取得
  AlpacaClient.get_positions()       — 全保有ポジションを dict リストで返す
  AlpacaClient.place_buy(sym, qty)   — 買い成行注文（安全チェック付き）
  AlpacaClient.place_sell(sym, qty)  — 売り成行注文（安全チェック付き）
  AlpacaClient.get_account()         — 口座情報取得
  AlpacaClient.sync_portfolio(path)  — Alpaca 保有 ↔ portfolio.json を同期

エラー応答の形式 (place_buy / place_sell 共通):
  成功: {"success": True,  "order_id": ..., "status": ..., "qty": ..., "fill_price": ...}
  スキップ: {"success": False, "skipped": True,  "skip_reason": "..."}
  失敗:     {"success": False, "skipped": False, "error": "..."}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_API_KEY    = os.getenv("APCA_API_KEY_ID", "")
_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY", "")
_PAPER      = os.getenv("ALPACA_PAPER_TRADING", "True").lower() != "false"

# 遅延インポート: 循環参照を避けるため関数内でインポートする
def _live_gate_check(sym: str, side: str) -> dict | None:
    """ライブモード時に LiveTradingGate を確認する。ブロック時は skipped dict を返す。"""
    if _PAPER:
        return None  # ペーパーモードは常に通過
    from tools.live_trading_gate import LiveTradingGate
    result = LiveTradingGate().check()
    if not result.allowed:
        logger.error(
            "  [AlpacaClient] 🚫 LiveTradingGate ブロック (%s %s): %s",
            side.upper(), sym, result.reason.split("\n")[0],
        )
        return {
            "success": False, "skipped": True,
            "skip_reason": f"LiveTradingGate: {result.reason}",
            "symbol": sym, "side": side,
        }
    return None

PORTFOLIO_PATH = Path("data/portfolio.json")


# ============================================================
# AlpacaClient
# ============================================================

class AlpacaClient:
    """Alpaca Paper Trading API のラッパークライアント。"""

    def __init__(self) -> None:
        from alpaca.trading.client import TradingClient
        self._tc = TradingClient(_API_KEY, _SECRET_KEY, paper=_PAPER)

    # ----------------------------------------------------------
    # 市場時間チェック
    # ----------------------------------------------------------

    def is_market_open(self) -> tuple[bool, str]:
        """
        米国株式市場が開場しているか確認する。

        Returns:
            (is_open: bool, message: str)
            閉場時は次回開場時刻を含むメッセージを返す。
        """
        try:
            clock = self._tc.get_clock()
            if clock.is_open:
                close_time = clock.next_close.strftime("%H:%M %Z")
                return True, f"開場中 (本日の終了: {close_time})"
            next_open = clock.next_open.strftime("%Y-%m-%d %H:%M %Z")
            return False, f"閉場中 (次回開場: {next_open})"
        except Exception as e:
            logger.error("  [AlpacaClient] 市場時間チェックエラー: %s", e)
            return False, f"市場時間チェック失敗: {e}"

    # ----------------------------------------------------------
    # ポジション / 注文チェック
    # ----------------------------------------------------------

    def has_position(self, symbol: str) -> bool:
        """指定銘柄を保有しているか確認する。"""
        try:
            self._tc.get_open_position(symbol.upper())
            return True
        except Exception:
            return False

    def get_position_qty(self, symbol: str) -> int:
        """保有株数を返す（保有していなければ 0）。"""
        try:
            pos = self._tc.get_open_position(symbol.upper())
            return int(float(pos.qty))
        except Exception:
            return 0

    def has_open_order(self, symbol: str) -> bool:
        """指定銘柄に未決済の注文があるか確認する。"""
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        try:
            orders = self._tc.get_orders(GetOrdersRequest(
                status=QueryOrderStatus.OPEN,
                symbols=[symbol.upper()],
            ))
            return len(orders) > 0
        except Exception as e:
            logger.error("  [AlpacaClient] 注文チェックエラー (%s): %s", symbol, e)
            return False

    def get_positions(self) -> list[dict]:
        """全保有ポジションを dict リストで返す。"""
        try:
            return [
                {
                    "symbol":          p.symbol,
                    "qty":             int(float(p.qty)),
                    "avg_entry_price": float(p.avg_entry_price),
                    "current_price":   float(p.current_price) if p.current_price else None,
                    "unrealized_pl":   float(p.unrealized_pl) if p.unrealized_pl else None,
                    "unrealized_plpc": float(p.unrealized_plpc) if p.unrealized_plpc else None,
                }
                for p in self._tc.get_all_positions()
            ]
        except Exception as e:
            logger.error("  [AlpacaClient] ポジション取得エラー: %s", e)
            return []

    # ----------------------------------------------------------
    # 口座情報
    # ----------------------------------------------------------

    def get_account(self) -> dict:
        """口座情報（残高・buying power・equity）を返す。"""
        try:
            acc = self._tc.get_account()
            return {
                "cash":         float(acc.cash),
                "buying_power": float(acc.buying_power),
                "equity":       float(acc.equity),
                "currency":     acc.currency,
                "paper":        _PAPER,
            }
        except Exception as e:
            logger.error("  [AlpacaClient] 口座情報取得エラー: %s", e)
            return {"error": str(e)}

    def get_next_open_seconds(self) -> int:
        """
        次回開場までの残り秒数を返す。

        - 既に開場中なら 0 を返す。
        - 取得失敗時は 3600 を返す。
        - 早起き防止のため計算値に +60 秒バッファを付加する。
        """
        try:
            from datetime import timezone
            clock = self._tc.get_clock()
            if clock.is_open:
                return 0
            now_utc   = datetime.now(timezone.utc)
            next_open = clock.next_open
            if next_open.tzinfo is None:
                next_open = next_open.replace(tzinfo=timezone.utc)
            secs = int((next_open - now_utc).total_seconds()) + 60  # +60s バッファ
            return max(secs, 60)
        except Exception as e:
            logger.error("  [AlpacaClient] get_next_open_seconds エラー: %s", e)
            return 3600

    # ----------------------------------------------------------
    # 注文発行
    # ----------------------------------------------------------

    def place_buy(self, symbol: str, qty: int) -> dict:
        """
        買い成行注文を発行する。

        閉場中でも time_in_force=DAY で送信し、Alpaca が寄り付きに自動執行する。

        安全チェック順序:
          1. 既存ポジション重複チェック
          2. 未決済注文重複チェック
          3. 注文送信

        Returns:
            成功: {"success": True, "order_id": ..., "status": ..., ...}
            スキップ: {"success": False, "skipped": True, "skip_reason": ...}
            失敗: {"success": False, "skipped": False, "error": ...}
        """
        sym = symbol.upper()

        # ── LiveTradingGate: 最終防壁 ──────────────────────────
        _blocked = _live_gate_check(sym, "buy")
        if _blocked is not None:
            return _blocked

        is_open, market_msg = self.is_market_open()
        if not is_open:
            logger.info("  [AlpacaClient] 閉場中のため寄り付き注文として送信 (%s): %s", sym, market_msg)

        if self.has_position(sym):
            logger.warning("  [AlpacaClient] 買い注文スキップ (%s): 既に保有中", sym)
            return {"success": False, "skipped": True, "skip_reason": f"{sym} を既に保有しています（二重注文防止）",
                    "symbol": sym, "qty": qty, "side": "buy"}

        if self.has_open_order(sym):
            logger.warning("  [AlpacaClient] 買い注文スキップ (%s): 未決済注文あり", sym)
            return {"success": False, "skipped": True, "skip_reason": f"{sym} に未決済の注文があります（二重注文防止）",
                    "symbol": sym, "qty": qty, "side": "buy"}

        return self._submit_market_order(sym, qty, "buy")

    def place_sell(self, symbol: str, qty: int | None = None) -> dict:
        """
        売り成行注文を発行する。

        qty=None の場合は Alpaca の保有株数を全て売却する。
        閉場中でも time_in_force=DAY で送信し、Alpaca が寄り付きに自動執行する。

        安全チェック順序:
          1. 保有株数チェック（保有なし → スキップ）
          2. 未決済注文重複チェック
          3. 注文送信
        """
        sym = symbol.upper()

        # ── LiveTradingGate: 最終防壁 ──────────────────────────
        _blocked = _live_gate_check(sym, "sell")
        if _blocked is not None:
            return _blocked

        is_open, market_msg = self.is_market_open()
        if not is_open:
            logger.info("  [AlpacaClient] 閉場中のため寄り付き注文として送信 (%s): %s", sym, market_msg)

        actual_qty = qty if qty is not None else self.get_position_qty(sym)
        if actual_qty <= 0:
            logger.warning("  [AlpacaClient] 売り注文スキップ (%s): 保有なし", sym)
            return {"success": False, "skipped": True, "skip_reason": f"{sym} の保有ポジションがありません",
                    "symbol": sym, "qty": actual_qty, "side": "sell"}

        if self.has_open_order(sym):
            logger.warning("  [AlpacaClient] 売り注文スキップ (%s): 未決済注文あり", sym)
            return {"success": False, "skipped": True, "skip_reason": f"{sym} に未決済の注文があります（二重注文防止）",
                    "symbol": sym, "qty": actual_qty, "side": "sell"}

        return self._submit_market_order(sym, actual_qty, "sell")

    def _submit_market_order(self, symbol: str, qty: int | float, side: str) -> dict:
        """成行注文を Alpaca に送信する（内部共通メソッド）。"""
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        side_enum = OrderSide.BUY if side == "buy" else OrderSide.SELL
        try:
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side_enum,
                time_in_force=TimeInForce.DAY,
            )
            order = self._tc.submit_order(req)
            result = {
                "success":      True,
                "skipped":      False,
                "order_id":     str(order.id),
                "status":       str(order.status),
                "symbol":       order.symbol,
                "qty":          float(order.qty),
                "side":         side,
                "fill_price":   float(order.filled_avg_price) if order.filled_avg_price else None,
                "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
            }
            logger.info(
                "  [AlpacaClient] %s 注文送信完了: id=%s  status=%s  qty=%s",
                side.upper(), result["order_id"], result["status"], result["qty"],
            )
            return result
        except Exception as e:
            logger.error("  [AlpacaClient] %s 注文エラー (%s): %s", side.upper(), symbol, e)
            return {"success": False, "skipped": False, "error": str(e),
                    "symbol": symbol, "qty": qty, "side": side}

    # ----------------------------------------------------------
    # portfolio.json 同期
    # ----------------------------------------------------------

    def sync_portfolio(self, portfolio_path: Path = PORTFOLIO_PATH) -> dict:
        """
        Alpaca の保有ポジションを portfolio.json に同期する。

        - Alpaca にあるが portfolio.json にない → 追加（外部で買われたポジション）
        - portfolio.json にあるが Alpaca にない → 除去（外部で売却済み）

        Returns:
            {"success": bool, "alpaca_positions": int, "added": int, "removed": int}
        """
        path = Path(portfolio_path)
        try:
            alpaca_pos = self._tc.get_all_positions()
        except Exception as e:
            logger.error("  [AlpacaClient] sync_portfolio: ポジション取得失敗: %s", e)
            return {"success": False, "error": str(e)}

        alpaca_symbols = {p.symbol.upper() for p in alpaca_pos}

        # portfolio.json を読み込む
        try:
            portfolio = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            portfolio = {}
        portfolio.setdefault("positions", [])
        portfolio.setdefault("schema_version", "1.0")

        current_map = {p["ticker"].upper(): p for p in portfolio["positions"]}

        # Alpaca ポジションを全件処理（追加 or 株数/価格更新）
        added = 0
        for ap in alpaca_pos:
            sym = ap.symbol.upper()
            entry = float(ap.avg_entry_price)
            actual_qty = int(float(ap.qty))
            if sym not in current_map:
                # 新規追加
                portfolio["positions"].append({
                    "id":              f"pos_sync_{sym}",
                    "ticker":          sym,
                    "entry_date":      datetime.now().strftime("%Y-%m-%d"),
                    "entry_price":     round(entry, 4),
                    "shares":          actual_qty,
                    "target_price":    round(entry * 1.10, 2),
                    "stop_loss_price": round(entry * 0.95, 2),
                    "buy_log_file":    "",
                    "thesis":          "Alpaca sync: 起動時に検出された既存ポジション",
                    "status":          "OPEN",
                })
                added += 1
                logger.info("  [AlpacaClient] sync: %s を portfolio.json に追加", sym)
            else:
                # 既存エントリの株数・entry_price を Alpaca の実際の値で上書き
                existing = current_map[sym]
                if existing.get("shares") != actual_qty or existing.get("entry_price") != round(entry, 4):
                    existing["shares"]      = actual_qty
                    existing["entry_price"] = round(entry, 4)
                    logger.info("  [AlpacaClient] sync: %s を更新 (qty=%d, entry=$%.2f)",
                                sym, actual_qty, entry)

        # portfolio にあるが Alpaca にない → 除去
        before = len(portfolio["positions"])
        portfolio["positions"] = [
            p for p in portfolio["positions"]
            if p["ticker"].upper() in alpaca_symbols
        ]
        removed = before - len(portfolio["positions"])
        if removed:
            logger.info("  [AlpacaClient] sync: %d 件を portfolio.json から除去", removed)

        portfolio["updated_at"] = datetime.now().isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(portfolio, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "success":          True,
            "alpaca_positions": len(alpaca_pos),
            "added":            added,
            "removed":          removed,
        }


# ============================================================
# 動作テスト
# ============================================================

if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    client = AlpacaClient()

    print("=" * 60)
    print("  AlpacaClient 動作テスト")
    print("=" * 60)

    acc = client.get_account()
    print(f"\n  口座情報: equity=${acc.get('equity', 0):,.2f}  "
          f"buying_power=${acc.get('buying_power', 0):,.2f}  paper={acc.get('paper')}")

    is_open, msg = client.is_market_open()
    print(f"  市場ステータス: {'開場' if is_open else '閉場'} — {msg}")

    positions = client.get_positions()
    print(f"  保有ポジション: {len(positions)} 件")
    for p in positions:
        print(f"    {p['symbol']:6s} ×{p['qty']:3d} @ ${p['avg_entry_price']:.2f}  "
              f"P&L: {p.get('unrealized_plpc', 0)*100:+.2f}%")

    print(f"\n  portfolio.json 同期中...")
    sync = client.sync_portfolio()
    print(f"  同期結果: alpaca={sync['alpaca_positions']}  追加={sync['added']}  除去={sync['removed']}")
