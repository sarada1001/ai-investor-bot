#!/usr/bin/env python3
"""
scripts/verify_analytics_etl.py — DuckDB で Parquet JOIN 確認

build_analytics_parquet.py で生成した2ファイルを DuckDB で読み込み、
JOIN クエリが通ることを確認する。

Usage:
    python scripts/verify_analytics_etl.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

SCRIPT_DIR    = Path(__file__).parent
PROJECT_ROOT  = SCRIPT_DIR.parent
ANALYTICS_DIR = PROJECT_ROOT / "data" / "analytics"

DECISIONS_PATH = ANALYTICS_DIR / "fact_decisions.parquet"
SIGNALS_PATH   = ANALYTICS_DIR / "fact_agent_signals.parquet"


def main() -> None:
    for p in (DECISIONS_PATH, SIGNALS_PATH):
        if not p.exists():
            print(f"[ERROR] ファイルが見つかりません: {p}")
            print("先に build_analytics_parquet.py を実行してください。")
            sys.exit(1)

    con = duckdb.connect()

    # ── スキーマ確認 ────────────────────────────────────────────
    print("─── fact_decisions スキーマ ───")
    con.execute(f"DESCRIBE SELECT * FROM read_parquet('{DECISIONS_PATH}')").df().apply(
        lambda row: print(f"  {row['column_name']:<28} {row['column_type']}"), axis=1
    )

    print("\n─── fact_agent_signals スキーマ ───")
    con.execute(f"DESCRIBE SELECT * FROM read_parquet('{SIGNALS_PATH}')").df().apply(
        lambda row: print(f"  {row['column_name']:<28} {row['column_type']}"), axis=1
    )

    # ── 基本 JOIN クエリ ────────────────────────────────────────
    print("\n─── JOIN サンプル 5行（STRONG BUY かつ is_closed=true） ───")
    query = f"""
    SELECT
        d.date,
        d.ticker,
        d.decision,
        d.score,
        d.outcome_label,
        s.agent_name,
        s.signal_score,
        s.trend
    FROM read_parquet('{DECISIONS_PATH}') d
    JOIN read_parquet('{SIGNALS_PATH}') s
      ON d.record_id = s.record_id
    WHERE d.decision = 'STRONG BUY'
      AND d.is_closed = true
    ORDER BY d.date, s.agent_name
    LIMIT 5
    """
    result = con.execute(query).df()
    print(result.to_string(index=False))

    # ── エージェント別平均シグナルスコア ────────────────────────
    print("\n─── エージェント別平均 signal_score（is_closed=true のみ） ───")
    agg_query = f"""
    SELECT
        s.agent_name,
        COUNT(*)                         AS n,
        ROUND(AVG(s.signal_score), 3)   AS avg_signal,
        ROUND(AVG(CASE WHEN d.outcome_label = 'WIN' THEN 1.0 ELSE 0.0 END), 3) AS win_rate
    FROM read_parquet('{DECISIONS_PATH}') d
    JOIN read_parquet('{SIGNALS_PATH}') s
      ON d.record_id = s.record_id
    WHERE d.is_closed = true
    GROUP BY s.agent_name
    ORDER BY s.agent_name
    """
    print(con.execute(agg_query).df().to_string(index=False))

    print("\n[OK] DuckDB JOIN 検証完了")
    con.close()


if __name__ == "__main__":
    main()
