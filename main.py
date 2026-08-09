"""
main.py — ECC スイングトレード自律エンジン エントリポイント

実装の大部分は engine/ パッケージに移動済み。
このファイルは CLI エントリポイントと後方互換 re-export のみ。

実行フロー:
  Stage 1 : TechnicalAgent + NewsAgent + MacroAgent + SocialAgent（安価スキャン）
  Gate    : マクロ NEGATIVE → ブレーキ発動 HOLD
  Stage 2 : FundamentalAgent（Gate 通過時のみ: Multi-HyDE RAG + EDGAR 取得）
  Stage 3 : ManagerAgent（BBS レポートを総合評価 → Strong Buy のみ発注）
  Stage 4 : RiskAgent（STRONG BUY 時のみ: ポジションサイジング + ストップロス算出）
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

from dotenv import load_dotenv

# .env をプロジェクトルートからの絶対パスで確実に読み込む。
# cron / デーモン / 異なるカレントディレクトリからの起動でも動作する。
load_dotenv(Path(__file__).parent / ".env")

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))

# ── 後方互換 re-export ────────────────────────────────────────
# test_news_trade.py / test_technical_trade.py: from main import BBS
# scripts/run_ablation_test.py: from main import run_trade_cycle
from engine.bbs         import BBS                           # noqa: F401
from engine.trade_cycle import run_trade_cycle               # noqa: F401
from engine.runner      import run_watchlist_cycle, run_daemon  # noqa: F401
from engine.constants   import TARGET_TICKER, DAEMON_INTERVAL_SECS, PRODUCTION_UNIVERSE  # noqa: F401

import skills.screener as _screener_mod


# =========================================================
# CLI エントリポイント
# =========================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="ECC スイングトレード自律エンジン",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── 銘柄指定 ─────────────────────────────────────────────────
    ticker_group = parser.add_mutually_exclusive_group()
    ticker_group.add_argument(
        "--ticker", default=None, help="単一銘柄の分析対象ティッカー（例: --ticker MSFT）",
    )
    ticker_group.add_argument(
        "--tickers", nargs="+", metavar="TICKER",
        help="複数銘柄を指定（例: --tickers AAPL MSFT NVDA）。",
    )
    ticker_group.add_argument(
        "--screen", action="store_true",
        help="S&P500 スクリーニングモード: テクニカルスコアで上位銘柄を絞り込み AI 分析まで実行。",
    )

    # ── スクリーニング設定 ───────────────────────────────────────
    parser.add_argument(
        "--top-n", type=int, default=5, metavar="N",
        help="--screen / デーモン+スクリーニング時の選出銘柄数（デフォルト: 5）",
    )
    parser.add_argument(
        "--screen-only", action="store_true",
        help="スクリーニング結果の表示のみ行い、AI 分析は実行しない（確認用）",
    )

    # ── 実行オプション ───────────────────────────────────────────
    parser.add_argument("--dry-run",     action="store_true", help="Alpaca 発注をスキップしてログのみ出力")
    parser.add_argument("--notify-line", action="store_true", help="最終判断を LINE に通知")
    parser.add_argument("--mock",        action="store_true",
                        help="モックモード: LLM/API 呼び出しをスキップしてシステムフローをテスト")
    parser.add_argument("--hybrid",      action="store_true",
                        help="ハイブリッドモード: リアル分析を実行し発注のみスキップ。学習データ品質向上に推奨。")
    parser.add_argument("--exclude", nargs="+", default=[], metavar="AGENT",
                        help="除外するエージェント名（アブレーション実験用）")
    parser.add_argument("--run-audit",   action="store_true",
                        help="AuditAgent によるエージェント成績評価を実行し agent_status.json を更新する。")
    parser.add_argument("--research-mode", action="store_true", dest="research_mode",
                        help="研究モード: 発注・LINE・Obsidian を完全無効化し "
                             "HOLD 判断を data/research/hold_cases.jsonl に記録する。"
                             "介入実験データ収集用。")

    # ── デーモンモード ───────────────────────────────────────────
    parser.add_argument(
        "--daemon", "--auto", action="store_true", dest="daemon",
        help="デーモンモード: 24時間稼働の自動取引ループ。",
    )
    parser.add_argument(
        "--interval", type=int, default=DAEMON_INTERVAL_SECS, metavar="SECONDS",
        help="デーモンモード: 市場開場中の評価間隔（秒, デフォルト: 3600=1時間）",
    )

    # ── ライブ取引二段階認証 ──────────────────────────────────────
    parser.add_argument(
        "--enable-live", action="store_true",
        help="ライブ取引有効化ウィザードを起動。",
    )
    parser.add_argument(
        "--disable-live", action="store_true",
        help="ライブ取引を即座に無効化する（意思ファイルを削除）。",
    )

    args = parser.parse_args()

    # ── enable-live / disable-live: 他のフローより先に処理して終了 ─
    if getattr(args, "enable_live", False):
        from tools.live_trading_gate import LiveTradingGate
        LiveTradingGate.enable_wizard()
        raise SystemExit(0)

    if getattr(args, "disable_live", False):
        from tools.live_trading_gate import LiveTradingGate
        LiveTradingGate.disable()
        raise SystemExit(0)

    # ── LLM バックエンド起動ログ ─────────────────────────────────────
    from skills.llm_factory import is_ollama_active
    _llm_backend = "ollama" if is_ollama_active() else "gemini"
    print(
        f"[LLM Factory] backend={_llm_backend}"
        f" | DISABLE_GEMINI={os.getenv('DISABLE_GEMINI', 'false')}"
        f" | FORCE_GEMINI={os.getenv('FORCE_GEMINI', 'false')}"
    )

    # ── 組み合わせバリデーション ──────────────────────────────────
    if args.screen_only and args.daemon:
        parser.error("--screen-only は --daemon と同時に指定できません。")

    # ── screen-only: スクリーニング結果のみ表示して終了 ──────────
    if args.screen_only:
        print("\n[Screen-Only モード] S&P500 をスクリーニングします...\n")
        results = _screener_mod.screen_sp500(
            top_n     = args.top_n,
            use_cache = True,
            verbose   = True,
            universe  = PRODUCTION_UNIVERSE,
        )
        print(f"\n上位 {len(results)} 銘柄:")
        for i, r in enumerate(results, 1):
            print(f"  {i}. {r['ticker']:<6}  スコア {r['score']:>2}  {r['reason']}")
        raise SystemExit(0)

    _run_audit_flag   = getattr(args, "run_audit",     False)
    _research_mode    = getattr(args, "research_mode", False)

    # ── daemon モード ─────────────────────────────────────────────
    if args.daemon:
        run_daemon(
            ticker          = args.ticker,
            tickers         = args.tickers,
            notify_line     = args.notify_line,
            mock_mode       = args.mock,
            hybrid_mode     = args.hybrid,
            excluded_agents = args.exclude,
            interval_secs   = args.interval,
            use_screener    = args.screen,
            screener_top_n  = args.top_n,
        )

    # ── screen モード（1回実行） ──────────────────────────────────
    elif args.screen:
        print("\n[Screen モード] S&P500 日中動的スクリーニング中...\n")
        screened = _screener_mod.screen_sp500_intraday(
            top_n    = args.top_n,
            verbose  = True,
            universe = PRODUCTION_UNIVERSE,
        )
        if not screened:
            print("スクリーニング結果が 0 件でした。終了します。")
            raise SystemExit(1)
        effective_tickers = [s["ticker"] for s in screened]
        run_watchlist_cycle(
            tickers         = effective_tickers,
            dry_run         = args.dry_run,
            notify_line     = args.notify_line,
            mock_mode       = args.mock,
            hybrid_mode     = args.hybrid,
            excluded_agents = args.exclude,
            run_audit       = _run_audit_flag,
            research_mode   = _research_mode,
        )

    # ── tickers モード（複数銘柄固定ウォッチリスト） ──────────────
    elif args.tickers:
        run_watchlist_cycle(
            tickers         = args.tickers,
            dry_run         = args.dry_run,
            notify_line     = args.notify_line,
            mock_mode       = args.mock,
            hybrid_mode     = args.hybrid,
            excluded_agents = args.exclude,
            run_audit       = _run_audit_flag,
            research_mode   = _research_mode,
        )

    # ── 単一銘柄モード（従来動作） ────────────────────────────────
    else:
        if args.ticker is None:
            parser.error(
                "銘柄が指定されていません。\n"
                "  単一銘柄: --ticker MSFT\n"
                "  複数銘柄: --tickers MSFT NVDA\n"
                "  スクリーニング: --screen"
            )
        run_trade_cycle(
            ticker          = args.ticker,
            dry_run         = args.dry_run,
            notify_line     = args.notify_line,
            mock_mode       = args.mock,
            hybrid_mode     = args.hybrid,
            excluded_agents = args.exclude,
            run_audit       = _run_audit_flag,
            research_mode   = _research_mode,
        )
