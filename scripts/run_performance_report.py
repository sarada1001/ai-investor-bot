#!/usr/bin/env python3
"""
scripts/run_performance_report.py — ペーパートレード パフォーマンスレポート

obsidian_logs の SELL ログと agent_status.json から以下を算出して stdout に出力:
  - 取引数 / 勝率 / 平均損益
  - シャープレシオ（簡易計算）
  - 最大ドローダウン
  - エージェント別勝率
  - data収集進捗 (training_data.jsonl 件数)

Usage:
    python scripts/run_performance_report.py          # 通常実行
    python scripts/run_performance_report.py --json   # JSON形式で出力
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR    = Path(__file__).parent
PROJECT_ROOT  = SCRIPT_DIR.parent
OBSIDIAN_DIR  = PROJECT_ROOT / "data" / "knowledge_base" / "obsidian_logs"
AGENT_STATUS  = PROJECT_ROOT / "data" / "agent_status.json"
TRAINING_DATA = PROJECT_ROOT / "data" / "training" / "training_data.jsonl"
PORTFOLIO     = PROJECT_ROOT / "data" / "portfolio.json"


# ─────────────────────────────────────────────────────────────
# データ収集
# ─────────────────────────────────────────────────────────────

def _parse_sell_logs() -> list[dict]:
    """SELL ログを解析して損益リストを返す。"""
    results = []
    for path in sorted(OBSIDIAN_DIR.glob("Log_*_SELL*.md")):
        text = path.read_text(encoding="utf-8")
        # YAML frontmatter から profit_loss を抽出
        m = re.search(r"profit_loss:\s*([+-]?\+?[-]?\d+\.?\d*)%", text)
        if not m:
            continue
        raw = m.group(1).lstrip("+")
        try:
            pnl = float(raw)
        except ValueError:
            continue
        date_m = re.search(r"date:\s*(\d{4}-\d{2}-\d{2})", text)
        ticker_m = re.search(r"ticker:\s*(\w+)", text)
        results.append({
            "file":   path.name,
            "ticker": ticker_m.group(1) if ticker_m else "?",
            "date":   date_m.group(1) if date_m else "unknown",
            "pnl":    pnl,
            "win":    pnl > 0,
        })
    return results


def _load_agent_status() -> dict:
    if not AGENT_STATUS.exists():
        return {}
    try:
        with AGENT_STATUS.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _training_count() -> int:
    if not TRAINING_DATA.exists():
        return 0
    return sum(1 for line in TRAINING_DATA.open(encoding="utf-8") if line.strip())


def _open_positions() -> list[dict]:
    if not PORTFOLIO.exists():
        return []
    try:
        with PORTFOLIO.open(encoding="utf-8") as f:
            data = json.load(f)
        return [p for p in data.get("positions", []) if p.get("status", "OPEN") == "OPEN"]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────
# 統計計算
# ─────────────────────────────────────────────────────────────

def _compute_stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "n": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "avg_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "max_drawdown": 0.0, "sharpe": 0.0,
        }

    pnls   = [t["pnl"] for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    avg   = sum(pnls) / n
    std   = math.sqrt(sum((p - avg) ** 2 for p in pnls) / n) if n > 1 else 0.0
    sharpe = (avg / std * math.sqrt(252)) if std > 0 else 0.0  # 年率換算(簡易)

    # 最大ドローダウン（複利ベースの純資産曲線から計算）
    equity = 1.0
    peak   = 1.0
    max_dd = 0.0
    for p in pnls:
        equity *= (1 + p / 100)
        peak    = max(peak, equity)
        dd      = (peak - equity) / peak
        max_dd  = max(max_dd, dd)

    return {
        "n":           n,
        "wins":        len(wins),
        "losses":      len(losses),
        "win_rate":    len(wins) / n,
        "avg_pnl":     avg,
        "avg_win":     sum(wins)   / len(wins)   if wins   else 0.0,
        "avg_loss":    sum(losses) / len(losses) if losses else 0.0,
        "max_drawdown": -max_dd,
        "sharpe":      sharpe,
    }


# ─────────────────────────────────────────────────────────────
# 出力
# ─────────────────────────────────────────────────────────────

def _bar(value: float, max_val: float = 1.0, width: int = 20) -> str:
    filled = int(round(value / max_val * width)) if max_val > 0 else 0
    filled = max(0, min(filled, width))
    return "█" * filled + "░" * (width - filled)


def print_report(trades: list[dict], stats: dict, agent_status: dict,
                 training_n: int, open_pos: list[dict]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    w   = 62
    bar = "═" * w

    print(f"\n╔{bar}╗")
    print(f"║{'ECC ペーパートレード パフォーマンスレポート':^{w}}║")
    print(f"║{'生成: ' + now:^{w}}║")
    print(f"╠{bar}╣")

    # ── 基本統計 ────────────────────────────────────────────
    print(f"║  {'【取引統計】':<{w - 2}}║")
    print(f"║  {'取引数 (SELL完了)':.<40} {stats['n']:>4} 件 {'':>8}║")
    if stats["n"] > 0:
        wr_bar = _bar(stats["win_rate"])
        print(f"║  {'勝率':.<40} {stats['win_rate'] * 100:>5.1f}%  {wr_bar}║")
        print(f"║  {'勝ち / 負け':.<40} {stats['wins']:>3}勝 / {stats['losses']:>3}負 {'':>8}║")
        print(f"║  {'平均損益':.<40} {stats['avg_pnl']:>+7.2f}%         ║")
        print(f"║  {'平均利益 (勝ちトレード)':.<40} {stats['avg_win']:>+7.2f}%         ║")
        print(f"║  {'平均損失 (負けトレード)':.<40} {stats['avg_loss']:>+7.2f}%         ║")
        print(f"╠{bar}╣")
        print(f"║  {'【リスク指標】':<{w - 2}}║")
        print(f"║  {'シャープレシオ (年率簡易)':.<40} {stats['sharpe']:>+7.2f}          ║")
        print(f"║  {'最大ドローダウン':.<40} {stats['max_drawdown']:>+7.2f}%         ║")
    print(f"╠{bar}╣")

    # ── オープンポジション ────────────────────────────────────
    print(f"║  {'【保有ポジション】':.<{w - 2}}║")
    if open_pos:
        for p in open_pos:
            ticker = p.get("ticker", "?")
            shares = p.get("shares", 0)
            entry  = p.get("entry_price", 0.0)
            tp     = p.get("target_price") or "-"
            sl     = p.get("stop_loss_price") or "-"
            line   = f"  {ticker:<6} {shares:>4}株  買値${entry:.2f}  TP:{tp}  SL:{sl}"
            print(f"║{line:<{w}}║")
    else:
        print(f"║  {'保有なし':<{w - 2}}║")
    print(f"╠{bar}╣")

    # ── エージェント勝率 ─────────────────────────────────────
    print(f"║  {'【エージェント勝率】':<{w - 2}}║")
    for agent, info in agent_status.items():
        status  = info.get("status", "UNKNOWN")
        wr      = info.get("win_rate", 0.0)
        n_t     = info.get("total_trades", 0)
        icon    = "🟢" if status == "ACTIVE" else "🔴"
        wr_bar  = _bar(wr)
        line    = f"  {icon} {agent:<20} {wr * 100:>5.1f}%  ({n_t:>3}件)  {wr_bar}"
        print(f"║{line:<{w + 6}}║")
    print(f"╠{bar}╣")

    # ── データ収集進捗 ────────────────────────────────────────
    print(f"║  {'【データ収集進捗 (200件目標)】':<{w - 2}}║")
    prog_bar = _bar(training_n, max_val=200)
    print(f"║  {'training_data.jsonl':.<40} {training_n:>4} 件  {prog_bar}║")
    print(f"║  {'obsidian_logs':.<40} {len(list(OBSIDIAN_DIR.glob('*.md'))):>4} 件         ║")
    print(f"╚{bar}╝\n")


def build_json(trades: list[dict], stats: dict, agent_status: dict,
               training_n: int, open_pos: list[dict]) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trade_stats":  stats,
        "agent_status": {
            k: {"status": v.get("status"), "win_rate": v.get("win_rate"),
                "total_trades": v.get("total_trades")}
            for k, v in agent_status.items()
        },
        "open_positions":  len(open_pos),
        "training_count":  training_n,
        "sell_log_count":  len(trades),
        "target_training": 200,
        "progress_pct":    round(training_n / 200 * 100, 1),
    }


# ─────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ECC ペーパートレード パフォーマンスレポート")
    parser.add_argument("--json", action="store_true", help="JSON形式で出力")
    args = parser.parse_args()

    trades        = _parse_sell_logs()
    stats         = _compute_stats(trades)
    agent_status  = _load_agent_status()
    training_n    = _training_count()
    open_pos      = _open_positions()

    if args.json:
        print(json.dumps(build_json(trades, stats, agent_status, training_n, open_pos),
                         ensure_ascii=False, indent=2))
    else:
        print_report(trades, stats, agent_status, training_n, open_pos)


if __name__ == "__main__":
    main()
