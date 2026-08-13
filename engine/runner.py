"""engine/runner.py — ウォッチリストサイクル & デーモンモード"""

from __future__ import annotations

import datetime
import time

import skills.screener as _screener_mod
from tools.alpaca_client import AlpacaClient as _AlpacaClient

from engine.constants   import TARGET_TICKER, DAEMON_INTERVAL_SECS, _W, PRODUCTION_UNIVERSE
from engine.display     import _log
from engine.notify      import send_line_message, send_line_notification
from engine.trade_cycle import run_trade_cycle, run_exit_stage, init_alpaca_and_sync

_DIP_SCAN_INTERVAL_SECS = 900  # 15分ごとに急落スキャン


def _run_dip_scan_subloop(
    effective_tickers: list[str],
    total_sleep_secs:  int,
    notify_line:       bool,
    dip_threshold_pct: float = -3.0,
) -> None:
    """メインサイクルのスリープ中、15分ごとに急落エントリー機会をスキャンする。"""
    remaining = total_sleep_secs
    while remaining > _DIP_SCAN_INTERVAL_SECS:
        time.sleep(_DIP_SCAN_INTERVAL_SECS)
        remaining -= _DIP_SCAN_INTERVAL_SECS

        _log(f"[DipScan] 急落エントリースキャン ({len(effective_tickers)} 銘柄)...")
        try:
            dips = _screener_mod.detect_dip_entries(
                watchlist         = effective_tickers,
                dip_threshold_pct = dip_threshold_pct,
            )
        except Exception as e:
            _log(f"[DipScan] スキャンエラー: {e}")
            continue

        if dips:
            _log(f"[DipScan] 急落候補 {len(dips)} 銘柄:")
            msg_lines = ["【ECC 急落エントリーアラート】"]
            for d in dips:
                line = (
                    f"  {d['ticker']}: {d['momentum_pct']:+.1f}%"
                    f"  (始値 ${d['day_open']:.2f} → 現在 ${d['current_price']:.2f})"
                )
                _log(line)
                msg_lines.append(line.strip())
        else:
            _log(f"[DipScan] 急落候補なし（閾値: {dip_threshold_pct:+.1f}%）")

    if remaining > 0:
        time.sleep(remaining)


def _watchlist_summary(results: list[dict]) -> None:
    """全銘柄の判断を一覧テーブルで表示する。"""
    print(f"\n╔{'═' * _W}╗")
    print(f"║  {'ウォッチリスト 分析サマリー':^{_W - 2}}  ║")
    print(f"╠{'═' * _W}╣")
    print(f"║  {'#':>3}  {'Ticker':<6}  {'Decision':<14}  {'Score':>7}  {'根拠 (60文字)':}")
    print(f"╠{'═' * _W}╣")
    icons = {"STRONG BUY": "🚀", "HOLD": "⏸", "SELL": "📉"}
    for i, r in enumerate(results, 1):
        decision  = r.get("decision", "HOLD")
        score     = r.get("score", 0.0)
        rationale = r.get("rationale", "")[:45]
        ticker    = r.get("ticker", "-")
        icon      = icons.get(decision, "❓")
        line = f"║  {i:>3}  {ticker:<6}  {icon} {decision:<12}  {score:>+7.4f}  {rationale}"
        print(line.ljust(_W + 1) + "║")
    print(f"╚{'═' * _W}╝\n")


