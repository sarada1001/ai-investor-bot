"""monitor.py — ECC リアルタイム Rich ターミナル TUI

SSH / tmux 環境での運用を前提としたターミナルダッシュボード。
Rich ライブラリを使い、ポートフォリオ P&L・エージェント健全性・
スクリーナー結果・最新トレード判断を 1 画面で確認できる。

起動:
    python monitor.py              # デフォルト 30 秒更新
    python monitor.py --interval 60
    python monitor.py --once       # 1 回表示して終了
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

# ──────────────────────────────────────────────
# パス定数
# ──────────────────────────────────────────────

_PORTFOLIO_PATH    = Path("data/portfolio.json")
_AGENT_STATUS_PATH = Path("data/agent_status.json")
_INTRADAY_CACHE    = Path("data/screener/intraday_cache.json")
_TRAINING_DATA     = Path("data/training/training_data.jsonl")
_AGENT_STALE_MIN   = 30


# ──────────────────────────────────────────────
# データ読み込み
# ──────────────────────────────────────────────

def _load_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _fetch_prices(tickers: list[str]) -> dict[str, float]:
    if not tickers or not _YF_AVAILABLE:
        return {}
    try:
        raw = yf.download(tickers, period="1d", progress=False, auto_adjust=True)
        close = raw["Close"] if "Close" in raw.columns else raw
        prices: dict[str, float] = {}
        if hasattr(close, "columns"):
            for t in tickers:
                if t in close.columns:
                    v = close[t].dropna()
                    if not v.empty:
                        prices[t] = float(v.iloc[-1])
        else:
            v = close.dropna()
            if not v.empty and tickers:
                prices[tickers[0]] = float(v.iloc[-1])
        return prices
    except Exception:
        return {}


def _last_n_training(n: int = 5) -> list[dict]:
    if not _TRAINING_DATA.exists():
        return []
    try:
        lines = _TRAINING_DATA.read_text(encoding="utf-8").splitlines()
        records = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
                if len(records) >= n:
                    break
            except json.JSONDecodeError:
                continue
        return records
    except Exception:
        return []


# ──────────────────────────────────────────────
# パネルビルダー
# ──────────────────────────────────────────────

def _portfolio_panel() -> Panel:
    data     = _load_json(_PORTFOLIO_PATH)
    if not isinstance(data, dict):
        return Panel(Text("データなし", style="dim"), title="💼 ポートフォリオ P&L", border_style="blue")

    positions = [p for p in data.get("positions", []) if p.get("status") == "OPEN"]

    if not positions:
        return Panel(
            Text("OPEN ポジションなし", style="dim"),
            title="💼 ポートフォリオ P&L",
            border_style="blue",
        )

    tickers       = [p["ticker"] for p in positions]
    current_prices = _fetch_prices(tickers)

    tbl = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan",
                expand=True)
    tbl.add_column("銘柄",   style="bold")
    tbl.add_column("エントリ", justify="right")
    tbl.add_column("現在値",   justify="right")
    tbl.add_column("株数",     justify="right")
    tbl.add_column("含み損益", justify="right")
    tbl.add_column("損益%",    justify="right")
    tbl.add_column("目標値",   justify="right")
    tbl.add_column("損切値",   justify="right")

    total_pnl  = 0.0
    total_cost = 0.0
    for pos in positions:
        ticker   = pos["ticker"]
        entry    = float(pos.get("entry_price", 0))
        shares   = int(pos.get("shares", 0))
        current  = current_prices.get(ticker, entry)
        pnl      = (current - entry) * shares
        pnl_pct  = (current - entry) / entry * 100 if entry else 0.0
        total_pnl  += pnl
        total_cost += entry * shares

        pnl_style = "green" if pnl >= 0 else "red"
        tbl.add_row(
            ticker,
            f"${entry:.2f}",
            f"${current:.2f}",
            str(shares),
            Text(f"${pnl:+,.0f}", style=pnl_style),
            Text(f"{pnl_pct:+.2f}%", style=pnl_style),
            f"${pos['target_price']:.2f}"   if pos.get("target_price") else "—",
            f"${pos['stop_loss_price']:.2f}" if pos.get("stop_loss_price") else "—",
        )

    total_pct = total_pnl / total_cost * 100 if total_cost else 0.0
    pnl_style = "green" if total_pnl >= 0 else "red"
    subtitle   = Text(f"合計含み損益: ", style="dim")
    subtitle.append(f"${total_pnl:+,.0f}  ({total_pct:+.2f}%)", style=pnl_style)

    return Panel(
        Columns([tbl, subtitle], expand=True),
        title=f"💼 ポートフォリオ P&L  [{len(positions)} 件]",
        border_style="blue",
    )


def _agent_health_panel() -> Panel:
    status_data = _load_json(_AGENT_STATUS_PATH)
    if not isinstance(status_data, dict) or not status_data:
        return Panel(Text("データなし", style="dim"), title="🤖 エージェント健全性", border_style="magenta")

    now_utc = datetime.now(timezone.utc)
    tbl = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold magenta",
                expand=True)
    tbl.add_column("エージェント", style="bold")
    tbl.add_column("ステータス",   justify="center")
    tbl.add_column("勝率",         justify="right")
    tbl.add_column("取引数",       justify="right")
    tbl.add_column("最終評価",     justify="right")

    for agent_name, info in status_data.items():
        status    = info.get("status", "UNKNOWN")
        win_rate  = info.get("win_rate")
        total_tr  = info.get("total_trades", 0)
        last_eval = info.get("last_evaluated", "")

        is_stale = False
        last_str = last_eval[:16] if last_eval else "—"
        if last_eval:
            try:
                le_dt = datetime.fromisoformat(last_eval)
                if le_dt.tzinfo is None:
                    le_dt = le_dt.replace(tzinfo=timezone.utc)
                is_stale = (now_utc - le_dt).total_seconds() / 60 > _AGENT_STALE_MIN
            except Exception:
                is_stale = True

        if status == "SUSPENDED":
            badge = Text("⛔ SUSPENDED", style="red bold")
        elif is_stale:
            badge = Text("⚠️  STALE", style="yellow bold")
        else:
            badge = Text("✅ ACTIVE", style="green bold")

        wr_text = f"{win_rate:.1%}" if win_rate is not None else "N/A"
        tbl.add_row(
            agent_name.replace("Agent", ""),
            badge,
            wr_text,
            str(total_tr),
            last_str,
        )

    return Panel(tbl, title="🤖 エージェント健全性", border_style="magenta")


def _screener_panel() -> Panel:
    if not _INTRADAY_CACHE.exists():
        return Panel(Text("スクリーナーキャッシュなし", style="dim"),
                     title="📡 スクリーナー（最新キャッシュ）", border_style="yellow")

    data    = _load_json(_INTRADAY_CACHE)
    results = data.get("results", []) if isinstance(data, dict) else []
    key     = data.get("session_key", "—") if isinstance(data, dict) else "—"

    tbl = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow",
                expand=True)
    tbl.add_column("#",      justify="right", style="dim")
    tbl.add_column("銘柄",   style="bold")
    tbl.add_column("Score",  justify="right")
    tbl.add_column("RSI",    justify="right")
    tbl.add_column("Momentum", justify="right")
    tbl.add_column("VolSpike", justify="right")

    for i, r in enumerate(results[:8], 1):
        mom     = r.get("intraday_momentum_pct", 0.0)
        m_style = "green" if mom >= 0 else "red"
        tbl.add_row(
            str(i),
            r.get("ticker", "—"),
            str(r.get("score", 0)),
            f"{r.get('rsi', 0):.1f}",
            Text(f"{mom:+.2f}%", style=m_style),
            f"{r.get('intraday_vol_spike', 1.0):.1f}x",
        )

    return Panel(tbl, title=f"📡 スクリーナー  [{key}]", border_style="yellow")


def _decisions_panel() -> Panel:
    records = _last_n_training(5)
    if not records:
        return Panel(Text("取引履歴なし", style="dim"),
                     title="📋 最新トレード判断", border_style="cyan")

    tbl = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan",
                expand=True)
    tbl.add_column("日付",     style="dim")
    tbl.add_column("銘柄",     style="bold")
    tbl.add_column("判断",     justify="center")
    tbl.add_column("スコア",   justify="right")
    tbl.add_column("根拠（要約）")

    for rec in records:
        cot      = rec.get("manager_chain_of_thought") or {}
        out      = rec.get("manager_output") or {}
        decision = out.get("decision", "HOLD")
        score    = float(cot.get("weighted_score") or out.get("score") or 0.0)
        rationale = str(cot.get("rationale", ""))[:40]
        date      = str(rec.get("date", ""))[:10]

        if decision == "STRONG BUY":
            d_style = "green bold"
            d_icon  = "🚀"
        elif decision == "SELL":
            d_style = "red bold"
            d_icon  = "📉"
        else:
            d_style = "blue"
            d_icon  = "⏸"

        tbl.add_row(
            date,
            rec.get("ticker", "—"),
            Text(f"{d_icon} {decision}", style=d_style),
            f"{score:+.3f}",
            rationale,
        )

    return Panel(tbl, title="📋 最新トレード判断", border_style="cyan")


# ──────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────

def _build_screen(interval: int) -> Columns:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header  = Text(
        f"🤖 ECC Investor Monitor  |  {now_str}  |  更新間隔: {interval}s",
        style="bold white on dark_blue",
        justify="center",
    )

    portfolio = _portfolio_panel()
    agents    = _agent_health_panel()
    screener  = _screener_panel()
    decisions = _decisions_panel()

    top_row    = Columns([portfolio, agents], expand=True)
    bottom_row = Columns([screener, decisions], expand=True)

    from rich.console import Group  # noqa: PLC0415
    return Group(
        Panel(header, border_style="dark_blue", padding=(0, 1)),
        top_row,
        bottom_row,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ECC リアルタイム Rich ターミナル TUI"
    )
    parser.add_argument("--interval", type=int, default=30,
                        help="画面更新間隔（秒）。デフォルト 30。")
    parser.add_argument("--once", action="store_true",
                        help="1 回表示して終了する。")
    args = parser.parse_args()

    console = Console()

    if args.once:
        console.print(_build_screen(args.interval))
        return

    try:
        with Live(
            _build_screen(args.interval),
            console=console,
            refresh_per_second=1,
            screen=True,
        ) as live:
            import time
            while True:
                time.sleep(args.interval)
                live.update(_build_screen(args.interval))
    except KeyboardInterrupt:
        console.print("\n[dim]モニター停止[/dim]")
        sys.exit(0)


if __name__ == "__main__":
    main()
