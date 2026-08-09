"""
dashboard/app.py — AI Investor Bot 分析ダッシュボード
Usage: streamlit run dashboard/app.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from dashboard import queries

st.set_page_config(
    page_title="AI Investor Bot ダッシュボード",
    page_icon="📈",
    layout="wide",
)

# ─────────────────────────────────────────────────────────────
# 接続・基本メタデータ
# ─────────────────────────────────────────────────────────────

@st.cache_resource
def get_connection():
    return queries.connect()


con = get_connection()
all_tickers = queries.ticker_list(con)
data_min, data_max = queries.date_range(con)

# ─────────────────────────────────────────────────────────────
# サイドバー：フィルタ
# ─────────────────────────────────────────────────────────────

st.sidebar.title("フィルタ")

date_start = st.sidebar.date_input(
    "開始日",
    value=date.fromisoformat(data_min),
    min_value=date.fromisoformat(data_min),
    max_value=date.fromisoformat(data_max),
)
date_end = st.sidebar.date_input(
    "終了日",
    value=date.fromisoformat(data_max),
    min_value=date.fromisoformat(data_min),
    max_value=date.fromisoformat(data_max),
)

selected_tickers = st.sidebar.multiselect(
    "銘柄（空=全銘柄）",
    options=all_tickers,
    default=[],
)

mock_option = st.sidebar.radio(
    "モード",
    options=["全て", "Mock のみ", "Live のみ"],
    index=0,
)
mock_mode_filter = {"全て": None, "Mock のみ": True, "Live のみ": False}[mock_option]

hybrid_option = st.sidebar.radio(
    "Hybrid",
    options=["全て", "Hybrid のみ", "非Hybrid のみ"],
    index=0,
)
hybrid_mode_filter = {"全て": None, "Hybrid のみ": True, "非Hybrid のみ": False}[hybrid_option]

exclude_gate = st.sidebar.toggle("GATE_SKIPPED を除外", value=False)

ds = str(date_start)
de = str(date_end)

# ─────────────────────────────────────────────────────────────
# タイトル
# ─────────────────────────────────────────────────────────────

st.title("📈 AI Investor Bot 分析ダッシュボード")
st.caption(f"データ範囲: {data_min} ～ {data_max}　｜　フィルタ後期間: {ds} ～ {de}")

# ─────────────────────────────────────────────────────────────
# Section 1: 日次スキャン数
# ─────────────────────────────────────────────────────────────

st.header("1. 日次スキャン数")
st.caption("スキャン数の急増・急減は MacroAgent の強制 HOLD や CircuitBreaker 発火と連動することが多い。")

df_daily = queries.daily_scans(
    con, selected_tickers, ds, de,
    mock_mode_filter, hybrid_mode_filter, exclude_gate,
)

if df_daily.empty:
    st.info("該当データがありません。")
else:
    fig1 = px.line(
        df_daily, x="date", y="scan_count",
        markers=True,
        labels={"date": "日付", "scan_count": "スキャン数"},
    )
    fig1.update_layout(height=300)
    st.plotly_chart(fig1, use_container_width=True)
    col1, col2, col3 = st.columns(3)
    col1.metric("合計スキャン数", int(df_daily["scan_count"].sum()))
    col2.metric("日平均", f"{df_daily['scan_count'].mean():.1f}")
    col3.metric("最大（1日）", int(df_daily["scan_count"].max()))

# ─────────────────────────────────────────────────────────────
# Section 2: HOLD / STRONG BUY / GATE_SKIPPED 比率
# ─────────────────────────────────────────────────────────────

st.header("2. 判断カテゴリ比率")
st.caption("GATE_SKIPPED は FundamentalAgent をスキップしたコスト削減ケース。HOLD 比率が高い日は弱気相場フェーズを示唆。")

df_ratio = queries.decision_ratio(
    con, selected_tickers, ds, de, mock_mode_filter, hybrid_mode_filter,
)

if df_ratio.empty:
    st.info("該当データがありません。")
else:
    color_map = {
        "STRONG BUY": "#00b386",
        "HOLD":       "#aaaaaa",
        "GATE_SKIPPED": "#ff8c00",
        "UNKNOWN":    "#cccccc",
    }
    fig2 = px.pie(
        df_ratio, names="category", values="n",
        color="category", color_discrete_map=color_map,
        hole=0.4,
    )
    fig2.update_layout(height=350)
    col_pie, col_tbl = st.columns([2, 1])
    with col_pie:
        st.plotly_chart(fig2, use_container_width=True)
    with col_tbl:
        st.dataframe(
            df_ratio.assign(
                割合=lambda d: (d["n"] / d["n"].sum() * 100).round(1).astype(str) + "%"
            ).rename(columns={"category": "カテゴリ", "n": "件数"}),
            use_container_width=True,
            hide_index=True,
        )

# ─────────────────────────────────────────────────────────────
# Section 3: エージェント別シグナル分布
# ─────────────────────────────────────────────────────────────

st.header("3. エージェント別シグナル分布")
st.caption("分布の幅が広いエージェントほど局面依存が強く判断への寄与が大きい。is_skipped=true の行は集計から除外。")

df_sig = queries.agent_signals(
    con, selected_tickers, ds, de,
    mock_mode_filter, hybrid_mode_filter, exclude_gate,
)

if df_sig.empty:
    st.info("該当データがありません。")
else:
    fig3 = px.box(
        df_sig,
        x="agent_name", y="signal_score",
        color="agent_name",
        points="outliers",
        labels={"agent_name": "エージェント", "signal_score": "シグナルスコア"},
        category_orders={"agent_name": sorted(df_sig["agent_name"].unique())},
    )
    fig3.update_layout(height=380, showlegend=False)
    fig3.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
    st.plotly_chart(fig3, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# Section 4: MacroAgent trend 別 HOLD 率
# ─────────────────────────────────────────────────────────────

st.header("4. MacroAgent trend 別 HOLD 率")
st.caption(
    "MacroAgent のトレンド判定（negative / neutral / positive）ごとの HOLD 率を比較する。"
    "gate_skipped 行は HOLD 率の算出から除外し、別途件数を注記する。"
)

df_macro_hold, df_macro_skipped = queries.macro_hold_rate_by_trend(
    con, selected_tickers, ds, de, mock_mode_filter, hybrid_mode_filter,
)

if df_macro_hold.empty and df_macro_skipped.empty:
    st.info("該当データがありません。")
else:
    TREND_ORDER = ["negative", "neutral", "positive"]
    TREND_COLOR = {
        "negative": "#d62728",
        "neutral":  "#aaaaaa",
        "positive": "#00b386",
    }

    skipped_map = (
        df_macro_skipped.set_index("trend")["gate_skipped_n"].to_dict()
        if not df_macro_skipped.empty else {}
    )

    # HOLD率算出不能（全件gate_skipped）のtrendをプレースホルダで補完
    all_trends_set = set(df_macro_hold["trend"].tolist()) | set(skipped_map.keys())
    placeholder_rows = [
        {"trend": t, "total": 0, "hold_n": 0.0, "hold_rate": None}
        for t in TREND_ORDER if t not in df_macro_hold["trend"].values
    ]
    if placeholder_rows:
        df_macro_hold = pd.concat(
            [df_macro_hold, pd.DataFrame(placeholder_rows)], ignore_index=True
        )

    df_macro_hold["trend"] = pd.Categorical(
        df_macro_hold["trend"], categories=TREND_ORDER, ordered=True
    )
    df_macro_hold = df_macro_hold.sort_values("trend")

    df_plot = df_macro_hold[df_macro_hold["hold_rate"].notna()].copy()

    fig4 = px.bar(
        df_plot,
        x="trend",
        y="hold_rate",
        color="trend",
        color_discrete_map=TREND_COLOR,
        text=df_plot["hold_rate"].apply(lambda v: f"{v*100:.1f}%"),
        labels={"trend": "MacroAgent trend", "hold_rate": "HOLD 率"},
        category_orders={"trend": TREND_ORDER},
    )
    fig4.update_traces(textposition="outside", width=0.4)
    fig4.update_layout(
        height=380,
        yaxis=dict(tickformat=".0%", range=[0, 1.05]),
        showlegend=False,
    )
    st.plotly_chart(fig4, use_container_width=True)

    # サンプル数サマリーテーブル（全trendを表示）
    summary_rows = []
    for trend in TREND_ORDER:
        row_data = df_macro_hold[df_macro_hold["trend"] == trend]
        n_eligible = int(row_data["total"].iloc[0]) if not row_data.empty else 0
        hold_n = int(row_data["hold_n"].iloc[0]) if not row_data.empty and pd.notna(row_data["hold_n"].iloc[0]) else 0
        hold_rate_val = row_data["hold_rate"].iloc[0] if not row_data.empty else None
        hold_rate_str = f"{hold_rate_val*100:.1f}%" if pd.notna(hold_rate_val) else "—（全件GATE_SKIPPED）"
        summary_rows.append({
            "trend": trend,
            "HOLD率算出対象 (n)": n_eligible,
            "HOLD件数": hold_n,
            "HOLD率": hold_rate_str,
            "GATE_SKIPPED (除外)": int(skipped_map.get(trend, 0)),
        })
    df_summary = pd.DataFrame(summary_rows)

    col_tbl4, col_note4 = st.columns([2, 1])
    with col_tbl4:
        st.dataframe(df_summary, use_container_width=True, hide_index=True)
    with col_note4:
        neg_skipped = int(skipped_map.get("negative", 0))
        neg_eligible = int(
            df_macro_hold.loc[df_macro_hold["trend"] == "negative", "total"].iloc[0]
        ) if "negative" in df_macro_hold["trend"].values else 0
        st.warning(
            f"**⚠️ サンプル数に注意**\n\n"
            f"negative: 全 {neg_skipped} 件が GATE_SKIPPED\n"
            f"→ HOLD率算出対象ゼロのため棒グラフに表示なし\n\n"
            f"MacroAgent が negative を返した場合、"
            f"システムがゲートを発火させてスキャンをスキップしている。\n\n"
            f"**統計的に強い主張はできません。**",
            icon="⚠️",
        )


# ─────────────────────────────────────────────────────────────
# Section 5: HOLD反実仮想介入実験（原因エージェント特定）
# ─────────────────────────────────────────────────────────────

st.header("5. HOLD反実仮想介入実験")
st.caption(
    "各HOLDケースについてエージェントを1体ずつ除外/反転させ、判断が変わるかを観測した結果。"
    "判断を単独で決定づけたエージェントを specialized cause として特定する（卒業研究：AIのHOLD判断評価フレームワーク）。"
    "本セクションは日付・銘柄フィルタの対象外（別データソース）。"
)

df_intervention = queries.load_intervention_results()

if df_intervention.empty:
    st.info("介入実験データがありません（data/research/intervention_results.jsonl）。")
else:
    df_cause = queries.intervention_cause_agent_distribution(df_intervention)
    df_hold_type = queries.intervention_hold_type_summary(df_intervention)

    col5a, col5b, col5c = st.columns(3)
    col5a.metric("分析済みHOLDケース数", len(df_intervention))
    single_cause_total = int(df_intervention["true_cause_agents"].apply(lambda a: len(a) > 0).sum())
    col5b.metric("単独原因を特定できたケース", single_cause_total)
    col5c.metric(
        "単独原因特定率",
        f"{single_cause_total / len(df_intervention) * 100:.1f}%" if len(df_intervention) else "—",
    )

    col5_chart, col5_table = st.columns([2, 1])
    with col5_chart:
        fig5 = px.bar(
            df_cause,
            x="agent", y="n",
            color="agent",
            labels={"agent": "原因エージェント", "n": "ケース数"},
        )
        fig5.update_layout(height=360, showlegend=False, xaxis_tickangle=-20)
        st.plotly_chart(fig5, use_container_width=True)
    with col5_table:
        st.dataframe(df_hold_type, use_container_width=True, hide_index=True)
        st.caption(
            "hold_type: gate=ゲート発火によるHOLD／manager=ManagerAgent判断によるHOLD。\n\n"
            "single_cause_rate: 単独エージェント除外で判断が反転したケースの割合。"
        )


# ─────────────────────────────────────────────────────────────
# Section 7: 銘柄別判断履歴
# ─────────────────────────────────────────────────────────────

st.header("7. 銘柄別判断履歴")
st.caption("score の推移で STRONG BUY → HOLD への転換点を確認できる。橙マーカーは GATE_SKIPPED（CB 発火）を示す。")

ticker_sel = st.selectbox(
    "銘柄を選択",
    options=all_tickers,
    index=all_tickers.index("AAPL") if "AAPL" in all_tickers else 0,
)

df_hist = queries.ticker_history(
    con, ticker_sel, ds, de, mock_mode_filter, hybrid_mode_filter,
)

if df_hist.empty:
    st.info("該当データがありません。")
else:
    symbol_map = {"STRONG BUY": "circle", "HOLD": "square"}
    color_map7 = {"STRONG BUY": "#00b386", "HOLD": "#aaaaaa"}

    fig7 = go.Figure()
    for dec, grp in df_hist.groupby("decision"):
        normal = grp[~grp["gate_skipped"].fillna(False)]
        skipped = grp[grp["gate_skipped"].fillna(False)]
        if not normal.empty:
            fig7.add_trace(go.Scatter(
                x=normal["date"], y=normal["score"],
                mode="markers+lines",
                name=dec,
                marker=dict(symbol=symbol_map.get(dec, "circle"), size=9,
                            color=color_map7.get(dec, "#888")),
                line=dict(color=color_map7.get(dec, "#888"), width=1.5),
            ))
        if not skipped.empty:
            fig7.add_trace(go.Scatter(
                x=skipped["date"], y=skipped["score"],
                mode="markers",
                name=f"{dec} (GATE_SKIPPED)",
                marker=dict(symbol="x", size=12, color="#ff8c00"),
                hovertext=skipped["gate_reason"].fillna(""),
            ))

    if "threshold" in df_hist.columns:
        pass  # threshold is in fact_decisions but not fetched here
    fig7.update_layout(
        height=380,
        xaxis_title="日付",
        yaxis_title="スコア",
        title=f"{ticker_sel} — 判断スコア推移",
    )
    st.plotly_chart(fig7, use_container_width=True)

    with st.expander("データテーブル"):
        st.dataframe(
            df_hist[["date", "decision", "score", "gate_skipped",
                      "social_hype_score", "macro_forced_hold", "outcome_label"]],
            use_container_width=True,
            hide_index=True,
        )

# ─────────────────────────────────────────────────────────────
# Section 8: hype_score と最終 decision の関係
# ─────────────────────────────────────────────────────────────

st.header("8. SNS Hype Score と判断スコアの関係")
st.caption(
    "hype_score > 0.7 で social_hype_penalty が発動しスコアが押し下げられる。"
    "SNS 煽り局面での誤信号抑制効果を確認できる。"
)

df_hype = queries.hype_scatter(
    con, selected_tickers, ds, de, mock_mode_filter, hybrid_mode_filter,
)

if df_hype.empty:
    st.info("該当データがありません。")
else:
    color_map8 = {"STRONG BUY": "#00b386", "HOLD": "#aaaaaa"}
    fig8 = px.scatter(
        df_hype,
        x="social_hype_score", y="score",
        color="decision",
        color_discrete_map=color_map8,
        symbol="social_hype_penalty",
        hover_data=["ticker", "date_str"],
        labels={
            "social_hype_score": "SNS Hype Score",
            "score": "最終スコア",
            "decision": "判断",
            "social_hype_penalty": "Penalty 発動",
        },
        opacity=0.75,
    )
    fig8.add_vline(x=0.7, line_dash="dash", line_color="red",
                   annotation_text="Penalty 閾値 0.7",
                   annotation_position="top right")
    fig8.update_layout(height=400)
    st.plotly_chart(fig8, use_container_width=True)

    penalty_n = df_hype["social_hype_penalty"].sum()
    st.caption(f"Penalty 発動: {int(penalty_n)} 件 / 全 {len(df_hype)} 件")

# ─────────────────────────────────────────────────────────────
# Section 9: AuditAgent による重み変化
# ─────────────────────────────────────────────────────────────

st.header("9. AuditAgent 実効ウェイト推移")
st.caption(
    "勝率が閾値（40%）を下回ると SUSPENDED になり実効ウェイトが 0 になる。"
    "ステータス変化があった時点をマーカーで強調する。"
)

df_history = queries.load_agent_history()

if df_history.empty:
    st.info("agent_status_history.jsonl が空またはファイルなし。")
else:
    agent_names = sorted(df_history["agent_name"].unique())
    agents_to_show = [a for a in agent_names if a != "ManagerAgent"]

    df_w = df_history[df_history["agent_name"].isin(agents_to_show)].copy()
    df_w = df_w.dropna(subset=["effective_weight"])

    if df_w.empty:
        st.info("effective_weight の有効なデータがありません。")
    else:
        fig9 = px.line(
            df_w,
            x="timestamp", y="effective_weight",
            color="agent_name",
            markers=True,
            labels={
                "timestamp": "評価日時",
                "effective_weight": "実効ウェイト",
                "agent_name": "エージェント",
            },
        )
        # ステータス変化点を強調
        status_changes = df_w[df_w["prev_status"] != df_w["new_status"]]
        if not status_changes.empty:
            fig9.add_trace(go.Scatter(
                x=status_changes["timestamp"],
                y=status_changes["effective_weight"],
                mode="markers",
                marker=dict(symbol="star", size=14, color="red"),
                name="ステータス変化",
                hovertext=status_changes.apply(
                    lambda r: f"{r.agent_name}: {r.prev_status}→{r.new_status}", axis=1
                ),
            ))
        fig9.update_layout(height=380, yaxis_range=[-0.05, 0.55])
        st.plotly_chart(fig9, use_container_width=True)

    with st.expander("最新ステータス一覧"):
        latest = (
            df_history.sort_values("timestamp")
            .groupby("agent_name")
            .last()
            .reset_index()
        )
        st.dataframe(
            latest[["agent_name", "new_status", "win_rate",
                     "total_trades", "winning_trades", "effective_weight"]]
            .rename(columns={
                "agent_name": "エージェント",
                "new_status": "ステータス",
                "win_rate": "勝率",
                "total_trades": "取引数",
                "winning_trades": "勝ち数",
                "effective_weight": "実効ウェイト",
            }),
            use_container_width=True,
            hide_index=True,
        )

# ─────────────────────────────────────────────────────────────
# Section 10: CircuitBreaker 発火タイムライン
# ─────────────────────────────────────────────────────────────

st.header("10. CircuitBreaker 発火タイムライン")
st.caption(
    "HARD_TRIP は連続損失でサーキットブレーカーが発動した記録。"
    "翌日以降の '継続' トリップがクールダウン期間を示す。"
)

df_cb = queries.circuit_breaker_events(
    con, selected_tickers, ds, de, mock_mode_filter, hybrid_mode_filter,
)

if df_cb.empty:
    st.info("期間内に CircuitBreaker 発火なし。")
else:
    def _cb_type(reason: str) -> str:
        if "継続" in reason:
            return "継続 (クールダウン)"
        return "初回 HARD_TRIP"

    df_cb["cb_type"] = df_cb["gate_reason"].apply(_cb_type)

    color_map10 = {
        "初回 HARD_TRIP":      "#d62728",
        "継続 (クールダウン)": "#ff7f0e",
    }
    fig10 = px.scatter(
        df_cb,
        x="date_str", y="ticker",
        color="cb_type",
        color_discrete_map=color_map10,
        symbol="cb_type",
        hover_data=["gate_reason"],
        labels={
            "date_str": "日付",
            "ticker": "銘柄",
            "cb_type": "種別",
        },
        size_max=14,
    )
    fig10.update_traces(marker_size=14)
    fig10.update_layout(height=max(280, len(df_cb["ticker"].unique()) * 40 + 80))
    st.plotly_chart(fig10, use_container_width=True)

    st.dataframe(
        df_cb[["date_str", "ticker", "cb_type", "gate_reason"]]
        .rename(columns={
            "date_str": "日付",
            "ticker": "銘柄",
            "cb_type": "種別",
            "gate_reason": "発火理由",
        }),
        use_container_width=True,
        hide_index=True,
    )

# ─────────────────────────────────────────────────────────────
# フッター
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# Section 6: 説明信頼性スコア
# ─────────────────────────────────────────────────────────────

st.header("6. 説明信頼性スコア（v1）")
st.caption(
    "rationale 本文からエージェント別の数値根拠を正規表現で抽出し、実際の signal_score との一致率を計算。"
    "将来の長文 rationale（400文字版）で有効になる設計。"
)

df_faith = queries.faithfulness_scores(
    con, selected_tickers, ds, de, mock_mode_filter, hybrid_mode_filter,
)
n_truncated = queries.truncated_rationale_count(
    con, selected_tickers, ds, de, mock_mode_filter, hybrid_mode_filter,
)

info_col, _ = st.columns([3, 1])
with info_col:
    st.info(
        f"**対象外: {n_truncated} 件**（rationale が 200 文字で切断済み — 全文消失のためスコア不能）\n\n"
        f"**スコア対象: {len(df_faith)} 件**（非truncated・mock除外）",
        icon="ℹ️",
    )

if df_faith.empty:
    st.warning("スコア対象レコードがありません。")
else:
    col_hist, col_stats = st.columns([3, 1])

    with col_hist:
        fig6 = px.histogram(
            df_faith,
            x="faithfulness_score",
            nbins=11,
            range_x=[-0.05, 1.05],
            labels={"faithfulness_score": "説明信頼性スコア", "count": "件数"},
            color_discrete_sequence=["#5b8dee"],
        )
        fig6.update_layout(
            height=300,
            bargap=0.1,
            xaxis=dict(tickvals=[i / 6 for i in range(7)],
                       ticktext=[f"{i}/6" for i in range(7)]),
        )
        st.plotly_chart(fig6, use_container_width=True)

    with col_stats:
        mean_s = df_faith["faithfulness_score"].mean()
        n_zero = int((df_faith["faithfulness_score"] == 0).sum())
        n_pos  = int((df_faith["faithfulness_score"] > 0).sum())
        st.metric("平均スコア", f"{mean_s:.3f}")
        st.metric("スコア = 0", f"{n_zero} 件")
        st.metric("スコア > 0", f"{n_pos} 件")

    st.caption(
        "現在の非truncated rationale は定性テキスト主体（数値未記載）のため全件 0.0。"
        "200文字制限撤廃後（400文字版）の rationale から数値が記載されるようになると機能する。"
    )

    low_score = df_faith[df_faith["faithfulness_score"] < 1.0].head(3)
    if not low_score.empty:
        with st.expander(f"低スコアサンプル（上位 {len(low_score)} 件）"):
            for _, r in low_score.iterrows():
                st.markdown(f"**[{r.ticker}]** score={r.faithfulness_score:.3f} ({r.matched_count}/{r.total_agents})")
                st.text(f"rationale: {r.rationale_preview[:100]}")
                try:
                    details = __import__('json').loads(r.details_json)
                    mismatch = [d for d in details if not d["matched"]]
                    for d in mismatch[:3]:
                        ext = f"{d['extracted']:+.3f}" if d["extracted"] is not None else "未抽出"
                        act = f"{d['actual']:+.3f}" if d["actual"] is not None else "N/A"
                        st.text(f"  ✗ {d['agent']}: 抽出={ext}  実際={act}")
                except Exception:
                    pass
                st.divider()

# ─────────────────────────────────────────────────────────────
# フッター
# ─────────────────────────────────────────────────────────────

st.divider()
st.caption("セクション 5（Ablation 寄与度）は未実装。")