def run_watchlist_cycle(
    tickers:          list[str],
    dry_run:          bool             = False,
    notify_line:      bool             = False,
    mock_mode:        bool             = False,
    hybrid_mode:      bool             = False,
    excluded_agents:  list[str] | None = None,
    run_audit:        bool             = False,
    research_mode:    bool             = False,
) -> list[dict]:
    """複数銘柄を順番に run_trade_cycle() で分析し、結果を集約して返す。"""
    results: list[dict] = []

    bar = "◆" * (_W + 2)
    print(f"\n{bar}")
    print(f"  📋  [WATCHLIST]  {len(tickers)} 銘柄を順番に分析します")
    for i, t in enumerate(tickers, 1):
        print(f"    {i}. {t}")
    print(f"{bar}\n")

    # ── Stage 0: Selling Loop（1サイクルにつき1回のみ）───────────────
    # 保有ポジションの評価は分析対象銘柄に依存しないため、銘柄ループの前に
    # 1度だけ実行し、結果を各 run_trade_cycle() へ渡す。
    # 以前は run_trade_cycle() の内部にあり、銘柄数だけ ExitAgent の
    # thesis LLM 判定が重複していた（保有3×分析5 = 同一判定を15回）。
    #
    # research_mode は run_trade_cycle 内で dry_run / notify_line を強制的に
    # 落とすため、外に出した Stage 0 にも同じ抑制を適用する
    # （適用しないと研究モードで実際の売り注文が飛ぶ）。
    _exit_dry_run     = dry_run or research_mode
    _exit_notify_line = notify_line and not research_mode
    try:
        _exit_alpaca = init_alpaca_and_sync(mock_mode)
        exit_results: list[dict] | None = run_exit_stage(
            mock_mode     = mock_mode,
            alpaca_client = _exit_alpaca if not _exit_dry_run else None,
            notify_line   = _exit_notify_line,
        )
    except Exception as e:
        # 保有監視が丸ごと欠落しないよう、失敗時は従来通り
        # 各 run_trade_cycle() 側で Stage 0 を実行させる（リトライ扱い）。
        _log(f"[Watchlist] Stage 0 実行エラー: {e} — 銘柄ごとの Stage 0 にフォールバックします")
        exit_results = None

    _first_run_audit = run_audit
    for ticker in tickers:
        try:
            result = run_trade_cycle(
                ticker          = ticker,
                dry_run         = dry_run,
                notify_line     = notify_line,
                mock_mode       = mock_mode,
                hybrid_mode     = hybrid_mode,
                excluded_agents = excluded_agents,
                run_audit       = _first_run_audit,
                research_mode   = research_mode,
                exit_results    = exit_results,
            )
            _first_run_audit = False
        except Exception as e:
            _log(f"[Watchlist] {ticker} の分析中にエラー: {e} — スキップします")
            result = {"decision": "HOLD", "score": 0.0, "rationale": f"エラー: {e}"}

        result["ticker"] = ticker
        results.append(result)

    _watchlist_summary(results)

    if notify_line:
        send_line_notification(results)
        print("\n[LINE] ウォッチリストサマリー通知送信完了。")

    return results


def _daemon_header(
    tickers:        list[str],
    interval_secs:  int,
    use_screener:   bool,
    screener_top_n: int,
) -> None:
    bar = "◆" * (_W + 2)
    h   = interval_secs // 3600
    m   = (interval_secs % 3600) // 60
    print(f"\n{bar}")
    print(f"  🤖  [DAEMON MODE]  24時間自動取引ボット起動  🤖")
    if use_screener:
        print(f"  モード              : S&P500 スクリーニング → 上位 {screener_top_n} 銘柄を AI 分析")
    else:
        print(f"  対象ティッカー      : {', '.join(tickers)}")
    print(f"  開場中インターバル  : {h}h {m:02d}m  ({interval_secs}秒)")
    print(f"  停止方法            : Ctrl+C")
    print(f"{bar}\n")


