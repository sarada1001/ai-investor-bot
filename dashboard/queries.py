"""
dashboard/queries.py — DuckDB クエリ集
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

_ROOT              = Path(__file__).parent.parent
DECISIONS_PATH     = str(_ROOT / "data" / "analytics" / "fact_decisions.parquet")
SIGNALS_PATH       = str(_ROOT / "data" / "analytics" / "fact_agent_signals.parquet")
FAITHFULNESS_PATH  = str(_ROOT / "data" / "analytics" / "fact_faithfulness.parquet")
HISTORY_PATH       = _ROOT / "data" / "evaluation" / "agent_status_history.jsonl"
INTERVENTION_PATH  = _ROOT / "data" / "research" / "intervention_results.jsonl"


def connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def _where(
    tickers: list[str],
    date_start: str,
    date_end: str,
    mock_mode: Optional[bool],
    hybrid_mode: Optional[bool],
    exclude_gate_skipped: bool,
    alias: str = "d",
) -> str:
    clauses = [
        f"{alias}.date >= '{date_start}'",
        f"{alias}.date <= '{date_end}'",
    ]
    if tickers:
        tlist = ", ".join(f"'{t}'" for t in tickers)
        clauses.append(f"{alias}.ticker IN ({tlist})")
    if mock_mode is not None:
        clauses.append(f"{alias}.mock_mode = {str(mock_mode).lower()}")
    if hybrid_mode is not None:
        clauses.append(f"{alias}.hybrid_mode = {str(hybrid_mode).lower()}")
    if exclude_gate_skipped:
        clauses.append(f"{alias}.gate_skipped = false")
    return "WHERE " + " AND ".join(clauses)


def ticker_list(con: duckdb.DuckDBPyConnection) -> list[str]:
    df = con.execute(
        f"SELECT DISTINCT ticker FROM read_parquet('{DECISIONS_PATH}') ORDER BY ticker"
    ).df()
    return df["ticker"].tolist()


def date_range(con: duckdb.DuckDBPyConnection) -> tuple[str, str]:
    df = con.execute(
        f"SELECT MIN(date)::VARCHAR, MAX(date)::VARCHAR FROM read_parquet('{DECISIONS_PATH}')"
    ).df()
    return df.iloc[0, 0], df.iloc[0, 1]


def daily_scans(
    con: duckdb.DuckDBPyConnection,
    tickers: list[str],
    date_start: str,
    date_end: str,
    mock_mode: Optional[bool],
    hybrid_mode: Optional[bool],
    exclude_gate_skipped: bool,
) -> pd.DataFrame:
    where = _where(tickers, date_start, date_end, mock_mode, hybrid_mode, exclude_gate_skipped)
    return con.execute(f"""
        SELECT d.date, COUNT(*) AS scan_count
        FROM read_parquet('{DECISIONS_PATH}') d
        {where}
        GROUP BY d.date
        ORDER BY d.date
    """).df()


def decision_ratio(
    con: duckdb.DuckDBPyConnection,
    tickers: list[str],
    date_start: str,
    date_end: str,
    mock_mode: Optional[bool],
    hybrid_mode: Optional[bool],
) -> pd.DataFrame:
    where = _where(tickers, date_start, date_end, mock_mode, hybrid_mode, False)
    return con.execute(f"""
        SELECT
            CASE
                WHEN d.gate_skipped = true THEN 'GATE_SKIPPED'
                ELSE COALESCE(d.decision, 'UNKNOWN')
            END AS category,
            COUNT(*) AS n
        FROM read_parquet('{DECISIONS_PATH}') d
        {where}
        GROUP BY category
        ORDER BY n DESC
    """).df()


def agent_signals(
    con: duckdb.DuckDBPyConnection,
    tickers: list[str],
    date_start: str,
    date_end: str,
    mock_mode: Optional[bool],
    hybrid_mode: Optional[bool],
    exclude_gate_skipped: bool,
) -> pd.DataFrame:
    where = _where(tickers, date_start, date_end, mock_mode, hybrid_mode, exclude_gate_skipped)
    return con.execute(f"""
        SELECT s.agent_name, s.signal_score, s.trend
        FROM read_parquet('{DECISIONS_PATH}') d
        JOIN read_parquet('{SIGNALS_PATH}') s ON d.record_id = s.record_id
        {where}
        AND s.is_skipped = false
        AND s.signal_score IS NOT NULL
        ORDER BY s.agent_name
    """).df()


def ticker_history(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    date_start: str,
    date_end: str,
    mock_mode: Optional[bool],
    hybrid_mode: Optional[bool],
) -> pd.DataFrame:
    where = _where([ticker], date_start, date_end, mock_mode, hybrid_mode, False)
    return con.execute(f"""
        SELECT d.date, d.decision, d.score, d.gate_skipped,
               d.gate_reason, d.social_hype_score,
               d.macro_forced_hold, d.outcome_label
        FROM read_parquet('{DECISIONS_PATH}') d
        {where}
        ORDER BY d.date
    """).df()


def hype_scatter(
    con: duckdb.DuckDBPyConnection,
    tickers: list[str],
    date_start: str,
    date_end: str,
    mock_mode: Optional[bool],
    hybrid_mode: Optional[bool],
) -> pd.DataFrame:
    where = _where(tickers, date_start, date_end, mock_mode, hybrid_mode, False)
    return con.execute(f"""
        SELECT d.social_hype_score, d.score, d.decision,
               d.social_hype_penalty, d.ticker, d.date::VARCHAR AS date_str
        FROM read_parquet('{DECISIONS_PATH}') d
        {where}
        AND d.social_hype_score IS NOT NULL
        ORDER BY d.social_hype_score
    """).df()


def macro_hold_rate_by_trend(
    con: duckdb.DuckDBPyConnection,
    tickers: list[str],
    date_start: str,
    date_end: str,
    mock_mode: Optional[bool],
    hybrid_mode: Optional[bool],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (hold_rate_df, gate_skipped_df).

    hold_rate_df  : trend × {total, hold_n, hold_rate} — gate_skipped 除外
    gate_skipped_df : trend × gate_skipped_n           — 注記用
    """
    where = _where(tickers, date_start, date_end, mock_mode, hybrid_mode, False)
    hold_rate = con.execute(f"""
        SELECT
            s.trend,
            COUNT(*)                                           AS total,
            SUM(CASE WHEN d.decision = 'HOLD' THEN 1 ELSE 0 END) AS hold_n,
            ROUND(
                SUM(CASE WHEN d.decision = 'HOLD' THEN 1.0 ELSE 0 END) / COUNT(*),
                4
            )                                                  AS hold_rate
        FROM read_parquet('{DECISIONS_PATH}') d
        JOIN read_parquet('{SIGNALS_PATH}') s ON d.record_id = s.record_id
        {where}
        AND s.agent_name = 'MacroAgent'
        AND s.trend IS NOT NULL
        AND d.gate_skipped = false
        GROUP BY s.trend
        ORDER BY s.trend
    """).df()

    gate_skipped = con.execute(f"""
        SELECT
            s.trend,
            COUNT(*) AS gate_skipped_n
        FROM read_parquet('{DECISIONS_PATH}') d
        JOIN read_parquet('{SIGNALS_PATH}') s ON d.record_id = s.record_id
        {where}
        AND s.agent_name = 'MacroAgent'
        AND s.trend IS NOT NULL
        AND d.gate_skipped = true
        GROUP BY s.trend
        ORDER BY s.trend
    """).df()

    return hold_rate, gate_skipped


