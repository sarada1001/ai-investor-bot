#!/usr/bin/env python3
"""
evaluate_performance.py — ECC 学習データ分析 & 研究進捗レポーター

Usage:
    python evaluate_performance.py                 # 全レコード集計
    python evaluate_performance.py --mode real     # mock 除外（hybrid 含む）
    python evaluate_performance.py --mode hybrid   # ハイブリッド実行分のみ
    python evaluate_performance.py --mode mock     # モック実行分のみ
    python evaluate_performance.py --file PATH     # ファイルパス指定

出力内容:
    1. 総合サマリー（セッション数・STRONG BUY 数・勝率）
    2. 判定別 加重スコア分布
    3. 日別セッション数テキストグラフ
    4. シグナル要因別平均貢献値
    5. 研究進捗ダッシュボード
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# ── パス定数 ──────────────────────────────────────────────────────────────────
DEFAULT_JSONL = Path(__file__).parent / "data" / "training" / "training_data.jsonl"

# ── 表示定数 ─────────────────────────────────────────────────────────────────
_W = 70  # 表示幅


# ============================================================================
# ローダー
# ============================================================================

def load_records(path: Path) -> list[dict]:
    if not path.exists():
        print(f"[ERROR] ファイルが見つかりません: {path}")
        sys.exit(1)

    records = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[WARN] 行 {lineno} のパースに失敗: {e}")
    return records


def filter_records(records: list[dict], mode: str) -> list[dict]:
    if mode == "all":
        return records
    if mode == "real":
        return [r for r in records if not r.get("mock_mode", False)]
    if mode == "hybrid":
        return [r for r in records if r.get("hybrid_mode", False)]
    if mode == "mock":
        return [r for r in records if r.get("mock_mode", False)]
    raise ValueError(f"Unknown mode: {mode!r}")


# ============================================================================
# DataFrame 変換
# ============================================================================

def to_dataframe(records: list[dict]) -> pd.DataFrame:
    rows = []
    for r in records:
        mo = r.get("manager_output") or {}
        cot = r.get("manager_chain_of_thought") or {}
        sigs = cot.get("signals") or {}
        outcome = r.get("outcome") or {}

        rows.append({
            "record_id":       r.get("record_id", ""),
            "session_id":      r.get("session_id", ""),
            "date":            r.get("date", ""),
            "ticker":          r.get("ticker", ""),
            "mock_mode":       bool(r.get("mock_mode", False)),
            "hybrid_mode":     bool(r.get("hybrid_mode", False)),
            "decision":        mo.get("decision", "HOLD"),
            "score":           mo.get("score"),
            "is_strong_buy":   bool(mo.get("is_strong_buy", False)),
            "threshold":       mo.get("threshold", 0.60),
            "gate_skipped":    bool(cot.get("gate_skipped", False)),
            "macro_brake":     bool(cot.get("macro_forced_hold", False)),
            "hype_penalty":    bool(cot.get("social_hype_penalty", False)),
            "sig_fundamental": sigs.get("fundamental"),
            "sig_technical":   sigs.get("technical"),
            "sig_macro":       sigs.get("macro"),
            "sig_news":        sigs.get("news"),
            "sig_social":      sigs.get("social"),
            "outcome_label":   r.get("outcome_label"),
            "pnl_pct":         outcome.get("pnl_pct"),
            "exit_price":      outcome.get("exit_price"),
            "exit_reason":     outcome.get("exit_reason"),
            "exit_date":       outcome.get("exit_date"),
            "created_at":      r.get("created_at", ""),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    return df


# ============================================================================
# 表示ヘルパー
# ============================================================================

def _hr(char: str = "─") -> None:
    print(char * _W)


def _header(title: str, char: str = "═") -> None:
    print()
    print(char * _W)
    print(f"  {title}")
    print(char * _W)


def _kv(label: str, value, width: int = 28) -> None:
    print(f"  {label:<{width}}: {value}")


def _bar(value: float, max_val: float, width: int = 30, char: str = "█") -> str:
    if max_val <= 0:
        return "░" * width
    filled = int(round(value / max_val * width))
    return char * filled + "░" * (width - filled)


def _mode_label(mock: bool, hybrid: bool) -> str:
    if mock:
        return "mock"
    if hybrid:
        return "hybrid"
    return "real"


# ============================================================================
# セクション 1: 総合サマリー
# ============================================================================

def section_summary(df: pd.DataFrame, mode: str) -> None:
    _header("1. 総合サマリー  Summary Statistics")

    total        = len(df)
    n_strong_buy = int(df["is_strong_buy"].sum())
    n_hold       = total - n_strong_buy
    n_mock       = int(df["mock_mode"].sum())
    n_hybrid     = int(df["hybrid_mode"].sum())
    n_real       = total - n_mock - n_hybrid

    confirmed    = df[df["outcome_label"].notna()]
    n_confirmed  = len(confirmed)
    n_win        = int((confirmed["outcome_label"] == "WIN").sum())
    n_loss       = int((confirmed["outcome_label"] == "LOSS").sum())
    win_rate     = n_win / n_confirmed * 100 if n_confirmed > 0 else float("nan")

    gate_skip    = int(df["gate_skipped"].sum())
    macro_brake  = int(df["macro_brake"].sum())
    hype_pen     = int(df["hype_penalty"].sum())

    tickers = df["ticker"].nunique()
    ticker_top = (
        df["ticker"].value_counts().index[0]
        if total > 0 else "N/A"
    )

    _kv("集計モード",          mode)
    _kv("総セッション数",      f"{total:,} 件")
    _kv("  うち real 実行",    f"{n_real:,} 件")
    _kv("  うち hybrid 実行",  f"{n_hybrid:,} 件")
    _kv("  うち mock 実行",    f"{n_mock:,} 件")
    _hr()
    _kv("STRONG BUY 判定",    f"{n_strong_buy:,} 件  ({n_strong_buy/total*100:.1f}%)" if total else "N/A")
    _kv("HOLD 判定",           f"{n_hold:,} 件  ({n_hold/total*100:.1f}%)" if total else "N/A")
    _hr()
    _kv("Gate スキップ",       f"{gate_skip:,} 件  (マクロブレーキ: {macro_brake}件)")
    _kv("Hype ペナルティ",     f"{hype_pen:,} 件")
    _hr()
    _kv("アウトカム確定数",    f"{n_confirmed:,} 件")
    _kv("  WIN",               f"{n_win:,} 件")
    _kv("  LOSS",              f"{n_loss:,} 件")
    if n_confirmed > 0:
        _kv("  勝率 (Win Rate)", f"{win_rate:.1f}%  ({n_win}/{n_confirmed})")
        if n_win + n_loss == n_confirmed:
            avg_pnl = confirmed["pnl_pct"].mean()
            _kv("  平均 P&L",   f"{avg_pnl:+.2f}%")
    else:
        _kv("  勝率 (Win Rate)", "未確定 (アウトカムなし)")
    _hr()
    _kv("分析銘柄数",          f"{tickers:,} 銘柄  (最多: {ticker_top})")


# ============================================================================
# セクション 2: 判定別スコア分布
# ============================================================================

def section_score_distribution(df: pd.DataFrame) -> None:
    _header("2. 判定別 加重スコア分布  Score Distribution by Decision")

    df_score = df[df["score"].notna()].copy()
    if df_score.empty:
        print("  スコアデータなし")
        return

    for decision in ["STRONG BUY", "HOLD"]:
        sub = df_score[df_score["decision"] == decision]["score"]
        if sub.empty:
            continue
        label = "🚀 STRONG BUY" if decision == "STRONG BUY" else "⏸  HOLD"
        print(f"\n  {label}  (n={len(sub)})")
        print(f"    平均スコア  : {sub.mean():+.4f}")
        print(f"    中央値      : {sub.median():+.4f}")
        print(f"    最大 / 最小 : {sub.max():+.4f} / {sub.min():+.4f}")
        print(f"    標準偏差    : {sub.std():.4f}" if len(sub) > 1 else "    標準偏差    : N/A (n=1)")

    _hr()

    # スコア帯別ヒストグラム（テキスト）
    print("\n  スコア帯分布  (bin=0.1):")
    bins = [-1.0, -0.5, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.01]
    labels = [
        "[-1.0, -0.5)",
        "[-0.5,  0.0)",
        "[ 0.0,  0.2)",
        "[ 0.2,  0.4)",
        "[ 0.4,  0.6)",
        "[ 0.6,  0.8)",
        "[ 0.8,  1.0)",
        "[ 1.0      ]",
    ]
    cut = pd.cut(df_score["score"], bins=bins, labels=labels, right=False)
    counts = cut.value_counts().reindex(labels, fill_value=0)
    max_c = counts.max() if counts.max() > 0 else 1
    threshold = df_score["threshold"].mode()[0] if len(df_score) > 0 else 0.60

    for label, cnt in counts.items():
        gate_marker = " ← Strong Buy 閾値" if label == "[ 0.6,  0.8)" else ""
        bar = _bar(cnt, max_c, width=25)
        print(f"    {label} │{bar}│ {cnt:3d}{gate_marker}")


# ============================================================================
# セクション 3: 日別セッション数テキストグラフ
# ============================================================================

def section_daily_trend(df: pd.DataFrame) -> None:
    _header("3. 日別セッション数推移  Daily Session Trend")

    if df.empty or df["date"].isna().all():
        print("  日付データなし")
        return

    daily = (
        df.groupby("date")
        .agg(
            total=("record_id", "count"),
            strong_buy=("is_strong_buy", "sum"),
            mock=("mock_mode", "sum"),
            hybrid=("hybrid_mode", "sum"),
        )
        .reset_index()
        .sort_values("date")
    )

    max_total = daily["total"].max() if len(daily) > 0 else 1

    print(f"\n  {'日付':<12} {'セッション':>10}  グラフ (■=STRONG BUY  □=HOLD/Gate)")
    _hr("─")
    for _, row in daily.iterrows():
        date_str   = str(row["date"])
        total      = int(row["total"])
        sb         = int(row["strong_buy"])
        hold       = total - sb
        mock_n     = int(row["mock"])
        hybrid_n   = int(row["hybrid"])
        real_n     = total - mock_n - hybrid_n

        mode_note = []
        if real_n > 0:
            mode_note.append(f"real:{real_n}")
        if hybrid_n > 0:
            mode_note.append(f"hybrid:{hybrid_n}")
        if mock_n > 0:
            mode_note.append(f"mock:{mock_n}")

        bar_sb   = "■" * sb
        bar_hold = "□" * hold
        bar_str  = (bar_sb + bar_hold)[:40]

        print(
            f"  {date_str:<12} {total:>3}件  │{bar_str:<40}│"
            f"  ({', '.join(mode_note)})"
        )
    _hr("─")
    print(f"  合計: {daily['total'].sum():,} セッション / {len(daily)} 日間")

    # 週次集計（日数が7以上の場合のみ）
    if len(daily) >= 7:
        print("\n  週次集計 (Week):")
        df_w = df.copy()
        df_w["week"] = pd.to_datetime(df_w["date"].astype(str)).dt.to_period("W")
        weekly = (
            df_w.groupby("week")
            .agg(total=("record_id", "count"), strong_buy=("is_strong_buy", "sum"))
            .reset_index()
        )
        for _, row in weekly.iterrows():
            bar = _bar(int(row["total"]), weekly["total"].max(), width=20)
            print(f"    {str(row['week']):<20} │{bar}│ {int(row['total']):3d}件 (SB: {int(row['strong_buy'])})")


# ============================================================================
# セクション 4: シグナル要因別平均
# ============================================================================

def section_signal_analysis(df: pd.DataFrame) -> None:
    _header("4. シグナル要因別 平均貢献値  Average Signal Contribution")

    sig_cols = {
        "sig_fundamental": ("ファンダメンタル", 0.40),
        "sig_technical":   ("テクニカル",       0.20),
        "sig_macro":       ("マクロ環境",        0.20),
        "sig_news":        ("ニュース",          0.10),
        "sig_social":      ("SNSセンチメント",   0.10),
    }

    df_sig = df[df["decision"].notna()].copy()
    if df_sig.empty:
        print("  データなし")
        return

    print(f"\n  {'要因':<18} {'ウェイト':>8}  {'全体平均':>9}  {'STRONG BUY 平均':>16}  {'HOLD 平均':>10}")
    _hr("─")

    sb_df   = df_sig[df_sig["decision"] == "STRONG BUY"]
    hold_df = df_sig[df_sig["decision"] == "HOLD"]

    for col, (label, weight) in sig_cols.items():
        if col not in df_sig.columns or df_sig[col].isna().all():
            continue
        all_avg  = df_sig[col].mean()
        sb_avg   = sb_df[col].mean()   if not sb_df.empty   else float("nan")
        hold_avg = hold_df[col].mean() if not hold_df.empty else float("nan")

        sb_str   = f"{sb_avg:+.3f}" if pd.notna(sb_avg) else "  N/A "
        hold_str = f"{hold_avg:+.3f}" if pd.notna(hold_avg) else "  N/A "
        all_str  = f"{all_avg:+.3f}" if pd.notna(all_avg) else "  N/A "
        bar = _bar(abs(all_avg) if pd.notna(all_avg) else 0, 1.0, width=10)

        print(
            f"  {label:<18} {weight:>7.0%}  "
            f"{all_str:>9}  │{bar}│  "
            f"{sb_str:>8}  {hold_str:>10}"
        )

    # アウトカム別シグナル分析
    confirmed = df_sig[df_sig["outcome_label"].notna()]
    if len(confirmed) >= 2:
        _hr("─")
        print("\n  アウトカム別シグナル平均:")
        for label_out in ["WIN", "LOSS"]:
            sub = confirmed[confirmed["outcome_label"] == label_out]
            if sub.empty:
                continue
            icon = "✅" if label_out == "WIN" else "❌"
            parts = []
            for col, (lbl, _) in sig_cols.items():
                if col in sub.columns:
                    avg = sub[col].mean()
                    if pd.notna(avg):
                        parts.append(f"{lbl}={avg:+.2f}")
            print(f"    {icon} {label_out} (n={len(sub)}): {', '.join(parts)}")


# ============================================================================
# セクション 5: 銘柄別統計
# ============================================================================

def section_ticker_stats(df: pd.DataFrame) -> None:
    _header("5. 銘柄別 分析頻度  Ticker Statistics")

    if df.empty:
        print("  データなし")
        return

    ticker_stats = (
        df.groupby("ticker")
        .agg(
            sessions=("record_id", "count"),
            strong_buy=("is_strong_buy", "sum"),
            avg_score=("score", "mean"),
            wins=("outcome_label", lambda x: (x == "WIN").sum()),
            losses=("outcome_label", lambda x: (x == "LOSS").sum()),
        )
        .reset_index()
        .sort_values("sessions", ascending=False)
    )

    max_sessions = ticker_stats["sessions"].max() if len(ticker_stats) > 0 else 1

    print(f"\n  {'銘柄':<8} {'分析':>5} {'SB':>4} {'SB率':>6}  {'平均スコア':>10}  {'W':>4} {'L':>4}  グラフ")
    _hr("─")
    for _, row in ticker_stats.iterrows():
        ticker    = str(row["ticker"])
        sessions  = int(row["sessions"])
        sb        = int(row["strong_buy"])
        sb_pct    = sb / sessions * 100 if sessions > 0 else 0
        avg_score = row["avg_score"]
        wins      = int(row["wins"])
        losses    = int(row["losses"])
        score_str = f"{avg_score:+.3f}" if pd.notna(avg_score) else "  N/A"
        bar       = _bar(sessions, max_sessions, width=20)

        print(
            f"  {ticker:<8} {sessions:>5}  {sb:>4}  {sb_pct:>5.1f}%"
            f"  {score_str:>10}  {wins:>4} {losses:>4}  │{bar}│"
        )


# ============================================================================
# セクション 6: 研究進捗ダッシュボード
# ============================================================================

def section_research_progress(df: pd.DataFrame) -> None:
    _header("6. 研究進捗ダッシュボード  Research Progress Dashboard")

    total       = len(df)
    n_real      = int((~df["mock_mode"]).sum())
    n_hybrid    = int(df["hybrid_mode"].sum())
    n_confirmed = int(df["outcome_label"].notna().sum())

    # 目標値（論文執筆に必要な最低ライン）
    GOAL_SESSIONS    = 100
    GOAL_CONFIRMED   = 30
    GOAL_REAL        = 50

    def _progress_bar(current: int, goal: int, width: int = 20) -> str:
        pct = min(current / goal, 1.0) if goal > 0 else 0
        filled = int(pct * width)
        return "█" * filled + "░" * (width - filled)

    def _pct_str(current: int, goal: int) -> str:
        pct = current / goal * 100 if goal > 0 else 0
        return f"{current}/{goal} ({pct:.0f}%)"

    print()
    print(f"  {'指標':<28} {'進捗':>20}  バー")
    _hr("─")

    metrics = [
        ("総セッション数",          total,       GOAL_SESSIONS,  "件"),
        ("リアル/ハイブリッド実行",  n_real + n_hybrid, GOAL_REAL, "件"),
        ("アウトカム確定数",        n_confirmed, GOAL_CONFIRMED, "件"),
    ]
    for label, current, goal, unit in metrics:
        bar   = _progress_bar(current, goal)
        pct_s = _pct_str(current, goal)
        print(f"  {label:<28} {pct_s:>20}  │{bar}│ 目標: {goal}{unit}")

    _hr("─")
    print()

    # 総合進捗スコア（3指標の達成率の平均）
    rates = []
    for _, current, goal, _ in metrics:
        rates.append(min(current / goal, 1.0) if goal > 0 else 0)
    overall = sum(rates) / len(rates) * 100

    bar = _progress_bar(int(overall), 100)
    print(f"  総合進捗スコア: {overall:.1f}%  │{bar}│")
    print()

    # ステータスメッセージ
    if overall < 20:
        status = "🔴 初期段階 — データ収集を継続してください"
    elif overall < 50:
        status = "🟡 蓄積中 — ハイブリッドモードでの実行を増やしましょう"
    elif overall < 80:
        status = "🟠 良好 — アウトカムの確定を増やすと統計的有意性が上がります"
    else:
        status = "🟢 論文執筆準備が整いつつあります"

    print(f"  ステータス: {status}")
    print()

    # データ品質メモ
    print("  【データ品質メモ】")
    if total > 0:
        mock_pct = df["mock_mode"].sum() / total * 100
        if mock_pct > 50:
            print(f"  ⚠️  モックデータが {mock_pct:.0f}% を占めています。")
            print("     --hybrid モードでの実行を増やし、リアルデータの比率を上げてください。")
        else:
            print(f"  ✅ リアル/ハイブリッドデータが {100 - mock_pct:.0f}% を占めています。")

        if n_confirmed == 0:
            print("  ⚠️  アウトカムが未確定です。ExitAgent による SELL 判定後に WIN/LOSS が付与されます。")
        elif n_confirmed / total < 0.3:
            print(f"  ℹ️  アウトカム確定率: {n_confirmed/total*100:.1f}% — ポジション保有期間中のレコードが多い状態です。")
    else:
        print("  ⚠️  レコードがありません。run_pipeline.py を実行してデータを蓄積してください。")


# ============================================================================
# メイン
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ECC 学習データ分析 & 研究進捗レポーター",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--file", type=Path, default=DEFAULT_JSONL,
        help="JSONL ファイルパス",
    )
    parser.add_argument(
        "--mode", choices=["all", "real", "hybrid", "mock"], default="all",
        help="集計対象モード (all=全件, real=mock除外, hybrid=ハイブリッドのみ, mock=モックのみ)",
    )
    parser.add_argument(
        "--no-ticker", action="store_true",
        help="銘柄別統計を省略",
    )
    args = parser.parse_args()

    # ─── ロード & フィルタ ─────────────────────────────────────────────────
    all_records     = load_records(args.file)
    filtered        = filter_records(all_records, args.mode)

    if not filtered:
        print(f"\n⚠️  モード '{args.mode}' に該当するレコードがありません。")
        print(f"   合計 {len(all_records)} 件のレコードが存在します。")
        sys.exit(0)

    df = to_dataframe(filtered)

    # ─── ヘッダー ─────────────────────────────────────────────────────────
    print()
    print("═" * _W)
    print("  ECC スイングトレード AI  学習データ分析レポート")
    print(f"  ファイル: {args.file}")
    print(f"  集計モード: {args.mode}  /  対象レコード: {len(df):,} 件")
    print("═" * _W)

    # ─── 各セクション ──────────────────────────────────────────────────────
    section_summary(df, args.mode)
    section_score_distribution(df)
    section_daily_trend(df)
    section_signal_analysis(df)
    if not args.no_ticker:
        section_ticker_stats(df)
    section_research_progress(df)

    print()
    print("═" * _W)
    print("  分析完了。")
    print("═" * _W)
    print()


if __name__ == "__main__":
    main()
