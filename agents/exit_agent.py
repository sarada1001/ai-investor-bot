"""
exit_agent.py — 保有ポジション監視・売却判断エージェント

main.py の Selling Loop（Stage 0）で Buying Loop の前に毎日実行され、
data/portfolio.json の全保有銘柄に対して以下の基準で売却判断を行う。

判断基準:
  TAKE_PROFIT  : 現在価格 >= 目標株価、または含み益 >= +10%
  STOP_LOSS    : 現在価格 <= ストップロス価格、または含み損 <= -5%
  THESIS_BROKEN: 購入時の理由が現在ニュースで LLM 判定により否定された
  HOLD         : 上記以外（継続保有）

依存:
  - data/portfolio.json   : 保有ポジション一覧
  - skills/news_monitor   : 最新ニュース取得
  - tools/auto_logger     : 売却ログ生成 & 購入ログの CLOSED 更新
  - yfinance              : 現在価格取得
  - langchain_google_genai: thesis 破綻の LLM 判定
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import yfinance as yf
from langchain_google_genai import ChatGoogleGenerativeAI

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import skills.news_monitor as _news_mod
from tools.auto_logger import ObsidianLogger

logger = logging.getLogger(__name__)

PORTFOLIO_PATH  = Path(__file__).parent.parent / "data" / "portfolio.json"
OBSIDIAN_LOGS   = Path(__file__).parent.parent / "data" / "knowledge_base" / "obsidian_logs"

TAKE_PROFIT_PCT = 10.0   # 含み益がこの % 以上 → 利確
STOP_LOSS_PCT   = -5.0   # 含み損がこの % 以下 → 損切り


# ============================================================
# ExitAgent
# ============================================================

class ExitAgent:
    """保有ポジション監視・売却判断エージェント。"""

    NAME = "ExitAgent"

    def __init__(self, bbs, llm_model: str = "gemini-2.0-flash") -> None:
        self.bbs     = bbs
        self._llm    = ChatGoogleGenerativeAI(model=llm_model, temperature=0)
        self._ob_log = ObsidianLogger()

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def run(self, mock_mode: bool = False, alpaca_client=None) -> list[dict]:
        """
        portfolio.json の全ポジションを評価し、売却判断リストを返す。

        alpaca_client が渡された場合:
          - SELL 判定の銘柄に対して Alpaca 売り注文を発行する
          - 注文成功時のみ portfolio.json から除去・ログ更新
          - 注文失敗時は SELL を HOLD に差し戻し（ポジション保持）

        alpaca_client=None の場合: 従来通り（評価 + ログ + portfolio 更新のみ）
        """
        portfolio = _load_portfolio()
        positions = portfolio.get("positions", [])

        if not positions:
            logger.info("  [ExitAgent] 保有ポジションなし — Selling Loop をスキップ")
            self.bbs.write(self.NAME, "exit_decisions", {
                "date": date.today().isoformat(), "results": [],
            })
            return []

        results:      list[dict] = []
        sell_tickers: set[str]   = set()

        for pos in positions:
            decision = self._evaluate(pos, mock_mode=mock_mode)
            results.append(decision)
            logger.info(
                "  [ExitAgent] %s → %s (%s) | P&L: %+.2f%%",
                pos["ticker"], decision["action"], decision["exit_type"], decision["pnl_pct"],
            )

            if decision["action"] != "SELL":
                continue

            # Alpaca 売り注文（live mode のみ）
            order_result: dict | None = None
            if alpaca_client is not None:
                order_result = alpaca_client.place_sell(
                    pos["ticker"], pos.get("shares")
                )
                decision["order_result"] = order_result

                if order_result.get("skipped"):
                    # 市場閉場・重複注文などでスキップ → SELL を HOLD に差し戻し
                    logger.warning(
                        "  [ExitAgent] %s SELL スキップ: %s",
                        pos["ticker"], order_result.get("skip_reason"),
                    )
                    decision["action"]    = "HOLD"
                    decision["exit_type"] = "ORDER_SKIPPED"
                    decision["reason"]    = order_result.get("skip_reason", "注文スキップ")
                    continue

                if not order_result.get("success"):
                    # 注文失敗 → SELL キャンセル、ポジション保持
                    logger.error(
                        "  [ExitAgent] %s 売り注文失敗: %s",
                        pos["ticker"], order_result.get("error"),
                    )
                    decision["action"]    = "HOLD"
                    decision["exit_type"] = "ORDER_FAILED"
                    decision["reason"]    = f"注文失敗: {order_result.get('error')}"
                    continue

            # 注文成功 or mock_mode → ログ更新 + portfolio から除去
            sell_tickers.add(pos["ticker"])
            try:
                self._record_exit(pos, decision, order_result=order_result)
            except Exception as e:
                logger.error("  [ExitAgent] %s ログ更新エラー: %s", pos["ticker"], e)

        if sell_tickers:
            portfolio["positions"] = [
                p for p in positions if p["ticker"] not in sell_tickers
            ]
            portfolio["updated_at"] = datetime.now().isoformat()
            _save_portfolio(portfolio)

        self.bbs.write(self.NAME, "exit_decisions", {
            "date":    date.today().isoformat(),
            "results": results,
        })
        return results

    # ----------------------------------------------------------
    # 評価コア
    # ----------------------------------------------------------

    def _evaluate(self, pos: dict, mock_mode: bool = False) -> dict:
        ticker      = pos["ticker"]
        entry_price = float(pos["entry_price"])
        target      = float(pos.get("target_price") or 0)
        stop        = float(pos.get("stop_loss_price") or 0)

        current = self._fetch_price(ticker)
        if current <= 0:
            return self._build(pos, "HOLD", "PRICE_UNAVAILABLE",
                               "現在価格を取得できません — HOLD で継続", current, 0.0)

        pnl_pct = (current - entry_price) / entry_price * 100.0

        # 利確チェック
        if (target and current >= target) or pnl_pct >= TAKE_PROFIT_PCT:
            reason = (
                f"目標株価 ${target:.2f} に到達"
                if (target and current >= target)
                else f"含み益 {pnl_pct:+.2f}% が利確閾値 +{TAKE_PROFIT_PCT:.0f}% を超過"
            )
            return self._build(pos, "SELL", "TAKE_PROFIT", reason, current, pnl_pct)

        # 損切りチェック
        if (stop and current <= stop) or pnl_pct <= STOP_LOSS_PCT:
            reason = (
                f"ストップロス ${stop:.2f} を下回る"
                if (stop and current <= stop)
                else f"含み損 {pnl_pct:+.2f}% が損切り閾値 {STOP_LOSS_PCT:.0f}% を超過"
            )
            return self._build(pos, "SELL", "STOP_LOSS", reason, current, pnl_pct)

        # Thesis 破綻チェック（LLM — mock_mode 時はスキップ）
        if not mock_mode:
            thesis    = self._extract_thesis(pos)
            news_text = self._fetch_news(ticker)
            if thesis and self._is_thesis_broken(ticker, thesis, news_text):
                return self._build(
                    pos, "SELL", "THESIS_BROKEN",
                    "購入時の thesis が現在のニュースにより否定された（LLM 判定）",
                    current, pnl_pct,
                )

        return self._build(pos, "HOLD", "CONTINUE",
                           f"利確/損切り条件未達・Thesis 継続 ({pnl_pct:+.2f}%)",
                           current, pnl_pct)

    @staticmethod
    def _build(pos: dict, action: str, exit_type: str, reason: str,
               current: float, pnl_pct: float) -> dict:
        return {
            "ticker":        pos["ticker"],
            "action":        action,
            "exit_type":     exit_type,
            "reason":        reason,
            "entry_price":   pos["entry_price"],
            "current_price": round(current, 4),
            "pnl_pct":       round(pnl_pct, 2),
            "shares":        pos.get("shares", 0),
            "entry_date":    pos.get("entry_date", ""),
            "buy_log_file":  pos.get("buy_log_file", ""),
        }

    # ----------------------------------------------------------
    # ログ更新
    # ----------------------------------------------------------

    def _record_exit(self, pos: dict, decision: dict, order_result: dict | None = None) -> None:
        """SELL 時: 売却ログを新規生成し、購入ログを PENDING → CLOSED に更新する。"""
        pnl    = decision["pnl_pct"]
        pl_str = f"{'+' if pnl >= 0 else ''}{pnl:.2f}%"

        context_lines = [
            f"売却理由: {decision['exit_type']}",
            decision["reason"],
            f"購入日: {pos.get('entry_date', 'N/A')} / "
            f"購入単価: ${float(pos['entry_price']):.2f} / "
            f"売却単価: ${decision['current_price']:.2f}",
        ]
        if order_result and order_result.get("success"):
            fill = order_result.get("fill_price")
            context_lines += [
                "--- Alpaca 注文 ---",
                f"注文ID: {order_result.get('order_id', 'N/A')}",
                f"ステータス: {order_result.get('status', 'N/A')}",
                f"約定価格: ${fill:.2f}" if fill else "約定価格: pending",
            ]

        sell_path = self._ob_log.save_log({
            "ticker":          decision["ticker"],
            "action":          "SELL",
            "context":         "\n".join(context_lines),
            "outcome":         "CLOSED",
            "profit_loss":     pl_str,
            "root_cause":      decision["reason"],
            "rule_for_future": _derive_rule(decision["exit_type"], pnl),
            "tags":            ["exit", decision["exit_type"].lower(), decision["ticker"].lower()],
        })
        logger.info("  [ExitAgent] 売却ログ保存: %s", sell_path.name)

        buy_file = pos.get("buy_log_file", "")
        if buy_file:
            self._ob_log.close_log(
                log_path      = OBSIDIAN_LOGS / buy_file,
                outcome       = "CLOSED",
                profit_loss   = pl_str,
                sell_log_name = sell_path.name,
                exit_type     = decision["exit_type"],
                exit_reason   = decision["reason"],
                sell_price    = decision["current_price"],
                sell_date     = date.today().isoformat(),
            )

    # ----------------------------------------------------------
    # データ取得
    # ----------------------------------------------------------

    def _fetch_price(self, ticker: str) -> float:
        try:
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            logger.error("  [ExitAgent] %s 価格取得エラー: %s", ticker, e)
        return 0.0

    def _fetch_news(self, ticker: str) -> str:
        try:
            result   = _news_mod.fetch_ticker_news(ticker, max_articles=3)
            articles = result.get("articles", [])
            return "\n".join(
                f"- [{a.get('sentiment', '?')}] {a.get('title', '')}: {a.get('reason', '')}"
                for a in articles
            )
        except Exception as e:
            logger.error("  [ExitAgent] %s ニュース取得エラー: %s → フォールバック: 空文字", ticker, e)
            return ""

    def _extract_thesis(self, pos: dict) -> str:
        """購入ログの '当時の市場コンテキスト' セクションを抽出する。"""
        buy_file = pos.get("buy_log_file", "")
        if buy_file:
            log_path = OBSIDIAN_LOGS / buy_file
            if log_path.exists():
                try:
                    text  = log_path.read_text(encoding="utf-8")
                    start = text.find("## 1. 当時の市場コンテキスト")
                    if start != -1:
                        end     = text.find("\n## 2.", start)
                        section = text[start + len("## 1. 当時の市場コンテキスト"):
                                       end if end != -1 else start + 600]
                        return section.strip()
                except Exception as e:
                    logger.error("  [ExitAgent] thesis 抽出エラー (%s): %s", buy_file, e)
        return pos.get("thesis", "")

    def _is_thesis_broken(self, ticker: str, thesis: str, news: str) -> bool:
        if not news:
            return False
        prompt = (
            f"あなたはスイングトレードの厳格なリスク管理者です。\n\n"
            f"銘柄: {ticker}\n\n"
            f"【購入時の理由（thesis）】\n{thesis}\n\n"
            f"【現在のニュース】\n{news}\n\n"
            f"現在のニュースは購入時の理由を明確に否定していますか？\n"
            f"YES または NO だけで答えてください。\n\n"
            f"判断基準:\n"
            f"- 「好決算期待」→ 決算ミス・ガイダンス下方修正 → YES\n"
            f"- 「テクニカルブレイクアウト」→ 重大なネガティブ決算・不正発覚 → YES\n"
            f"- 軽微なネガティブニュース → NO\n"
            f"- 無関係なニュース → NO"
        )
        try:
            response = self._llm.invoke(prompt).content.strip().upper()
            return response.startswith("YES")
        except Exception as e:
            logger.error("  [ExitAgent] thesis LLM 判定エラー: %s → フォールバック: NO", e)
            return False


# ============================================================
# Portfolio I/O（public — main.py から呼び出し可能）
# ============================================================

def _load_portfolio() -> dict:
    if not PORTFOLIO_PATH.exists():
        return {"updated_at": datetime.now().isoformat(), "schema_version": "1.0", "positions": []}
    try:
        with open(PORTFOLIO_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("  [ExitAgent] portfolio.json 読み込みエラー: %s → 空ポートフォリオで継続", e)
        return {"updated_at": datetime.now().isoformat(), "schema_version": "1.0", "positions": []}


def _save_portfolio(data: dict) -> None:
    PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("  [ExitAgent] portfolio.json を更新しました")


def add_position(
    ticker: str,
    entry_price: float,
    shares: int,
    target_price: float | None = None,
    stop_loss_price: float | None = None,
    buy_log_file: str = "",
    thesis: str = "",
) -> None:
    """
    portfolio.json に新規ポジションを追加する。
    STRONG BUY 発注成功直後に main.py から呼ぶ。
    """
    portfolio = _load_portfolio()

    # 同一ティッカーが既にある場合は上書きせず警告のみ
    existing = [p for p in portfolio.get("positions", []) if p["ticker"] == ticker.upper()]
    if existing:
        logger.warning("  [ExitAgent] %s は既にポートフォリオに存在します — スキップ", ticker)
        return

    position = {
        "id":              f"pos_{date.today().strftime('%Y%m%d')}_{ticker.upper()}",
        "ticker":          ticker.upper(),
        "entry_date":      date.today().isoformat(),
        "entry_price":     round(entry_price, 4),
        "shares":          shares,
        "target_price":    round(target_price, 2) if target_price else None,
        "stop_loss_price": round(stop_loss_price, 2) if stop_loss_price else None,
        "buy_log_file":    buy_log_file,
        "thesis":          thesis,
        "status":          "OPEN",
    }
    portfolio.setdefault("positions", []).append(position)
    portfolio["updated_at"] = datetime.now().isoformat()
    _save_portfolio(portfolio)
    logger.info(
        "  [ExitAgent] ポジション追加: %s ×%d @ $%.2f  (target=$%s  stop=$%s)",
        ticker, shares, entry_price,
        f"{target_price:.2f}" if target_price else "N/A",
        f"{stop_loss_price:.2f}" if stop_loss_price else "N/A",
    )


# ============================================================
# Internal helpers
# ============================================================

def _derive_rule(exit_type: str, pnl: float) -> str:
    if exit_type == "TAKE_PROFIT":
        return f"利確成功 ({pnl:+.2f}%)。同様のセットアップでルール継続適用。"
    if exit_type == "STOP_LOSS":
        return f"損切り実行 ({pnl:+.2f}%)。エントリー条件を再検証すること。"
    if exit_type == "THESIS_BROKEN":
        return "購入理由が崩れた時点での即時撤退は正しい判断。感情的なホールドを避けること。"
    return "継続保有中。次回チェックへ。"


# ============================================================
# 動作テスト
# ============================================================

if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # BBS スタブ（単体テスト用）
    class _StubBBS:
        def write(self, *args, **kwargs):
            print(f"  [BBS stub] write({args[0]!r}, {args[1]!r})")

    print("=" * 60)
    print("  ExitAgent 動作テスト (mock_mode=True)")
    print("=" * 60)

    portfolio = _load_portfolio()
    print(f"\n  現在の保有ポジション数: {len(portfolio.get('positions', []))}")

    agent   = ExitAgent(_StubBBS())
    results = agent.run(mock_mode=True)

    print(f"\n  評価結果: {len(results)} 件")
    for r in results:
        print(f"  {r['action']:4s} | {r['ticker']:6s} | P&L: {r['pnl_pct']:+.2f}%  [{r['exit_type']}]")
        print(f"       {r['reason']}")