def circuit_breaker_events(
    con: duckdb.DuckDBPyConnection,
    tickers: list[str],
    date_start: str,
    date_end: str,
    mock_mode: Optional[bool],
    hybrid_mode: Optional[bool],
) -> pd.DataFrame:
    where = _where(tickers, date_start, date_end, mock_mode, hybrid_mode, False)
    return con.execute(f"""
        SELECT d.date::VARCHAR AS date_str, d.ticker, d.gate_reason
        FROM read_parquet('{DECISIONS_PATH}') d
        {where}
        AND d.gate_reason LIKE '%CircuitBreaker HARD_TRIP%'
        ORDER BY d.date, d.ticker
    """).df()


def faithfulness_scores(
    con: duckdb.DuckDBPyConnection,
    tickers: list[str],
    date_start: str,
    date_end: str,
    mock_mode: Optional[bool],
    hybrid_mode: Optional[bool],
) -> pd.DataFrame:
    where = _where(tickers, date_start, date_end, mock_mode, hybrid_mode, False, alias="d")
    return con.execute(f"""
        SELECT f.record_id, f.ticker, f.date, f.faithfulness_score,
               f.matched_count, f.total_agents,
               f.rationale_preview, f.details_json
        FROM read_parquet('{FAITHFULNESS_PATH}') f
        JOIN read_parquet('{DECISIONS_PATH}') d ON f.record_id = d.record_id
        {where}
        ORDER BY f.faithfulness_score DESC, f.date
    """).df()