def run_daemon(
    ticker:           str             = TARGET_TICKER,
    tickers:          list[str] | None = None,
    notify_line:      bool             = False,
    mock_mode:        bool             = False,
    hybrid_mode:      bool             = False,
    excluded_agents:  list[str] | None = None,
    interval_secs:    int              = DAEMON_INTERVAL_SECS,
    use_screener:     bool             = False,
    screener_top_n:   int              = 5,
) -> None:
    """デーモンモード: 市場開閉に合わせて自動でスリープ/実行を繰り返す無限ループ。"""
    _fixed_tickers: list[str] | None = tickers
    _daemon_header(
        tickers        = _fixed_tickers or [f"S&P500 Top-{screener_top_n}"],
        interval_secs  = interval_secs,
        use_screener   = use_screener and not _fixed_tickers,
        screener_top_n = screener_top_n,
    )

    while True:
        try:
            _alpaca_chk = _AlpacaClient()
            is_open, market_msg = _alpaca_chk.is_market_open()
        except Exception as e:
            _log(f"[Daemon] Alpaca 接続エラー: {e} → 60秒後にリトライ")
            time.sleep(60)
            continue

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'━' * (_W + 2)}")
        print(f"  [Daemon] {now_str}  市場状態: {market_msg}")
        print(f"{'━' * (_W + 2)}")

        if is_open:
            if _fixed_tickers:
                effective_tickers = _fixed_tickers
            elif use_screener:
                _log("[Daemon] S&P500 日中動的スクリーニングを実行します...")
                try:
                    screened = _screener_mod.screen_sp500_intraday(
                        top_n    = screener_top_n,
                        verbose  = True,
                        universe = PRODUCTION_UNIVERSE,
                    )
                    effective_tickers = [s["ticker"] for s in screened]
                except Exception as e:
                    _log(f"[Daemon] スクリーナーエラー: {e} → このサイクルをスキップします")
                    effective_tickers = []
                if not effective_tickers:
                    _log("[Daemon] スクリーナー結果 0 件 → このサイクルをスキップします")
            else:
                effective_tickers = _fixed_tickers or []

            try:
                if not effective_tickers:
                    _log("[Daemon] 有効な銘柄がないためトレード評価をスキップします")
                elif len(effective_tickers) == 1:
                    run_trade_cycle(
                        ticker          = effective_tickers[0],
                        dry_run         = False,
                        notify_line     = notify_line,
                        mock_mode       = mock_mode,
                        hybrid_mode     = hybrid_mode,
                        excluded_agents = excluded_agents,
                    )
                else:
                    run_watchlist_cycle(
                        tickers         = effective_tickers,
                        dry_run         = False,
                        notify_line     = notify_line,
                        mock_mode       = mock_mode,
                        hybrid_mode     = hybrid_mode,
                        excluded_agents = excluded_agents,
                    )
            except KeyboardInterrupt:
                raise
            except Exception as e:
                _log(f"[Daemon] パイプライン実行エラー: {e}")

            next_check = datetime.datetime.now() + datetime.timedelta(seconds=interval_secs)
            h = interval_secs // 3600
            m = (interval_secs % 3600) // 60
            _log(
                f"[Daemon] 次回評価まで {h}h {m:02d}m スリープします "
                f"(再開: {next_check.strftime('%Y-%m-%d %H:%M:%S')})"
            )
            _run_dip_scan_subloop(
                effective_tickers = effective_tickers,
                total_sleep_secs  = interval_secs,
                notify_line       = notify_line,
            )

        else:
            sleep_secs = _alpaca_chk.get_next_open_seconds()
            wake_dt    = datetime.datetime.now() + datetime.timedelta(seconds=sleep_secs)
            wake_str   = wake_dt.strftime("%Y-%m-%d %H:%M:%S")
            h = sleep_secs // 3600
            m = (sleep_secs % 3600) // 60
            print(f"\n╔{'═' * _W}╗")
            print(f"║  [Daemon] 市場閉場中。".ljust(_W + 1) + "║")
            print(f"║  次回開場時刻の {wake_str} までスリープします。".ljust(_W + 1) + "║")
            print(f"║  ({h}h {m:02d}m 後に自動起動)".ljust(_W + 1) + "║")
            print(f"╚{'═' * _W}╝\n")
            time.sleep(sleep_secs)
            if use_screener and not _fixed_tickers:
                _screener_mod.invalidate_cache()
