"""
ECC スイングトレード自律エンジン — 学習データ可視化ダッシュボード

起動:
    streamlit run dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ================================================================
# ページ設定（最初に呼ぶ）
# ================================================================

st.set_page_config(
    page_title="ECC AI Fund — Dashboard",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ================================================================
# カスタム CSS
# ================================================================

st.markdown("""
<style>
/* ヘッダバナー */
.banner {
    background: linear-gradient(135deg, #0f2027, #203a43, #2c5364);
    border-radius: 12px;
    padding: 1.2rem 2rem;
    margin-bottom: 1.5rem;
    color: white;
}
.banner h1 { margin: 0; font-size: 1.8rem; }
.banner p  { margin: 0.2rem 0 0; opacity: .7; font-size: .9rem; }

/* Metric カードの数値を大きく */
[data-testid="stMetricValue"] { font-size: 1.9rem !important; }

/* セクション見出し */
.section-title {
    font-size: 1.1rem;
    font-weight: 700;
    color: #4f9cf9;
    border-left: 4px solid #4f9cf9;
    padding-left: .6rem;
    margin: 1.5rem 0 .8rem;
}
</style>
""", unsafe_allow_html=True)

# ================================================================
# ヘッダ
# ================================================================

st.markdown("""
<div class="banner">
  <h1>🏦 ECC スイングトレード自律エンジン</h1>
  <p>AI Investment Pipeline — 学習データ可視化ダッシュボード</p>
</div>
""", unsafe_allow_html=True)

# ================================================================
# データ読み込み
# ================================================================

DATA_PATH = Path("data/training/training_data.jsonl")


@st.cache_data(ttl=30)
def load_data(path: Path) -> pd.DataFrame:
    """JSONL を DataFrame に変換する。空行はスキップ。"""
    records: list[dict] = []
    if not path.exists():
        return pd.DataFrame()

    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            cot = raw.get("manager_chain_of_thought") or {}
            out = raw.get("manager_output") or {}
            inp = raw.get("inputs") or {}
            sigs = cot.get("signals") or {}

            records.append({
                # ── 基本情報 ──────────────────────────────────────
                "record_id":      raw.get("record_id", ""),
                "session_id":     raw.get("session_id", ""),
                "date":           raw.get("date", ""),
                "created_at":     raw.get("created_at", ""),
                "ticker":         raw.get("ticker", ""),
                "mock_mode":      raw.get("mock_mode", False),
                "hybrid_mode":    raw.get("hybrid_mode", False),
                # ── 判断結果 ──────────────────────────────────────
                "decision":       out.get("decision", "HOLD"),
                "score":          float(cot.get("weighted_score") or out.get("score") or 0.0),
                "threshold":      float(cot.get("threshold", 0.6)),
                "is_strong_buy":  bool(out.get("is_strong_buy", False)),
                "gate_skipped":   bool(cot.get("gate_skipped", False)),
                "macro_brake":    bool(cot.get("macro_forced_hold", False)),
                "hype_penalty":   bool(cot.get("social_hype_penalty", False)),
                "rationale":      cot.get("rationale", ""),
                # ── シグナル ──────────────────────────────────────
                "sig_fundamental": float(sigs.get("fundamental", 0.0)),
                "sig_technical":   float(sigs.get("technical", 0.0)),
                "sig_news":        float(sigs.get("news", 0.0)),
                "sig_macro":       float(sigs.get("macro", 0.0)),
                "sig_social":      float(sigs.get("social", 0.0)),
                # ── エージェント分析結果 ──────────────────────────
                "fa_trend":    (inp.get("fundamental_analysis") or {}).get("trend", "neutral"),
                "tech_trend":  (inp.get("technical_analysis")   or {}).get("trend", "neutral"),
                "news_score":  float((inp.get("news_analysis")  or {}).get("avg_sentiment_score", 0.0)),
                "macro_trend": (inp.get("macro_analysis")       or {}).get("trend", "neutral"),
                "social_hype": float((inp.get("social_analysis") or {}).get("hype_score", 0.0)),
                # ── 勝敗（バックテスト用） ─────────────────────────
                "outcome":       raw.get("outcome"),
                "outcome_label": raw.get("outcome_label"),  # "WIN" / "LOSS" / None
            })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


df_all = load_data(DATA_PATH)

if df_all.empty:
    st.warning(
        f"学習データが見つかりません。`{DATA_PATH}` にデータを蓄積してから再読み込みしてください。"
    )
    st.stop()

# ================================================================
# サイドバー — フィルタ
# ================================================================

with st.sidebar:
    st.header("🔍 フィルタ")

    # リロードボタン
    if st.button("🔄 最新データを取得", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # 銘柄
    all_tickers = sorted(df_all["ticker"].dropna().unique().tolist())
    sel_tickers = st.multiselect(
        "銘柄（Ticker）",
        options=all_tickers,
        default=all_tickers,
        placeholder="銘柄を選択...",
    )

    # 判定
    all_decisions = sorted(df_all["decision"].dropna().unique().tolist())
    sel_decisions = st.multiselect(
        "判定（Decision）",
        options=all_decisions,
        default=all_decisions,
        placeholder="判定を選択...",
    )

    # モードフィルタ
    st.divider()
    show_mock   = st.checkbox("モックデータを含む",   value=True)
    show_hybrid = st.checkbox("ハイブリッドデータを含む", value=True)

    st.divider()
    # 日付範囲
    if df_all["date"].notna().any():
        min_date = df_all["date"].min().date()
        max_date = df_all["date"].max().date()
        date_range = st.date_input(
            "日付範囲",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
    else:
        date_range = None

    st.divider()
    st.caption(f"データファイル: `{DATA_PATH}`")
    st.caption(f"全レコード数: **{len(df_all):,}**")

# ================================================================
# フィルタ適用
# ================================================================

df = df_all.copy()
if sel_tickers:
    df = df[df["ticker"].isin(sel_tickers)]
if sel_decisions:
    df = df[df["decision"].isin(sel_decisions)]
if not show_mock:
    df = df[~df["mock_mode"]]
if not show_hybrid:
    df = df[~df["hybrid_mode"].fillna(False)]
if date_range and len(date_range) == 2:
    start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
    df = df[(df["date"] >= start) & (df["date"] <= end)]

if df.empty:
    st.info("絞り込み条件に一致するレコードがありません。サイドバーのフィルタを調整してください。")
    st.stop()

# ================================================================
# サマリー指標
# ================================================================

st.markdown('<div class="section-title">📊 サマリー指標</div>', unsafe_allow_html=True)

total         = len(df)
strong_buy_n  = df["is_strong_buy"].sum()
sb_rate       = strong_buy_n / total * 100 if total else 0
avg_score     = df["score"].mean()
gate_skip_n   = df["gate_skipped"].sum()
macro_brake_n = df["macro_brake"].sum()

# 勝率（outcome_label が付いたレコードのみ）
labelled = df[df["outcome_label"].notna()]
win_n    = (labelled["outcome_label"] == "WIN").sum()
win_rate = win_n / len(labelled) * 100 if len(labelled) > 0 else None

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("総分析数",          f"{total:,}")
c2.metric("STRONG BUY 数",     f"{strong_buy_n:,}",
          delta=f"{sb_rate:.1f}% of total",
          delta_color="normal" if sb_rate >= 30 else "off")
c3.metric("勝率 (Win Rate)",
          f"{win_rate:.1f}%" if win_rate is not None else "N/A",
          delta=f"{win_n}/{len(labelled)} 件" if len(labelled) > 0 else "未評価",
          delta_color="normal" if (win_rate or 0) >= 55 else "off")
c4.metric("平均スコア",         f"{avg_score:+.3f}")
c5.metric("Gate HOLD 数",      f"{gate_skip_n:,}",
          delta=f"macro brake: {macro_brake_n}件",
          delta_color="inverse" if macro_brake_n > 0 else "off")
c6.metric("Hype ペナルティ",    f"{df['hype_penalty'].sum():,}",
          delta="ソーシャル補正あり",
          delta_color="off")

# ================================================================
# グラフ行 1 — 日次推移 & スコア分布
# ================================================================

st.markdown('<div class="section-title">📈 分析推移 & スコア分布</div>',
            unsafe_allow_html=True)

col_left, col_right = st.columns([3, 2], gap="large")

with col_left:
    # ── 日次分析件数（判定別色分け）────────────────────────────
    daily = (
        df.groupby(["date", "decision"])
          .size()
          .reset_index(name="count")
    )
    daily["date_str"] = daily["date"].dt.strftime("%Y-%m-%d")

    color_map = {
        "STRONG BUY": "#00c49f",
        "HOLD":       "#4f9cf9",
    }

    fig_daily = px.bar(
        daily,
        x="date_str",
        y="count",
        color="decision",
        color_discrete_map=color_map,
        title="日ごとの分析実行推移（判定別）",
        labels={"date_str": "日付", "count": "件数", "decision": "判定"},
        text="count",
    )
    fig_daily.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#cdd6f4",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=10, r=10, t=50, b=10),
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,.08)"),
    )
    fig_daily.update_traces(textposition="outside")
    st.plotly_chart(fig_daily, use_container_width=True)

with col_right:
    # ── 加重スコア分布（ヒストグラム）───────────────────────────
    fig_hist = px.histogram(
        df,
        x="score",
        color="decision",
        color_discrete_map=color_map,
        nbins=20,
        title="AI確信度（加重スコア）分布",
        labels={"score": "加重スコア", "count": "件数", "decision": "判定"},
        opacity=0.85,
    )
    # Strong Buy 閾値ライン
    threshold = float(df["threshold"].iloc[0]) if not df.empty else 0.6
    fig_hist.add_vline(
        x=threshold,
        line_dash="dash",
        line_color="#f38ba8",
        annotation_text=f"閾値 {threshold}",
        annotation_position="top right",
        annotation_font_color="#f38ba8",
    )
    fig_hist.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#cdd6f4",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=10, r=10, t=50, b=10),
        barmode="overlay",
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,.08)"),
    )
    st.plotly_chart(fig_hist, use_container_width=True)

# ================================================================
# グラフ行 2 — シグナル内訳 & ティッカー別件数
# ================================================================

st.markdown('<div class="section-title">🔬 シグナル詳細分析</div>',
            unsafe_allow_html=True)

col_a, col_b = st.columns([2, 3], gap="large")

with col_a:
    # ── 平均シグナルレーダーチャート ─────────────────────────────
    sig_cols = {
        "ファンダメンタル": "sig_fundamental",
        "テクニカル":       "sig_technical",
        "ニュース":         "sig_news",
        "マクロ":           "sig_macro",
        "ソーシャル":       "sig_social",
    }
    labels  = list(sig_cols.keys())
    values  = [df[v].mean() for v in sig_cols.values()]
    values += values[:1]   # close the polygon

    fig_radar = go.Figure(go.Scatterpolar(
        r=values,
        theta=labels + [labels[0]],
        fill="toself",
        fillcolor="rgba(79,156,249,.25)",
        line_color="#4f9cf9",
        name="平均シグナル",
    ))
    fig_radar.update_layout(
        title="5 シグナル平均（レーダー）",
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(
                visible=True,
                range=[-1, 1],
                tickfont_color="#cdd6f4",
                gridcolor="rgba(255,255,255,.1)",
            ),
            angularaxis=dict(
                tickfont_color="#cdd6f4",
                gridcolor="rgba(255,255,255,.15)",
            ),
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#cdd6f4",
        margin=dict(l=20, r=20, t=50, b=20),
    )
    st.plotly_chart(fig_radar, use_container_width=True)

with col_b:
    # ── ティッカー × 判定 ヒートマップ ──────────────────────────
    pivot = (
        df.groupby(["ticker", "decision"])
          .size()
          .reset_index(name="count")
    )

    fig_heat = px.density_heatmap(
        df,
        x="ticker",
        y="decision",
        title="ティッカー × 判定 ヒートマップ",
        color_continuous_scale="Blues",
        labels={"ticker": "銘柄", "decision": "判定"},
        nbinsx=len(df["ticker"].unique()),
        nbinsy=len(df["decision"].unique()),
    )
    # 右側にシグナル折れ線グラフを追加（各シグナルの平均時系列）
    sig_ts = (
        df.groupby("date")[list(sig_cols.values())]
          .mean()
          .reset_index()
    )

    if len(sig_ts) > 1:
        fig_sig = px.line(
            sig_ts.melt(id_vars="date", var_name="signal", value_name="value"),
            x="date",
            y="value",
            color="signal",
            title="シグナル平均の時系列推移",
            labels={"date": "日付", "value": "平均シグナル", "signal": "シグナル"},
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig_sig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,.3)")
        fig_sig.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cdd6f4",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(l=10, r=10, t=50, b=10),
            yaxis=dict(range=[-1.1, 1.1],
                       showgrid=True, gridcolor="rgba(255,255,255,.08)"),
            xaxis=dict(showgrid=False),
        )
        st.plotly_chart(fig_sig, use_container_width=True)
    else:
        # データが1日分しかない場合はシンプルなバーチャート
        sig_bar_data = pd.DataFrame({
            "シグナル": list(sig_cols.keys()),
            "値": [df[v].mean() for v in sig_cols.values()],
        })
        fig_sig_bar = px.bar(
            sig_bar_data,
            x="シグナル",
            y="値",
            title="シグナル平均（直近）",
            color="値",
            color_continuous_scale=["#f38ba8", "#cdd6f4", "#00c49f"],
            range_color=[-1, 1],
        )
        fig_sig_bar.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,.4)")
        fig_sig_bar.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cdd6f4",
            margin=dict(l=10, r=10, t=50, b=10),
            xaxis=dict(showgrid=False),
            yaxis=dict(range=[-1.1, 1.1],
                       showgrid=True, gridcolor="rgba(255,255,255,.08)"),
            coloraxis_showscale=False,
        )
        st.plotly_chart(fig_sig_bar, use_container_width=True)

# ================================================================
# 勝敗分析（ラベルが付いたレコードのみ）
# ================================================================

if not labelled.empty:
    st.markdown('<div class="section-title">🏆 勝敗分析（バックテスト）</div>',
                unsafe_allow_html=True)

    cl, cr = st.columns(2, gap="large")
    with cl:
        outcome_counts = labelled["outcome_label"].value_counts().reset_index()
        outcome_counts.columns = ["outcome", "count"]
        fig_pie = px.pie(
            outcome_counts,
            names="outcome",
            values="count",
            title="勝敗分布",
            color="outcome",
            color_discrete_map={"WIN": "#00c49f", "LOSS": "#f38ba8"},
            hole=0.5,
        )
        fig_pie.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cdd6f4",
            margin=dict(l=10, r=10, t=50, b=10),
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    with cr:
        fig_outcome_score = px.box(
            labelled,
            x="outcome_label",
            y="score",
            color="outcome_label",
            color_discrete_map={"WIN": "#00c49f", "LOSS": "#f38ba8"},
            title="勝敗 × スコア分布",
            points="all",
            labels={"outcome_label": "結果", "score": "加重スコア"},
        )
        fig_outcome_score.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cdd6f4",
            margin=dict(l=10, r=10, t=50, b=10),
            showlegend=False,
        )
        st.plotly_chart(fig_outcome_score, use_container_width=True)

# ================================================================
# インタラクティブ履歴テーブル
# ================================================================

st.markdown('<div class="section-title">📋 分析履歴テーブル</div>',
            unsafe_allow_html=True)

# 表示カラムと表示名のマッピング
display_cols = {
    "date":           "日付",
    "ticker":         "銘柄",
    "decision":       "判定",
    "score":          "スコア",
    "fa_trend":       "FA",
    "tech_trend":     "Tech",
    "macro_trend":    "Macro",
    "sig_news":       "News信号",
    "sig_social":     "SNS信号",
    "social_hype":    "Hype",
    "gate_skipped":   "Gate HOLD",
    "macro_brake":    "MacroBrake",
    "hype_penalty":   "HypePenalty",
    "outcome_label":  "勝敗",
    "mock_mode":      "Mock",
    "rationale":      "根拠（要約）",
}

df_display = df[list(display_cols.keys())].rename(columns=display_cols).copy()
df_display["日付"] = df_display["日付"].dt.strftime("%Y-%m-%d").fillna("")
df_display["スコア"] = df_display["スコア"].map("{:+.3f}".format)
df_display["News信号"] = df_display["News信号"].map("{:+.2f}".format)
df_display["SNS信号"]  = df_display["SNS信号"].map("{:+.2f}".format)
df_display["Hype"]    = df_display["Hype"].map("{:.2f}".format)
df_display["根拠（要約）"] = df_display["根拠（要約）"].str[:80]

# テーブル表示（ハイライト関数）
def _color_decision(val: str) -> str:
    if val == "STRONG BUY":
        return "background-color: rgba(0,196,159,.18); color: #00c49f; font-weight: bold"
    if val == "HOLD":
        return "background-color: rgba(79,156,249,.1); color: #4f9cf9"
    return ""

def _color_outcome(val: str) -> str:
    if val == "WIN":
        return "color: #00c49f; font-weight: bold"
    if val == "LOSS":
        return "color: #f38ba8; font-weight: bold"
    return "color: #888"

styled = (
    df_display.style
    .map(_color_decision, subset=["判定"])
    .map(_color_outcome,  subset=["勝敗"])
)

st.dataframe(
    styled,
    use_container_width=True,
    height=min(400, 50 + 35 * len(df_display)),
    hide_index=True,
)

# ================================================================
# フッタ
# ================================================================

st.markdown("---")
st.caption(
    f"ECC Autonomous Fund Dashboard  |  "
    f"表示レコード: {len(df):,} / {len(df_all):,}  |  "
    f"データソース: `{DATA_PATH}`"
)