def truncated_rationale_count(
    con: duckdb.DuckDBPyConnection,
    tickers: list[str],
    date_start: str,
    date_end: str,
    mock_mode: Optional[bool],
    hybrid_mode: Optional[bool],
) -> int:
    where = _where(tickers, date_start, date_end, mock_mode, hybrid_mode, False)
    result = con.execute(f"""
        SELECT COUNT(*) AS n
        FROM read_parquet('{DECISIONS_PATH}') d
        {where}
        AND d.rationale_truncated = true
        AND d.mock_mode = false
    """).df()
    return int(result.iloc[0, 0])


def load_agent_history() -> pd.DataFrame:
    rows: list[dict] = []
    if not HISTORY_PATH.exists():
        return pd.DataFrame()
    with HISTORY_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def load_intervention_results() -> pd.DataFrame:
    """
    HOLD反実仮想介入実験の結果を読み込む（研究フレームワーク: AIのHOLD判断評価）。

    各行は1つのHOLDケースに対して、エージェントを1体ずつ除外/反転させて
    再判定した結果と、その差分から特定した true_cause_agents を持つ。
    """
    rows: list[dict] = []
    if not INTERVENTION_PATH.exists():
        return pd.DataFrame()
    with INTERVENTION_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def intervention_cause_agent_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """
    true_cause_agents を展開し、エージェント別に「単独でHOLDの原因と特定された回数」を集計。
    true_cause_agents が空のケースは "合議一致（単独原因なし）" として別枠に集計する。
    """
    if df.empty:
        return pd.DataFrame(columns=["agent", "n"])

    counts: dict[str, int] = {}
    no_single_cause = 0
    for agents in df["true_cause_agents"]:
        if not agents:
            no_single_cause += 1
            continue
        for a in agents:
            counts[a] = counts.get(a, 0) + 1

    rows = [{"agent": a, "n": n} for a, n in counts.items()]
    rows.append({"agent": "合議一致（単独原因なし）", "n": no_single_cause})
    return pd.DataFrame(rows).sort_values("n", ascending=False).reset_index(drop=True)


def intervention_hold_type_summary(df: pd.DataFrame) -> pd.DataFrame:
    """hold_type（gate/manager）別のケース数と、単独原因が特定できた割合。"""
    if df.empty:
        return pd.DataFrame(columns=["hold_type", "n", "single_cause_n", "single_cause_rate"])

    rows = []
    for hold_type, group in df.groupby("hold_type"):
        n = len(group)
        single_cause_n = int(group["true_cause_agents"].apply(lambda a: len(a) > 0).sum())
        rows.append({
            "hold_type": hold_type,
            "n": n,
            "single_cause_n": single_cause_n,
            "single_cause_rate": round(single_cause_n / n, 4) if n else None,
        })
    return pd.DataFrame(rows).sort_values("hold_type").reset_index(drop=True)
