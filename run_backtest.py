"""
run_backtest.py — STRONG BUY 全件バッチ検証

training_data.jsonl から STRONG BUY 判定を全件読み込み、
実際の株価推移に対してバッチ検証する。

Usage:
    python run_backtest.py                  # 全 STRONG BUY を検証（コンソール表示）
    python run_backtest.py --days 30        # 保有期間を変更（デフォルト: 45暦日）
    python run_backtest.py --backfill       # 45日経過済み分を outcome_label に自動書き戻し
    python run_backtest.py --csv out.csv    # 結果を CSV エクスポート
    python run_backtest.py --mode hybrid    # hybrid 実行分のみに絞り込み
"""

from __future__ import annotations

import argparse
import csv
import datetime
import importlib.util
import json
import sys
import warnings
from pathlib import Path

import yfinance as yf

warnings.filterwarnings("ignore")

# ── パス定数 ────────────────────────────────────────────────────────────────
PROJECT_ROOT   = Path(__file__).parent
TRAINING_FILE  = PROJECT_ROOT / "data" / "training" / "training_data.jsonl"

_W = 70  # 表示幅


# ============================================================================
# 表示ヘルパー
# ============================================================================

def _hr(char: str = "─") -> None:
    print(char * _W)


def _header(title: str) -> None:
    print()
    print("═" * _W)
    print(f"  {title}")
    print("═" * _W)


def _bar(value: float, max_val: float, width: int = 20, char: str = "█") -> str:
    if max_val <= 0:
        return "░" * width
    filled = int(round(min(abs(value) / max_val, 1.0) * width))
    return char * filled + "░" * (width - filled)


# ============================================================================
# データロード
# ============================================================================

