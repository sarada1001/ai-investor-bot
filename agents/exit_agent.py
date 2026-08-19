"""
exit_agent.py — 保有ポジション監視・売却判断エージェント

main.py の Selling Loop（Stage 0）で Buying Loop の前に毎日実行され、
data/portfolio.json の全保有銘柄に対して以下の基準で売却判断を行う。

判断基準（この順に評価する）:
  TAKE_PROFIT  : 現在価格 >= 目標株価、または含み益 >= +10%
  STOP_LOSS    : 現在価格 <= ストップロス価格、または含み損 <= -5%
  MAX_HOLD     : 保有期間が MAX_HOLD_DAYS 営業日に到達（TIME_EXIT）
  THESIS_BROKEN: 購入時の理由が現在ニュースで LLM 判定により否定された
  HOLD         : 上記以外（継続保有）

MAX_HOLD を THESIS_BROKEN より前に置くのは、時間切れが確定している
ポジションで LLM API を消費しないようにするため。

依存:
  - data/portfolio.json   : 保有ポジション一覧
  - engine/constants       : MAX_HOLD_DAYS（0 で TIME_EXIT 無効）
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

import numpy as np
import yfinance as yf
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import skills.news_monitor as _news_mod
import skills.training_data_collector as _training_mod
from engine.constants import MAX_HOLD_DAYS
from tools.auto_logger import ObsidianLogger

logger = logging.getLogger(__name__)

PORTFOLIO_PATH  = Path(__file__).parent.parent / "data" / "portfolio.json"
OBSIDIAN_LOGS   = Path(__file__).parent.parent / "data" / "knowledge_base" / "obsidian_logs"

TAKE_PROFIT_PCT = 10.0   # 固定利確フォールバック（ATR価格未設定時のみ適用）
STOP_LOSS_PCT   = -5.0   # 固定損切りフォールバック（ATR価格未設定時のみ適用）

# LLM 停止時のルールベースフォールバック用キーワード
_THESIS_BREAK_KEYWORDS: frozenset[str] = frozenset({
    # English — earnings / guidance
    "earnings miss", "missed expectations", "below expectations",
    "guidance cut", "guidance reduced", "guidance lowered", "cut guidance",
    "profit warning", "revenue miss", "revenue decline",
    # English — corporate events
    "fraud", "investigation", "lawsuit", "accounting irregulari",
    "recall", "downgrade", "ceo resign", "ceo fired", "layoffs",
    # Japanese
    "下方修正", "業績悪化", "赤字転落", "不正", "リコール",
    "訴訟", "格下げ", "減収", "大幅減益",
})


def _rule_based_thesis_break(news_text: str) -> bool:
    """
    Conservative rule-based thesis break detection used when LLM is unavailable.

    Returns True only when:
      1. Every article in news_text carries negative sentiment, AND
      2. At least one strong thesis-break keyword appears in the text.

    Deliberately conservative to avoid spurious SELL signals.
    """
    if not news_text:
        return False

    lines = [ln for ln in news_text.splitlines() if ln.strip().startswith("-")]
    if not lines:
        return False

    neg_count = sum(1 for ln in lines if "[negative]" in ln.lower())
    all_negative = neg_count == len(lines)

    has_break_keyword = any(kw in news_text.lower() for kw in _THESIS_BREAK_KEYWORDS)

    return all_negative and has_break_keyword


def _business_days_held(entry_date: str, today: date | None = None) -> int | None:
    """
    entry_date から today までの経過営業日数を返す。判定不能なら None。

    None を返すケース（呼び出し側は TIME_EXIT をスキップして HOLD 側に倒す）:
      - entry_date が空文字 / None
      - entry_date が ISO 形式としてパースできない
      - entry_date が未来日

    既知の制約: numpy.busday_count は土日のみを除外し、**米国市場の祝日は
    営業日としてカウントする**。祝日カレンダー用の依存パッケージを増やさない
    方針のため、実際の営業日数よりわずかに多く数えられ、TIME_EXIT が本来より
    少し早く発火しうる（保有を引き延ばさない方向の誤差）。
    """
    if not entry_date:
        return None

    try:
        entry = date.fromisoformat(str(entry_date)[:10])
    except (TypeError, ValueError):
        return None

    ref = today or date.today()
    if entry > ref:
        return None

    return int(np.busday_count(entry, ref))


def _safe_log_path(base: Path, filename: str) -> Path | None:
    """base ディレクトリの外に出ないことを resolve() で保証してパスを返す。"""
    if not filename:
        return None
    resolved = (base / filename).resolve()
    if not str(resolved).startswith(str(base.resolve())):
        logger.warning("[ExitAgent] 不正なログファイルパス（拒否）: %s", filename)
        return None
    return resolved


# ============================================================
# ExitAgent
# ============================================================

class ExitAgent:
    """保有ポジション監視・売却判断エージェント。"""

    NAME = "ExitAgent"

    def __init__(self, bbs, llm_model: str | None = None) -> None:
        """llm_model=None（既定）で .env の GEMINI_MODEL に従う。

        以前はここで既定値にモデル名を直書きしており、.env の設定を
        常に上書きしていた。廃止モデルを指し続けた結果、thesis 判定が
        毎回 429 でルールベースにフォールバックしていた。
        明示指定はテスト・調査目的の注入口としてのみ残す。
        """
        self.bbs     = bbs
        from skills.llm_factory import get_llm_instance
        self._llm    = get_llm_instance(gemini_model=llm_model)
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

            # ── broker-side stop の状態確認と厳密キャンセル ──────────
            # 二重 SELL を防ぐため、stop の最終状態を確認してから SELL する。
            #
            # 判定結果（proceed_sell）:
            #   True  → stop を安全にキャンセル確認済み or stop なし → SELL 続行
            #   False → stop が約定済み or キャンセル未確認 → SELL 中止
            proceed_sell = True

            if alpaca_client is not None:
                _stop_id     = pos.get("stop_order_id")
                _stop_status = pos.get("broker_stop_status", "none")

                if _stop_id and _stop_status == "open":
                    # Step 1: キャンセルリクエスト
                    _cancel = alpaca_client.cancel_order(_stop_id)

                    # Step 2: 実際の注文状態を確認（cancel API の成否に関わらず）
                    _order_info   = alpaca_client.get_order(_stop_id)
                    _order_status = (
                        _order_info.get("status", "unknown")
                        if _order_info.get("success") else "unknown"
                    )

                    _CANCELLED_STATUSES = frozenset({
                        "canceled", "cancelled", "expired", "done_for_day",
                    })
                    _FILLED_STATUSES = frozenset({
                        "filled", "partially_filled",
                    })

                    if _order_status in _CANCELLED_STATUSES:
                        # キャンセル確認 → SELL 続行
                        update_position_stop_order(pos["ticker"], _stop_id, "cancelled")
                        logger.info(
                            "  [ExitAgent] %s broker stop キャンセル確認 (status=%s) → SELL 続行",
                            pos["ticker"], _order_status,
                        )

                    elif _order_status in _FILLED_STATUSES:
                        # broker stop が先に約定済み → 二重 SELL を防止
                        logger.info(
                            "  [ExitAgent] %s broker stop が既に約定済み (status=%s)"
                            " — 二重 SELL 防止のため通常 SELL をスキップ",
                            pos["ticker"], _order_status,
                        )
                        update_position_stop_order(pos["ticker"], _stop_id, "filled")
                        decision["action"]    = "HOLD"
                        decision["exit_type"] = "BROKER_STOP_FILLED"
                        decision["reason"]    = (
                            f"broker stop が既に約定 (status={_order_status}) — 二重 SELL 防止"
                        )
                        proceed_sell = False

                    else:
                        # 状態不明またはまだ active → SELL を中止（二重約定リスク）
                        logger.critical(
                            "  [ExitAgent] CRITICAL: %s broker stop のキャンセルを確認できません "
                            "(cancel_api_ok=%s, order_status=%s) "
                            "— 二重 SELL リスクのため SELL を中止します",
                            pos["ticker"], _cancel.get("success"), _order_status,
                        )
                        decision["action"]    = "HOLD"
                        decision["exit_type"] = "STOP_CANCEL_UNCONFIRMED"
                        decision["reason"]    = (
                            f"broker stop キャンセル未確認 (status={_order_status})"
                            " — 二重 SELL 防止"
                        )
                        proceed_sell = False

            if not proceed_sell:
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
        target      = float(pos.get("target_price") or 0)   # ATR-based TP price
        stop        = float(pos.get("stop_loss_price") or 0) # ATR-based SL price

        current = self._fetch_price(ticker)
        if current <= 0:
            return self._build(pos, "HOLD", "PRICE_UNAVAILABLE",
                               "現在価格を取得できません — HOLD で継続", current, 0.0)

        pnl_pct = (current - entry_price) / entry_price * 100.0

        # 利確チェック
        # ATR由来のtarget_priceが設定されていればそれを優先。
        # 未設定の場合のみ固定+10%フォールバックを適用する。
        if target and current >= target:
            reason = f"ATRベース目標株価 ${target:.2f} に到達"
            return self._build(pos, "SELL", "TAKE_PROFIT", reason, current, pnl_pct)
        if not target and pnl_pct >= TAKE_PROFIT_PCT:
            reason = f"含み益 {pnl_pct:+.2f}% が固定利確閾値 +{TAKE_PROFIT_PCT:.0f}% を超過（ATR未設定フォールバック）"
            return self._build(pos, "SELL", "TAKE_PROFIT", reason, current, pnl_pct)

        # 損切りチェック
        # ATR由来のstop_loss_priceが設定されていればそれを優先。
        # 未設定の場合のみ固定-5%フォールバックを適用する。
        if stop and current <= stop:
            reason = f"ATRベースストップロス ${stop:.2f} を下回る"
            return self._build(pos, "SELL", "STOP_LOSS", reason, current, pnl_pct)
        if not stop and pnl_pct <= STOP_LOSS_PCT:
            reason = f"含み損 {pnl_pct:+.2f}% が固定損切り閾値 {STOP_LOSS_PCT:.0f}% を超過（ATR未設定フォールバック）"
            return self._build(pos, "SELL", "STOP_LOSS", reason, current, pnl_pct)

        # 最大保有日数チェック（TIME_EXIT）
        # MAX_HOLD_DAYS が 0 / None のときは分岐ごと無効。
        # LLM を呼ぶ THESIS_BROKEN より前に置き、時間切れが確定している
        # ポジションで API を消費しないようにする。
        if MAX_HOLD_DAYS:
            held_days = _business_days_held(pos.get("entry_date", ""))
            if held_days is None:
                logger.warning(
                    "  [ExitAgent] %s entry_date が不正・未設定・未来日のため "
                    "TIME_EXIT 判定をスキップ: %r",
                    ticker, pos.get("entry_date", ""),
                )
            elif held_days >= MAX_HOLD_DAYS:
                reason = (
                    f"最大保有日数 {MAX_HOLD_DAYS} 営業日を経過"
                    f"（保有 {held_days} 営業日, {pnl_pct:+.2f}%）"
                )
                return self._build(pos, "SELL", "MAX_HOLD", reason, current, pnl_pct)

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
        buy_log_path = _safe_log_path(OBSIDIAN_LOGS, buy_file)
        if buy_log_path:
            self._ob_log.close_log(
                log_path      = buy_log_path,
                outcome       = "CLOSED",
                profit_loss   = pl_str,
                sell_log_name = sell_path.name,
                exit_type     = decision["exit_type"],
                exit_reason   = decision["reason"],
                sell_price    = decision["current_price"],
                sell_date     = date.today().isoformat(),
            )

        # training_data.jsonl の対応レコードに WIN/LOSS を書き戻す
        updated = _training_mod.update_outcome(
            ticker     = decision["ticker"],
            pnl_pct    = decision["pnl_pct"],
            exit_price = decision["current_price"],
            exit_reason= decision["exit_type"],
        )
        if updated:
            logger.info("  [ExitAgent] training_data.jsonl outcome 更新: %s +%d件", decision["ticker"], updated)
        else:
            logger.debug("  [ExitAgent] training_data.jsonl に対応レコードなし（旧ポジションまたは未記録）: %s", decision["ticker"])

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
        log_path = _safe_log_path(OBSIDIAN_LOGS, buy_file)
        if log_path and log_path.exists():
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
            logger.warning(
                "  [ExitAgent] %s thesis LLM 判定エラー: %s → ルールベースフォールバック",
                ticker, e,
            )
            result = _rule_based_thesis_break(news)
            if result:
                logger.warning("  [ExitAgent] %s ルールベース判定: THESIS_BROKEN", ticker)
            return result


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
    entry_order_id: str | None = None,
    stop_order_id: str | None = None,
    broker_stop_status: str = "none",
) -> None:
    """
    portfolio.json に新規ポジションを追加する。
    STRONG BUY 発注成功直後に main.py から呼ぶ。

    broker_stop_status の取り得る値:
      "none"         — broker stop 未作成
      "open"         — Alpaca に GTC stop 注文が生存中
      "pending_fill" — BUY が未約定のため stop 未作成
      "filled"       — broker stop が約定（ポジションが自動決済された）
      "cancelled"    — stop をキャンセル済み（ExitAgent の通常 SELL 前）
      "error"        — stop 作成試みたが失敗（無保護状態）
    """
    portfolio = _load_portfolio()

    # 同一ティッカーが既にある場合は上書きせず警告のみ
    existing = [p for p in portfolio.get("positions", []) if p["ticker"] == ticker.upper()]
    if existing:
        logger.warning("  [ExitAgent] %s は既にポートフォリオに存在します — スキップ", ticker)
        return

    position = {
        "id":                 f"pos_{date.today().strftime('%Y%m%d')}_{ticker.upper()}",
        "ticker":             ticker.upper(),
        "entry_date":         date.today().isoformat(),
        "entry_price":        round(entry_price, 4),
        "shares":             shares,
        "target_price":       round(target_price, 2) if target_price else None,
        "stop_loss_price":    round(stop_loss_price, 2) if stop_loss_price else None,
        "buy_log_file":       buy_log_file,
        "thesis":             thesis,
        "status":             "OPEN",
        "entry_order_id":     entry_order_id,
        "stop_order_id":      stop_order_id,
        "broker_stop_status": broker_stop_status,
    }
    portfolio.setdefault("positions", []).append(position)
    portfolio["updated_at"] = datetime.now().isoformat()
    _save_portfolio(portfolio)
    logger.info(
        "  [ExitAgent] ポジション追加: %s ×%d @ $%.2f  (target=$%s  stop=$%s  broker_stop=%s)",
        ticker, shares, entry_price,
        f"{target_price:.2f}" if target_price else "N/A",
        f"{stop_loss_price:.2f}" if stop_loss_price else "N/A",
        broker_stop_status,
    )


def update_position_stop_order(
    ticker: str,
    stop_order_id: str | None,
    broker_stop_status: str,
    entry_order_id: str | None = None,
    portfolio_path: Path = PORTFOLIO_PATH,
) -> bool:
    """
    portfolio.json の既存ポジションに broker stop 情報を書き込む。

    Returns:
        True  — 更新成功
        False — ティッカーが見つからない / 書き込みエラー
    """
    try:
        portfolio = _load_portfolio()
        updated = False
        for pos in portfolio.get("positions", []):
            if pos["ticker"].upper() == ticker.upper():
                pos["broker_stop_status"] = broker_stop_status
                pos["stop_order_id"]      = stop_order_id
                if entry_order_id is not None:
                    pos["entry_order_id"] = entry_order_id
                updated = True
                break
        if not updated:
            logger.warning("  [ExitAgent] update_position_stop_order: %s not found", ticker)
            return False
        portfolio["updated_at"] = datetime.now().isoformat()
        _save_portfolio(portfolio)
        logger.info(
            "  [ExitAgent] %s broker_stop_status=%s  stop_order_id=%s",
            ticker, broker_stop_status, stop_order_id,
        )
        return True
    except Exception as e:
        logger.error("  [ExitAgent] update_position_stop_order エラー (%s): %s", ticker, e)
        return False


def reconcile_broker_stops(alpaca_client, portfolio_path: Path = PORTFOLIO_PATH) -> dict:
    """
    bot 再起動時: portfolio.json の stop_order_id を Alpaca の状態と照合し、
    必要に応じて broker-side stop を自動再作成する。

    照合対象:
      - Alpaca position qty
      - local portfolio qty
      - stop order qty
      - stop order status

    検出・対応ケース（ポジションごと）:
      ok          — stop_order_id が Alpaca open orders に存在 → 正常
      refilled    — stop_order_id が filled 状態 → broker stop が約定済み
      repaired    — stop が消失 / 未作成だったが自動再作成に成功
      missing     — stop が消失し再作成にも失敗 → CRITICAL ログ
      no_stop     — stop_loss_price がないため再作成不可 → WARNING
      qty_mismatch — stop qty < Alpaca position qty → 一部無保護

    Returns:
        {"ok": [...], "refilled": [...], "repaired": [...],
         "missing": [...], "no_stop": [...], "qty_mismatch": [...]}
    """
    result: dict[str, list[str]] = {
        "ok": [], "refilled": [], "repaired": [],
        "missing": [], "no_stop": [], "qty_mismatch": [],
    }

    if alpaca_client is None:
        return result

    portfolio = _load_portfolio()
    positions = portfolio.get("positions", [])
    if not positions:
        return result

    # Alpaca 側の open orders を一括取得
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        open_orders = alpaca_client._tc.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
        ))
        open_order_ids = {str(o.id) for o in open_orders}
    except Exception as e:
        logger.error("  [BrokerStop] reconcile: open orders 取得失敗: %s", e)
        return result

    # Alpaca の保有ポジション（symbol → qty マップ）
    try:
        alpaca_pos_map: dict[str, float] = {
            p.symbol.upper(): float(p.qty)
            for p in alpaca_client._tc.get_all_positions()
        }
    except Exception as e:
        logger.error("  [BrokerStop] reconcile: positions 取得失敗: %s", e)
        alpaca_pos_map = {}

    def _try_recreate(ticker: str, pos: dict, alpaca_qty: float) -> bool:
        """stop を自動再作成する。成功したら result["repaired"] に追加し True を返す。"""
        sl_price = pos.get("stop_loss_price")
        if not sl_price or float(sl_price) <= 0:
            logger.warning(
                "  [BrokerStop] %s stop_loss_price が未設定 — broker stop を再作成できません",
                ticker,
            )
            result["no_stop"].append(ticker)
            return False
        stop_res = alpaca_client.place_broker_stop(ticker, alpaca_qty, float(sl_price))
        if stop_res.get("success"):
            update_position_stop_order(
                ticker, stop_res["order_id"], "open",
                portfolio_path=portfolio_path,
            )
            result["repaired"].append(ticker)
            logger.info(
                "  [BrokerStop] %s broker stop 自動再作成完了: id=%s  qty=%.0f  stop=$%.2f",
                ticker, stop_res["order_id"], alpaca_qty, float(sl_price),
            )
            return True
        else:
            result["missing"].append(ticker)
            update_position_stop_order(ticker, None, "error", portfolio_path=portfolio_path)
            logger.critical(
                "  [BrokerStop] CRITICAL: %s broker stop 再作成失敗 — ポジションが無保護状態 (%s)",
                ticker, stop_res.get("error"),
            )
            return False

    def _check_qty_mismatch(ticker: str, pos: dict, stop_id: str, alpaca_qty: float) -> None:
        """stop order qty と Alpaca position qty を照合し、不一致をログに出す。"""
        local_qty = float(pos.get("shares", 0))
        if local_qty != alpaca_qty:
            logger.warning(
                "  [BrokerStop] %s qty 不一致: portfolio=%d  Alpaca=%d",
                ticker, int(local_qty), int(alpaca_qty),
            )
        if not stop_id:
            return
        try:
            stop_info = alpaca_client.get_order(stop_id)
            if stop_info.get("success"):
                stop_qty = float(stop_info.get("qty") or 0)
                if stop_qty < alpaca_qty:
                    logger.warning(
                        "  [BrokerStop] %s stop qty 不足: stop=%.0f < Alpaca=%.0f"
                        " — 未保護株数=%.0f",
                        ticker, stop_qty, alpaca_qty, alpaca_qty - stop_qty,
                    )
                    result["qty_mismatch"].append(ticker)
        except Exception as e:
            logger.warning("  [BrokerStop] %s stop qty 確認エラー: %s", ticker, e)

    for pos in positions:
        ticker      = pos["ticker"].upper()
        stop_id     = pos.get("stop_order_id")
        stop_status = pos.get("broker_stop_status", "none")
        alpaca_qty  = alpaca_pos_map.get(ticker)  # None if not in Alpaca

        # Alpaca にポジションがなければ sync_portfolio() が除去するので無視
        if alpaca_qty is None:
            continue

        # ── ケース1: broker stop が "open" のはずの場合 ────────────
        if stop_id and stop_status == "open":
            if stop_id in open_order_ids:
                result["ok"].append(ticker)
                logger.info("  [BrokerStop] reconcile OK: %s (stop_id=%s)", ticker, stop_id)
                _check_qty_mismatch(ticker, pos, stop_id, alpaca_qty)

            else:
                # open orders にない → 状態を個別確認
                order_info = alpaca_client.get_order(stop_id)
                status_str = order_info.get("status", "unknown") if order_info.get("success") else "unknown"

                if status_str in ("filled", "partially_filled"):
                    # broker stop が約定済み
                    result["refilled"].append(ticker)
                    update_position_stop_order(
                        ticker, stop_id, "filled", portfolio_path=portfolio_path,
                    )
                    logger.info(
                        "  [BrokerStop] %s broker stop が約定済み"
                        " → portfolio は次回 sync_portfolio() で除去",
                        ticker,
                    )

                elif status_str in ("canceled", "cancelled", "expired", "rejected", "unknown"):
                    # stop が無効化 or 見つからない → 再作成試行
                    logger.warning(
                        "  [BrokerStop] %s broker stop が消失 (status=%s) — 自動再作成を試みます",
                        ticker, status_str,
                    )
                    _try_recreate(ticker, pos, alpaca_qty)

                else:
                    # pending_cancel など予期しない状態
                    logger.warning(
                        "  [BrokerStop] %s broker stop の状態が不明 (status=%s) — スキップ",
                        ticker, status_str,
                    )

        # ── ケース2: stop が未作成 / 失敗 / pending_fill の場合 ────
        elif not stop_id or stop_status in ("none", "error", "pending_fill"):
            logger.warning(
                "  [BrokerStop] %s broker stop がありません (broker_stop_status=%s)"
                " — 自動再作成を試みます",
                ticker, stop_status,
            )
            _try_recreate(ticker, pos, alpaca_qty)

        # ── ケース3: cancelled / filled は無視（正常終了状態）──────
        # broker_stop_status == "cancelled" は ExitAgent が通常 SELL 済み
        # broker_stop_status == "filled"    は broker stop 約定済み
        # どちらも sync_portfolio() が除去するので何もしない

    return result


# ============================================================
# Internal helpers
# ============================================================

def _derive_rule(exit_type: str, pnl: float) -> str:
    if exit_type == "TAKE_PROFIT":
        return f"利確成功 ({pnl:+.2f}%)。同様のセットアップでルール継続適用。"
    if exit_type == "STOP_LOSS":
        return f"損切り実行 ({pnl:+.2f}%)。エントリー条件を再検証すること。"
    if exit_type == "MAX_HOLD":
        return f"最大保有日数到達で機械的決済 ({pnl:+.2f}%)。想定期間内に動かない銘柄に資金を寝かせないこと。"
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
