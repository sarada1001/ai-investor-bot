"""
run_backtest.py — 過去シミュレーション評価スクリプト

既存の main.py・エージェント群とは完全に独立したスタンドアロンスクリプト。
過去の特定日付でAIが判断を下したと仮定し、その後45暦日（約30営業日）の
実際の株価推移から損益を検証する。
"""

from __future__ import annotations

import sys
import datetime
import warnings
import yfinance as yf

warnings.filterwarnings("ignore")

# ─── 表示ヘルパー ─────────────────────────────────────────────── #

_W = 62


def _log(msg: str) -> None:
    print(f"│  {msg}")


def _sep() -> None:
    print(f"│  {'┄' * (_W - 4)}")


def _stage_header(title: str) -> None:
    print(f"\n{'━' * (_W + 2)}")
    print(f"  ◆ {title}")
    print(f"{'━' * (_W + 2)}")


def _box(lines: list[str]) -> None:
    print(f"\n╔{'═' * _W}╗")
    for line in lines:
        print(f"║  {line}".ljust(_W + 1) + "║")
    print(f"╚{'═' * _W}╝")


# ─── メイン関数 ──────────────────────────────────────────────── #

def run_historical_simulation(
    ticker: str,
    target_date: str,
    ai_judgment: str,
) -> dict:
    """
    過去シミュレーションを実行して損益を計算する。

    Args:
        ticker:      銘柄シンボル（例: "AAPL"）
        target_date: シミュレーション基準日（例: "2023-05-04"）
        ai_judgment: AIの擬似判断（例: "STRONG BUY"）

    Returns:
        {
            "ticker", "entry_date", "entry_price",
            "exit_date", "exit_price", "pnl_pct", "win"
        }
    """
    judgment_icon = "🚀" if "BUY" in ai_judgment.upper() else "📉"

    # ── ヘッダー表示 ───────────────────────────────────────────
    _box([
        f"  バックテスト: 過去シミュレーション  [{ticker}]",
        f"{'─' * (_W - 2)}",
        f"  ターゲット日 : {target_date}",
        f"  AI 判断     : {judgment_icon} {ai_judgment}",
        f"  保有期間     : 45暦日（約30営業日）",
    ])

    # ── 日付計算 ───────────────────────────────────────────────
    start_dt  = datetime.date.fromisoformat(target_date)
    end_dt    = start_dt + datetime.timedelta(days=45)
    fetch_end = end_dt   + datetime.timedelta(days=5)   # バッファ（週末・祝日対策）

    _stage_header(
        f"データ取得: {start_dt.isoformat()} → {end_dt.isoformat()}  (45暦日)"
    )

    # ── yfinance でデータ取得 ──────────────────────────────────
    try:
        ticker_obj = yf.Ticker(ticker)
        df = ticker_obj.history(
            start=start_dt.isoformat(),
            end=fetch_end.isoformat(),
        )
    except Exception as e:
        print(f"\n[ERROR] データ取得に失敗しました: {e}", file=sys.stderr)
        sys.exit(1)

    if df.empty:
        print(f"\n[ERROR] {ticker} のデータが取得できませんでした。", file=sys.stderr)
        sys.exit(1)

    # タイムゾーン除去（比較のため）
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    trading_days = len(df)
    _log(f"取得完了: {trading_days} 営業日分のデータ")
    _sep()

    # ── エントリー価格（target_date 以降の最初の営業日）────────
    entry_df = df[df.index.date >= start_dt]
    if entry_df.empty:
        print(
            f"\n[ERROR] {target_date} 以降の営業日データが見つかりません。",
            file=sys.stderr,
        )
        sys.exit(1)

    entry_row   = entry_df.iloc[0]
    entry_date  = entry_df.index[0].date()
    entry_price = float(entry_row["Close"])

    # ── エグジット価格（end_dt 以前の最後の営業日）──────────────
    exit_df = df[df.index.date <= end_dt]
    if exit_df.empty:
        # end_dt がまだ来ていない場合は取得済み最新日を使用
        exit_df = df

    exit_row   = exit_df.iloc[-1]
    exit_date  = exit_df.index[-1].date()
    exit_price = float(exit_row["Close"])

    # ── 損益計算 ───────────────────────────────────────────────
    pnl_pct = (exit_price - entry_price) / entry_price * 100
    win     = pnl_pct > 0.0

    pnl_sign  = "+" if pnl_pct >= 0 else ""
    pnl_arrow = "📈" if win else "📉"

    _log(f"エントリー : {entry_date}  終値 ${entry_price:.2f}")
    _log(f"エグジット : {exit_date}   終値 ${exit_price:.2f}")
    _log(f"保有期間   : {(exit_date - entry_date).days} 暦日 / {len(exit_df)} 営業日")
    _sep()
    _log(f"損益率     : {pnl_arrow} {pnl_sign}{pnl_pct:.2f}%")

    # ── 結果ボックス ───────────────────────────────────────────
    if win:
        verdict_line = "🏆 勝利  (AIの判断は正しかった！)"
        verdict_note = f"   {ai_judgment} → {pnl_sign}{pnl_pct:.2f}% の利益を確認"
    else:
        verdict_line = "💀 敗北  (AIの判断は間違いだった...)"
        verdict_note = f"   {ai_judgment} → {pnl_sign}{pnl_pct:.2f}% の損失"

    _box([
        f"{'─' * (_W - 2)}",
        "  シミュレーション結果",
        f"{'─' * (_W - 2)}",
        f"  銘柄         : {ticker}",
        f"  AI 判断      : {judgment_icon} {ai_judgment}",
        f"  エントリー   : {entry_date}  ${entry_price:.2f}",
        f"  エグジット   : {exit_date}  ${exit_price:.2f}  (+45暦日)",
        f"  損益率       : {pnl_sign}{pnl_pct:.2f}%",
        f"{'─' * (_W - 2)}",
        f"  判定         : {verdict_line}",
        f"{verdict_note}",
        f"{'─' * (_W - 2)}",
    ])

    return {
        "ticker":       ticker,
        "entry_date":   str(entry_date),
        "entry_price":  entry_price,
        "exit_date":    str(exit_date),
        "exit_price":   exit_price,
        "pnl_pct":      round(pnl_pct, 4),
        "win":          win,
    }


# ─── エントリポイント ─────────────────────────────────────────── #

if __name__ == "__main__":
    # AAPL: 2023-05-04（Q2 FY2023 好決算発表翌日）
    run_historical_simulation(
        ticker="AAPL",
        target_date="2023-05-04",
        ai_judgment="STRONG BUY",
    )