def load_strong_buy_records(mode: str = "all") -> list[dict]:
    """training_data.jsonl から STRONG BUY レコードを抽出する。"""
    if not TRAINING_FILE.exists():
        print(f"[ERROR] {TRAINING_FILE} が見つかりません。")
        sys.exit(1)

    records = []
    with open(TRAINING_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            mo = r.get("manager_output") or {}
            if mo.get("decision") != "STRONG BUY":
                continue
            if mode == "hybrid" and not r.get("hybrid_mode"):
                continue
            if mode == "real" and (r.get("mock_mode") or r.get("hybrid_mode")):
                continue
            records.append(r)
    return records


# ============================================================================
# 株価取得・損益計算
# ============================================================================

def fetch_pnl(ticker: str, entry_date_str: str, hold_days: int) -> dict:
    """
    entry_date から hold_days 後（または今日）までの損益を yfinance で計算する。

    Returns:
        {
            "entry_date", "exit_date", "entry_price", "exit_price",
            "pnl_pct", "win", "status"
        }
        status: "completed" | "in_progress" | "error"
    """
    try:
        entry_date = datetime.date.fromisoformat(entry_date_str)
    except ValueError:
        return {"status": "error", "error": f"invalid date: {entry_date_str}"}

    today      = datetime.date.today()
    target_end = entry_date + datetime.timedelta(days=hold_days)
    fetch_end  = min(target_end, today) + datetime.timedelta(days=5)
    status     = "completed" if today >= target_end else "in_progress"

    try:
        df = yf.Ticker(ticker).history(
            start=entry_date.isoformat(),
            end=fetch_end.isoformat(),
        )
    except Exception as e:
        return {"status": "error", "error": str(e)}

    if df.empty:
        return {"status": "error", "error": "no data"}

    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    # エントリー価格: entry_date 以降の最初の営業日
    entry_df = df[df.index.date >= entry_date]
    if entry_df.empty:
        return {"status": "error", "error": "no entry data"}
    entry_price = float(entry_df.iloc[0]["Close"])
    actual_entry_date = entry_df.index[0].date()

    # エグジット価格: target_end 以前の最後の営業日（in_progress なら今日まで）
    exit_cutoff = target_end if status == "completed" else today
    exit_df = df[df.index.date <= exit_cutoff]
    if exit_df.empty:
        exit_df = df
    exit_price = float(exit_df.iloc[-1]["Close"])
    actual_exit_date = exit_df.index[-1].date()

    pnl_pct = (exit_price - entry_price) / entry_price * 100.0

    return {
        "status":      status,
        "entry_date":  str(actual_entry_date),
        "exit_date":   str(actual_exit_date),
        "entry_price": round(entry_price, 4),
        "exit_price":  round(exit_price, 4),
        "pnl_pct":     round(pnl_pct, 4),
        "win":         pnl_pct >= 0,
    }


# ============================================================================
# バッチ検証
# ============================================================================

def run_batch(records: list[dict], hold_days: int) -> list[dict]:
    """全 STRONG BUY レコードに対して損益を計算し、結果リストを返す。"""
    results = []
    total = len(records)
    for i, r in enumerate(records, 1):
        ticker = r.get("ticker", "?")
        date   = r.get("date", "")
        mo     = r.get("manager_output") or {}
        score  = mo.get("score")
        cot    = r.get("manager_chain_of_thought") or {}
        sigs   = (cot.get("signals") or {})

        print(f"  [{i:2d}/{total}] {ticker:6s}  {date}  score={score or '?'} ... ", end="", flush=True)

        # 既に outcome_label が設定済み（ExitAgent 経由）なら実績値を使う
        if r.get("outcome_label") is not None:
            outcome = r.get("outcome") or {}
            result = {
                "status":      "completed",
                "entry_date":  date,
                "exit_date":   outcome.get("exit_date", ""),
                "entry_price": None,
                "exit_price":  outcome.get("exit_price"),
                "pnl_pct":     outcome.get("pnl_pct"),
                "win":         (outcome.get("pnl_pct") or 0) >= 0,
                "source":      "exit_agent",
            }
        else:
            result = fetch_pnl(ticker, date, hold_days)
            result["source"] = "yfinance"

        pnl_str = f"{result.get('pnl_pct', 0):+.2f}%" if result.get("pnl_pct") is not None else "error"
        icon    = "✅" if result.get("win") else ("❌" if result.get("pnl_pct") is not None else "⚠️")
        prog    = "📊" if result.get("status") == "in_progress" else ""
        print(f"{icon} {pnl_str} {prog}")

        results.append({
            "record_id":    r.get("record_id"),
            "ticker":       ticker,
            "date":         date,
            "score":        score,
            "sig_fa":       sigs.get("fundamental"),
            "sig_tech":     sigs.get("technical"),
            "sig_macro":    sigs.get("macro"),
            "sig_news":     sigs.get("news"),
            "sig_social":   sigs.get("social"),
            "hybrid_mode":  r.get("hybrid_mode"),
            "outcome_label": r.get("outcome_label"),
            **result,
        })

    return results


# ============================================================================
# レポート表示
# ============================================================================

def print_report(results: list[dict], hold_days: int) -> None:
    completed   = [r for r in results if r.get("pnl_pct") is not None]
    in_progress = [r for r in results if r.get("status") == "in_progress" and r.get("pnl_pct") is None]
    errors      = [r for r in results if r.get("status") == "error"]
    wins        = [r for r in completed if r.get("win")]
    losses      = [r for r in completed if not r.get("win")]

    _header(f"バックテスト結果  STRONG BUY {len(results)}件  (保有期間: {hold_days}暦日)")

    # ── 総合サマリー ──────────────────────────────────────────────
    print(f"\n  総 STRONG BUY 件数 : {len(results):>4} 件")
    print(f"  損益確定           : {len(completed):>4} 件")
    print(f"    ✅ WIN           : {len(wins):>4} 件")
    print(f"    ❌ LOSS          : {len(losses):>4} 件")
    if in_progress:
        print(f"    📊 保有中        : {len(in_progress):>4} 件")
    if errors:
        print(f"    ⚠️  データ取得失敗: {len(errors):>4} 件")
    _hr()

    if completed:
        win_rate = len(wins) / len(completed) * 100
        pnls     = [r["pnl_pct"] for r in completed]
        avg_pnl  = sum(pnls) / len(pnls)
        max_pnl  = max(pnls)
        min_pnl  = min(pnls)
        print(f"  勝率               : {win_rate:>6.1f}%  ({len(wins)}/{len(completed)})")
        print(f"  平均 P&L           : {avg_pnl:>+7.2f}%")
        print(f"  最大利益           : {max_pnl:>+7.2f}%")
        print(f"  最大損失           : {min_pnl:>+7.2f}%")
    _hr()

    # ── 銘柄別集計 ────────────────────────────────────────────────
    _header("銘柄別集計")
    tickers = sorted(set(r["ticker"] for r in results))
    print(f"\n  {'銘柄':8s} {'件数':>4}  {'WIN':>4}  {'勝率':>6}  {'平均P&L':>8}  グラフ")
    _hr("─")
    for tkr in tickers:
        sub  = [r for r in results if r["ticker"] == tkr]
        comp = [r for r in sub if r.get("pnl_pct") is not None]
        w    = [r for r in comp if r.get("win")]
        wr   = len(w) / len(comp) * 100 if comp else float("nan")
        ap   = sum(r["pnl_pct"] for r in comp) / len(comp) if comp else float("nan")
        bar  = _bar(ap if ap == ap else 0, 10.0, width=15)
        wr_s = f"{wr:.0f}%" if wr == wr else "N/A"
        ap_s = f"{ap:+.2f}%" if ap == ap else "N/A"
        print(f"  {tkr:8s} {len(sub):>4}  {len(w):>4}  {wr_s:>6}  {ap_s:>8}  │{bar}│")
    _hr("─")

    # ── シグナル相関 ──────────────────────────────────────────────
    sig_cols = {
        "sig_fa":    "ファンダメンタル",
        "sig_tech":  "テクニカル",
        "sig_macro": "マクロ",
        "sig_news":  "ニュース",
        "sig_social":"SNS",
        "score":     "加重スコア",
    }
    comp_with_sig = [r for r in completed if r.get("sig_fa") is not None or r.get("score") is not None]
    if len(comp_with_sig) >= 2:
        _header("シグナル × P&L 相関（WIN vs LOSS 平均値比較）")
        print(f"\n  {'シグナル':20s} {'WIN平均':>9}  {'LOSS平均':>9}  {'差分':>8}")
        _hr("─")
        w_comp  = [r for r in comp_with_sig if r.get("win")]
        l_comp  = [r for r in comp_with_sig if not r.get("win")]
        for col, label in sig_cols.items():
            w_vals = [r[col] for r in w_comp  if r.get(col) is not None]
            l_vals = [r[col] for r in l_comp  if r.get(col) is not None]
            if not w_vals and not l_vals:
                continue
            w_avg = sum(w_vals) / len(w_vals) if w_vals else float("nan")
            l_avg = sum(l_vals) / len(l_vals) if l_vals else float("nan")
            diff  = (w_avg - l_avg) if (w_avg == w_avg and l_avg == l_avg) else float("nan")
            w_s   = f"{w_avg:+.3f}" if w_avg == w_avg else "  N/A "
            l_s   = f"{l_avg:+.3f}" if l_avg == l_avg else "  N/A "
            d_s   = f"{diff:+.3f}" if diff == diff else "  N/A "
            print(f"  {label:20s} {w_s:>9}  {l_s:>9}  {d_s:>8}")
        _hr("─")

    # ── 個別結果一覧 ─────────────────────────────────────────────
    _header("個別結果一覧")
    print(f"\n  {'日付':12s} {'銘柄':6s} {'スコア':>7}  {'P&L':>8}  {'状態':12s}  {'出典'}")
    _hr("─")
    for r in sorted(results, key=lambda x: (x["date"], x["ticker"])):
        pnl_s  = f"{r['pnl_pct']:+.2f}%" if r.get("pnl_pct") is not None else "  N/A"
        icon   = "✅" if r.get("win") else ("❌" if r.get("pnl_pct") is not None else "📊")
        score_s = f"{r['score']:.4f}" if r.get("score") else "   N/A"
        prog   = "(保有中)" if r.get("status") == "in_progress" else ""
        src    = r.get("source", "")
        print(f"  {r['date']:12s} {r['ticker']:6s} {score_s:>7}  {icon}{pnl_s:>8}  {prog:12s}  {src}")
    _hr("─")


# ============================================================================
# CSV エクスポート
# ============================================================================

def export_csv(results: list[dict], path: Path) -> None:
    fields = [
        "record_id", "ticker", "date", "score",
        "sig_fa", "sig_tech", "sig_macro", "sig_news", "sig_social",
        "hybrid_mode", "status", "entry_date", "exit_date",
        "entry_price", "exit_price", "pnl_pct", "win", "source",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"\n  [CSV] {path} に出力しました ({len(results)} 件)")


# ============================================================================
# バックフィル（45日経過済み → outcome_label 自動更新）
# ============================================================================

def backfill_outcomes(results: list[dict]) -> int:
    """status=completed かつ outcome_label 未設定のレコードを自動更新する。"""
    spec = importlib.util.spec_from_file_location(
        "training_data_collector",
        PROJECT_ROOT / "skills" / "training_data_collector.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    updated_total = 0
    for r in results:
        if r.get("status") != "completed":
            continue
        if r.get("outcome_label") is not None:
            continue
        if r.get("pnl_pct") is None:
            continue
        n = mod.update_outcome(
            ticker     = r["ticker"],
            pnl_pct    = r["pnl_pct"],
            exit_price = r.get("exit_price") or 0.0,
            exit_reason= "BACKTEST_BACKFILL",
        )
        if n:
            updated_total += n
            print(f"  [backfill] {r['ticker']} {r['date']}  {r['pnl_pct']:+.2f}% → {'WIN' if r['win'] else 'LOSS'}")
    return updated_total


# ============================================================================
# エントリポイント
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="STRONG BUY 全件バッチ検証",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--days",     type=int,  default=45,    help="保有期間（暦日）")
    parser.add_argument("--mode",     choices=["all", "hybrid", "real"], default="all",
                        help="対象レコードの絞り込み (all=全件, hybrid=ハイブリッドのみ, real=リアルのみ)")
    parser.add_argument("--backfill", action="store_true",
                        help="45日経過済み分を training_data.jsonl に outcome_label として書き戻す")
    parser.add_argument("--csv",      type=Path, default=None,  help="結果を CSV に出力するファイルパス")
    args = parser.parse_args()

    print()
    print("═" * _W)
    print("  ECC バックテスト — STRONG BUY 全件バッチ検証")
    print(f"  保有期間: {args.days}暦日  /  絞り込み: {args.mode}")
    print("═" * _W)

    records = load_strong_buy_records(mode=args.mode)
    if not records:
        print("\n  対象レコードが見つかりませんでした。")
        sys.exit(0)

    print(f"\n  {len(records)} 件の STRONG BUY レコードを検証します...\n")
    results = run_batch(records, hold_days=args.days)

    print_report(results, hold_days=args.days)

    if args.csv:
        export_csv(results, args.csv)

    if args.backfill:
        _header("バックフィル — outcome_label 自動書き戻し")
        n = backfill_outcomes(results)
        if n:
            print(f"\n  合計 {n} 件を更新しました。")
        else:
            print("\n  バックフィル対象なし（全件 in_progress または設定済み）。")

    print()
    print("═" * _W)
    print("  完了。")
    print("═" * _W)
    print()


if __name__ == "__main__":
    main()
