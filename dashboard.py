"""
ECC スイングトレード自律エンジン — 投資判断ダッシュボード

起動:
    streamlit run dashboard.py
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

# ================================================================
# ページ設定（最初に呼ぶ）
# ================================================================

st.set_page_config(
    page_title="🤖 Exa-Investor Dashboard",
    page_icon="🤖",
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
    padding: 1.4rem 2rem;
    margin-bottom: 1.2rem;
    color: white;
}
.banner h1 { margin: 0; font-size: 2rem; letter-spacing: -0.5px; }
.banner p  { margin: 0.3rem 0 0; opacity: .65; font-size: .9rem; }

/* Metric カード数値を大きく */
[data-testid="stMetricValue"] { font-size: 2rem !important; font-weight: 700 !important; }
[data-testid="stMetricDelta"] { font-size: .82rem !important; }

/* セクション見出し */
.section-title {
    font-size: 1.05rem;
    font-weight: 700;
    color: #4f9cf9;
    border-left: 4px solid #4f9cf9;
    padding-left: .6rem;
    margin: 1.4rem 0 .8rem;
}

/* エージェントバッジ */
.agent-badge {
    display: inline-block;
    padding: .18rem .7rem;
    border-radius: 20px;
    font-size: .78rem;
    font-weight: 600;
    letter-spacing: .4px;
}
.badge-news       { background:#1e3a5f; color:#7ec8e3; }
.badge-technical  { background:#1a3d2b; color:#7ae582; }
.badge-macro      { background:#3b2e1f; color:#f4a261; }
.badge-social     { background:#3b1f3b; color:#da77f2; }
.badge-fundamental{ background:#2c2c1a; color:#f4d35e; }
.badge-manager    { background:#1f2c3b; color:#4f9cf9; }

/* 判断カラム区切り */
.divider-line { border-top: 1px solid rgba(255,255,255,.08); margin: 1rem 0; }

/* スコアバーのシンプル装飾 */
.score-bar-wrap { font-family: monospace; color: #4f9cf9; letter-spacing: 0; }
</style>
""", unsafe_allow_html=True)

# ================================================================
# ヘッダバナー
# ================================================================

st.markdown("""
<div class="banner">
  <h1>🤖 Exa-Investor Dashboard</h1>
  <p>Autonomous Financial Multi-Agent System — 5-Agent Consensus Architecture</p>
</div>
""", unsafe_allow_html=True)

# ================================================================
# データ読み込み
# ================================================================

DATA_PATH = Path("data/training/training_data.jsonl")


@st.cache_data(ttl=30)
def load_data(path: Path) -> pd.DataFrame:
    """JSONL を集計用 DataFrame に変換する。"""
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

            cot  = raw.get("manager_chain_of_thought") or {}
            out  = raw.get("manager_output") or {}
            inp  = raw.get("inputs") or {}
            sigs = cot.get("signals") or {}

            records.append({
                "record_id":       raw.get("record_id", ""),
                "session_id":      raw.get("session_id", ""),
                "date":            raw.get("date", ""),
                "created_at":      raw.get("created_at", ""),
                "ticker":          raw.get("ticker", ""),
                "mock_mode":       raw.get("mock_mode", False),
                "hybrid_mode":     raw.get("hybrid_mode", False),
                "decision":        out.get("decision", "HOLD"),
                "score":           float(cot.get("weighted_score") or out.get("score") or 0.0),
                "threshold":       float(cot.get("threshold", 0.6)),
                "is_strong_buy":   bool(out.get("is_strong_buy", False)),
                "gate_skipped":    bool(cot.get("gate_skipped", False)),
                "macro_brake":     bool(cot.get("macro_forced_hold", False)),
                "hype_penalty":    bool(cot.get("social_hype_penalty", False)),
                "rationale":       cot.get("rationale", ""),
                "sig_fundamental": float(sigs.get("fundamental", 0.0)),
                "sig_technical":   float(sigs.get("technical", 0.0)),
                "sig_news":        float(sigs.get("news", 0.0)),
                "sig_macro":       float(sigs.get("macro", 0.0)),
                "sig_social":      float(sigs.get("social", 0.0)),
                "fa_trend":    (inp.get("fundamental_analysis") or {}).get("trend", "neutral"),
                "tech_trend":  (inp.get("technical_analysis")   or {}).get("trend", "neutral"),
                "news_score":  float((inp.get("news_analysis")  or {}).get("avg_sentiment_score", 0.0)),
                "macro_trend": (inp.get("macro_analysis")       or {}).get("trend", "neutral"),
                "social_hype": float((inp.get("social_analysis") or {}).get("hype_score", 0.0)),
                "outcome":       raw.get("outcome"),
                "outcome_label": raw.get("outcome_label"),
            })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=30)
def load_raw_data(path: Path) -> list[dict]:
    """JSONL を生 dict リストで返す（エージェント会議録用）。"""
    records: list[dict] = []
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


PORTFOLIO_PATH    = Path("data/portfolio.json")
AGENT_STATUS_PATH = Path("data/agent_status.json")
_AGENT_STALE_MINUTES = 30


@st.cache_data(ttl=30)
def load_portfolio() -> list[dict]:
    if not PORTFOLIO_PATH.exists():
        return []
    try:
        data = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
        return [p for p in data.get("positions", []) if p.get("status") == "OPEN"]
    except Exception:
        return []


@st.cache_data(ttl=30)
def load_agent_status() -> dict:
    if not AGENT_STATUS_PATH.exists():
        return {}
    try:
        return json.loads(AGENT_STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


@st.cache_data(ttl=60)
def fetch_current_prices(tickers: tuple[str, ...]) -> dict[str, float]:
    """yfinance で現在の終値を取得する。失敗した銘柄は除外。"""
    if not tickers:
        return {}
    prices: dict[str, float] = {}
    try:
        raw = yf.download(
            list(tickers), period="1d", progress=False, auto_adjust=True
        )
        close = raw["Close"] if "Close" in raw.columns else raw
        if hasattr(close, "columns"):
            for t in tickers:
                if t in close.columns:
                    val = close[t].dropna()
                    if not val.empty:
                        prices[t] = float(val.iloc[-1])
        else:
            t = tickers[0]
            val = close.dropna()
            if not val.empty:
                prices[t] = float(val.iloc[-1])
    except Exception:
        pass
    return prices


df_all  = load_data(DATA_PATH)
raw_all = load_raw_data(DATA_PATH)

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

    if st.button("🔄 最新データを取得", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    auto_refresh    = st.checkbox("⏱ 自動更新", value=False)
    refresh_secs    = st.slider("更新間隔（秒）", 30, 300, 60, step=10,
                                disabled=not auto_refresh)

    st.divider()

    all_tickers   = sorted(df_all["ticker"].dropna().unique().tolist())
    sel_tickers   = st.multiselect("銘柄（Ticker）", options=all_tickers,
                                   default=all_tickers, placeholder="銘柄を選択...")
    all_decisions = sorted(df_all["decision"].dropna().unique().tolist())
    sel_decisions = st.multiselect("判定（Decision）", options=all_decisions,
                                   default=all_decisions, placeholder="判定を選択...")

    st.divider()
    show_mock   = st.checkbox("モックデータを含む",       value=True)
    show_hybrid = st.checkbox("ハイブリッドデータを含む", value=True)

    st.divider()
    if df_all["date"].notna().any():
        min_date  = df_all["date"].min().date()
        max_date  = df_all["date"].max().date()
        date_range = st.date_input("日付範囲", value=(min_date, max_date),
                                   min_value=min_date, max_value=max_date)
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
# ヘルパー関数
# ================================================================

_DECISION_COLOR = {
    "STRONG BUY": "#00c49f",
    "HOLD":       "#4f9cf9",
    "SELL":       "#f38ba8",
}

_CHART_THEME = dict(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font_color="#cdd6f4",
    margin=dict(l=10, r=10, t=50, b=10),
)


def _sentiment_badge(val: str) -> str:
    v = (val or "").lower()
    if v in ("positive", "ポジティブ", "強気", "buy"):
        return "🟢 Positive"
    if v in ("negative", "ネガティブ", "弱気", "sell"):
        return "🔴 Negative"
    return "🟡 Neutral"


def _decision_callout(decision: str, score: float, rationale: str = "") -> None:
    """ManagerAgent の最終判断を色付きコールアウトで表示する。"""
    label = f"### {'🚀' if decision == 'STRONG BUY' else '⏸' if decision == 'HOLD' else '🔻'} {decision}   (確信度スコア: **{score:+.3f}**)"
    if decision == "STRONG BUY":
        st.success(label)
    elif decision == "HOLD":
        st.info(label)
    else:
        st.warning(label)
    if rationale:
        st.caption(f"根拠: {rationale}")


# ================================================================
# メインタブ
# ================================================================

tab0, tab1, tab2, tab3 = st.tabs([
    "💼 ポートフォリオ & エージェント健全性",
    "📊 総合評価（サマリー）",
    "🤖 エージェント会議録",
    "⚙️ システムログ・生データ",
])

# ────────────────────────────────────────────────────────────────
# Tab 0 : ポートフォリオ P&L & エージェント健全性
# ────────────────────────────────────────────────────────────────

with tab0:
    # ── ライブ P&L ────────────────────────────────────────────────
    st.markdown('<div class="section-title">💼 ライブポートフォリオ P&L</div>',
                unsafe_allow_html=True)

    open_positions = load_portfolio()
    if not open_positions:
        st.info("現在 OPEN ポジションはありません。")
    else:
        tickers_open = tuple(p["ticker"] for p in open_positions)
        current_prices = fetch_current_prices(tickers_open)

        pnl_rows = []
        total_pnl = 0.0
        total_cost = 0.0
        for pos in open_positions:
            ticker      = pos["ticker"]
            entry       = float(pos.get("entry_price", 0))
            shares      = int(pos.get("shares", 0))
            current     = current_prices.get(ticker, entry)
            cost_basis  = entry * shares
            market_val  = current * shares
            unreal_pnl  = market_val - cost_basis
            unreal_pct  = (current - entry) / entry * 100 if entry else 0.0
            target      = pos.get("target_price")
            stop        = pos.get("stop_loss_price")

            total_pnl  += unreal_pnl
            total_cost += cost_basis

            pnl_rows.append({
                "銘柄":     ticker,
                "エントリ": f"${entry:.2f}",
                "現在値":   f"${current:.2f}",
                "株数":     shares,
                "コスト":   f"${cost_basis:,.0f}",
                "時価":     f"${market_val:,.0f}",
                "含み損益": f"{unreal_pnl:+,.0f}",
                "損益%":    f"{unreal_pct:+.2f}%",
                "目標値":   f"${target:.2f}" if target else "—",
                "損切値":   f"${stop:.2f}"   if stop   else "—",
            })

        total_pct = total_pnl / total_cost * 100 if total_cost else 0.0

        c1, c2, c3 = st.columns(3)
        c1.metric("保有ポジション数", f"{len(open_positions)} 件")
        c2.metric("総コスト",         f"${total_cost:,.0f}")
        c3.metric("含み損益合計",
                  f"${total_pnl:+,.0f}",
                  delta=f"{total_pct:+.2f}%",
                  delta_color="normal" if total_pnl >= 0 else "inverse")

        def _color_pnl(val: str) -> str:
            v = val.replace(",", "").replace("$", "").replace("%", "")
            try:
                return ("color:#00c49f; font-weight:bold" if float(v) >= 0
                        else "color:#f38ba8; font-weight:bold")
            except ValueError:
                return ""

        pnl_df = pd.DataFrame(pnl_rows)
        st.dataframe(
            pnl_df.style.map(_color_pnl, subset=["含み損益", "損益%"]),
            use_container_width=True,
            hide_index=True,
        )

    st.markdown('<div class="divider-line"></div>', unsafe_allow_html=True)

    # ── エージェント健全性 ────────────────────────────────────────
    st.markdown('<div class="section-title">🤖 エージェント健全性ステータス</div>',
                unsafe_allow_html=True)

    agent_status = load_agent_status()
    if not agent_status:
        st.info("data/agent_status.json が見つかりません。")
    else:
        now_utc = datetime.now(timezone.utc)
        cols = st.columns(len(agent_status))
        for col, (agent_name, info) in zip(cols, agent_status.items()):
            status     = info.get("status", "UNKNOWN")
            win_rate   = info.get("win_rate")
            last_eval  = info.get("last_evaluated", "")

            # 30分以上評価されていない場合は STALE 扱い
            is_stale = False
            if last_eval:
                try:
                    le_dt = datetime.fromisoformat(last_eval)
                    if le_dt.tzinfo is None:
                        le_dt = le_dt.replace(tzinfo=timezone.utc)
                    delta_min = (now_utc - le_dt).total_seconds() / 60
                    is_stale = delta_min > _AGENT_STALE_MINUTES
                except Exception:
                    is_stale = True

            badge_color = (
                "🔴" if status == "SUSPENDED" or is_stale
                else "🟢" if status == "ACTIVE"
                else "🟡"
            )
            label = f"{badge_color} {agent_name.replace('Agent', '')}"
            wr    = f"{win_rate:.1%}" if win_rate is not None else "N/A"
            delta_label = ("⚠️ STALE" if is_stale
                           else status)

            col.metric(label, wr, delta=delta_label,
                       delta_color="off" if not is_stale else "inverse")

        # 詳細テーブル
        rows = []
        for agent_name, info in agent_status.items():
            rows.append({
                "エージェント": agent_name,
                "ステータス":   info.get("status", "?"),
                "勝率":         f"{info['win_rate']:.1%}" if info.get("win_rate") is not None else "N/A",
                "取引数":       info.get("total_trades", 0),
                "最終評価":     (info.get("last_evaluated") or "")[:16],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ────────────────────────────────────────────────────────────────
# Tab 1 : 総合評価
# ────────────────────────────────────────────────────────────────

with tab1:
    # ── KPI メトリクス ────────────────────────────────────────────
    st.markdown('<div class="section-title">📊 KPI サマリー</div>', unsafe_allow_html=True)

    total         = len(df)
    strong_buy_n  = df["is_strong_buy"].sum()
    sb_rate       = strong_buy_n / total * 100 if total else 0
    avg_score     = df["score"].mean()
    gate_skip_n   = df["gate_skipped"].sum()
    macro_brake_n = df["macro_brake"].sum()

    labelled = df[df["outcome_label"].notna()]
    win_n    = (labelled["outcome_label"] == "WIN").sum()
    win_rate = win_n / len(labelled) * 100 if len(labelled) > 0 else None

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("総分析数",       f"{total:,}")
    c2.metric("STRONG BUY",    f"{strong_buy_n:,}",
              delta=f"{sb_rate:.1f}% of total",
              delta_color="normal" if sb_rate >= 30 else "off")
    c3.metric("勝率 (Win Rate)",
              f"{win_rate:.1f}%" if win_rate is not None else "N/A",
              delta=f"{win_n}/{len(labelled)} 件" if len(labelled) > 0 else "未評価",
              delta_color="normal" if (win_rate or 0) >= 55 else "off")
    c4.metric("平均スコア",     f"{avg_score:+.3f}")
    c5.metric("Gate HOLD 数",  f"{gate_skip_n:,}",
              delta=f"macro brake: {macro_brake_n} 件",
              delta_color="inverse" if macro_brake_n > 0 else "off")
    c6.metric("Hype ペナルティ", f"{df['hype_penalty'].sum():,}",
              delta="ソーシャル補正あり", delta_color="off")

    # ── 最新レコードの判断ハイライト ─────────────────────────────
    latest = df.sort_values("created_at", ascending=False).iloc[0]
    st.markdown('<div class="section-title">🔔 最新の AI 判断</div>', unsafe_allow_html=True)
    col_l, col_r = st.columns([2, 3])
    with col_l:
        _decision_callout(
            latest["decision"],
            latest["score"],
            str(latest["rationale"])[:120],
        )
    with col_r:
        st.markdown(f"""
| 項目 | 値 |
|---|---|
| 銘柄 | **{latest["ticker"]}** |
| 日付 | {str(latest["date"])[:10]} |
| FA シグナル | {_sentiment_badge(latest["fa_trend"])} |
| テクニカル  | {_sentiment_badge(latest["tech_trend"])} |
| マクロ      | {_sentiment_badge(latest["macro_trend"])} |
| Hype スコア | {latest["social_hype"]:.2f} |
""")

    st.markdown('<div class="divider-line"></div>', unsafe_allow_html=True)

    # ── チャート：日次推移 & スコア分布 ──────────────────────────
    st.markdown('<div class="section-title">📈 分析推移 & スコア分布</div>', unsafe_allow_html=True)

    col_left, col_right = st.columns([3, 2], gap="large")

    with col_left:
        daily = (
            df.groupby(["date", "decision"])
              .size()
              .reset_index(name="count")
        )
        daily["date_str"] = daily["date"].dt.strftime("%Y-%m-%d")

        fig_daily = px.bar(
            daily, x="date_str", y="count", color="decision",
            color_discrete_map=_DECISION_COLOR,
            title="日ごとの分析実行推移（判定別）",
            labels={"date_str": "日付", "count": "件数", "decision": "判定"},
            text="count",
        )
        fig_daily.update_layout(
            **_CHART_THEME,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            xaxis=dict(showgrid=False),
            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,.08)"),
        )
        fig_daily.update_traces(textposition="outside")
        st.plotly_chart(fig_daily, use_container_width=True)

    with col_right:
        threshold = float(df["threshold"].iloc[0]) if not df.empty else 0.6
        fig_hist = px.histogram(
            df, x="score", color="decision",
            color_discrete_map=_DECISION_COLOR,
            nbins=20,
            title="AI 確信度（加重スコア）分布",
            labels={"score": "加重スコア", "count": "件数", "decision": "判定"},
            opacity=0.85,
        )
        fig_hist.add_vline(x=threshold, line_dash="dash", line_color="#f38ba8",
                           annotation_text=f"閾値 {threshold}",
                           annotation_position="top right",
                           annotation_font_color="#f38ba8")
        fig_hist.update_layout(
            **_CHART_THEME,
            barmode="overlay",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            xaxis=dict(showgrid=False),
            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,.08)"),
        )
        st.plotly_chart(fig_hist, use_container_width=True)

    # ── 勝敗分析 ─────────────────────────────────────────────────
    if not labelled.empty:
        st.markdown('<div class="section-title">🏆 勝敗分析（バックテスト）</div>',
                    unsafe_allow_html=True)

        cl, cr = st.columns(2, gap="large")
        with cl:
            outcome_counts = labelled["outcome_label"].value_counts().reset_index()
            outcome_counts.columns = ["outcome", "count"]
            fig_pie = px.pie(
                outcome_counts, names="outcome", values="count",
                title="勝敗分布",
                color="outcome",
                color_discrete_map={"WIN": "#00c49f", "LOSS": "#f38ba8"},
                hole=0.5,
            )
            fig_pie.update_layout(**_CHART_THEME)
            st.plotly_chart(fig_pie, use_container_width=True)

        with cr:
            fig_box = px.box(
                labelled, x="outcome_label", y="score",
                color="outcome_label",
                color_discrete_map={"WIN": "#00c49f", "LOSS": "#f38ba8"},
                title="勝敗 × スコア分布",
                points="all",
                labels={"outcome_label": "結果", "score": "加重スコア"},
            )
            fig_box.update_layout(**_CHART_THEME, showlegend=False)
            st.plotly_chart(fig_box, use_container_width=True)

# ────────────────────────────────────────────────────────────────
# Tab 2 : エージェント会議録
# ────────────────────────────────────────────────────────────────

with tab2:
    st.markdown('<div class="section-title">🤖 エージェント会議録</div>',
                unsafe_allow_html=True)

    if not raw_all:
        st.warning("会議録データが存在しません。")
    else:
        # ── セッション選択 ────────────────────────────────────────
        session_labels = [
            f"{r.get('date','?')}  {r.get('ticker','?')}  [{r.get('session_id','?')[:14]}]"
            for r in raw_all
        ]
        sel_idx = st.selectbox(
            "分析セッションを選択",
            options=range(len(raw_all)),
            format_func=lambda i: session_labels[i],
            index=len(raw_all) - 1,  # 最新レコードをデフォルト
        )

        rec  = raw_all[sel_idx]
        inp  = rec.get("inputs") or {}
        cot  = rec.get("manager_chain_of_thought") or {}
        out  = rec.get("manager_output") or {}
        sigs = cot.get("signals") or {}
        sig_reasons = cot.get("signal_reasons") or {}
        weights     = cot.get("weights") or {}

        # ── セッション基本情報 ────────────────────────────────────
        col_inf1, col_inf2, col_inf3, col_inf4 = st.columns(4)
        col_inf1.metric("銘柄",     rec.get("ticker", "—"))
        col_inf2.metric("日付",     str(rec.get("date", "—"))[:10])
        col_inf3.metric("セッション", str(rec.get("session_id", "—"))[:14])
        col_inf4.metric("モード",
                         "Mock" if rec.get("mock_mode") else "Live" +
                         (" + Hybrid" if rec.get("hybrid_mode") else ""))

        st.markdown('<div class="divider-line"></div>', unsafe_allow_html=True)
        st.markdown("#### 📋 各エージェントの分析結果")

        # ── NewsAgent ────────────────────────────────────────────
        news = inp.get("news_analysis") or {}
        with st.chat_message("NewsAgent", avatar="📰"):
            st.markdown(
                '<span class="agent-badge badge-news">📰 NewsAgent</span>',
                unsafe_allow_html=True,
            )
            news_sig = sigs.get("news", 0.0)
            articles = news.get("articles") or []
            col_n1, col_n2 = st.columns([1, 3])
            col_n1.metric("シグナル", f"{news_sig:+.2f}",
                          delta=_sentiment_badge(sig_reasons.get("news", "")),
                          delta_color="off")
            with col_n2:
                if articles:
                    for a in articles[:3]:
                        icon = "🟢" if a.get("sentiment") == "positive" else \
                               "🔴" if a.get("sentiment") == "negative" else "🟡"
                        st.markdown(f"{icon} **{a.get('title', 'N/A')}**")
                        if a.get("reason"):
                            st.caption(f"  → {a['reason']}")
                else:
                    st.markdown(f"センチメント: {_sentiment_badge(sig_reasons.get('news', 'neutral'))}")
            avg_score_news = news.get("avg_sentiment_score")
            if avg_score_news is not None:
                st.caption(f"平均センチメントスコア: `{avg_score_news:.2f}`  /  "
                           f"Weight: `{weights.get('news', 0):.0%}`")

        # ── TechnicalAgent ────────────────────────────────────────
        tech = inp.get("technical_analysis") or {}
        with st.chat_message("TechnicalAgent", avatar="📈"):
            st.markdown(
                '<span class="agent-badge badge-technical">📈 TechnicalAgent</span>',
                unsafe_allow_html=True,
            )
            tech_sig = sigs.get("technical", 0.0)
            col_t1, col_t2 = st.columns([1, 3])
            col_t1.metric("シグナル", f"{tech_sig:+.2f}",
                          delta=_sentiment_badge(tech.get("trend", "")),
                          delta_color="off")
            col_t2.markdown(f"トレンド: {_sentiment_badge(tech.get('trend', 'neutral'))}")
            st.caption(f"Weight: `{weights.get('technical', 0):.0%}`")
            tech_reason = tech.get("trend_reason") or ""
            if tech_reason:
                with st.expander("🔍 テクニカル分析の詳細（Chain-of-Thought）"):
                    st.markdown(tech_reason)

        # ── MacroAgent ────────────────────────────────────────────
        macro = inp.get("macro_analysis") or {}
        with st.chat_message("MacroAgent", avatar="🌍"):
            st.markdown(
                '<span class="agent-badge badge-macro">🌍 MacroAgent</span>',
                unsafe_allow_html=True,
            )
            macro_sig = sigs.get("macro", 0.0)
            col_m1, col_m2 = st.columns([1, 3])
            col_m1.metric("シグナル", f"{macro_sig:+.2f}",
                          delta=_sentiment_badge(macro.get("trend", "")),
                          delta_color="off")
            col_m2.markdown(f"トレンド: {_sentiment_badge(macro.get('trend', 'neutral'))}")
            if cot.get("macro_forced_hold"):
                st.warning("⚠️ Macro ブレーキ発動 — マクロ悪化により HOLD を強制")
            st.caption(f"Weight: `{weights.get('macro', 0):.0%}`")
            macro_reason = macro.get("trend_reason") or ""
            if macro_reason:
                with st.expander("🔍 マクロ分析の詳細（Chain-of-Thought）"):
                    st.markdown(macro_reason)

        # ── SocialAgent ───────────────────────────────────────────
        social = inp.get("social_analysis") or {}
        with st.chat_message("SocialAgent", avatar="💬"):
            st.markdown(
                '<span class="agent-badge badge-social">💬 SocialAgent</span>',
                unsafe_allow_html=True,
            )
            social_sig = sigs.get("social", 0.0)
            col_s1, col_s2 = st.columns([1, 3])
            col_s1.metric("シグナル", f"{social_sig:+.2f}",
                          delta=f"Hype: {social.get('hype_score', 0.0):.2f}",
                          delta_color="off")
            col_s2.markdown(f"Posts: `{social.get('post_count', '?')}` | Source: `{social.get('source', '?')}`")
            if cot.get("social_hype_penalty"):
                st.warning("⚠️ Hype ペナルティ適用 — SNS 過熱によりスコア減算")
            st.caption(f"Weight: `{weights.get('social', 0):.0%}`")
            social_reason = social.get("reason") or ""
            if social_reason:
                with st.expander("🔍 ソーシャル分析の詳細（Chain-of-Thought）"):
                    st.markdown(social_reason)

        # ── FundamentalAgent ──────────────────────────────────────
        fa = inp.get("fundamental_analysis") or {}
        with st.chat_message("FundamentalAgent", avatar="🔬"):
            st.markdown(
                '<span class="agent-badge badge-fundamental">🔬 FundamentalAgent</span>',
                unsafe_allow_html=True,
            )
            fa_sig = sigs.get("fundamental", 0.0)
            col_f1, col_f2 = st.columns([1, 3])
            col_f1.metric("シグナル", f"{fa_sig:+.2f}",
                          delta=_sentiment_badge(fa.get("trend", "")),
                          delta_color="off")
            col_f2.markdown(fa.get("trend_reason") or "— データなし —")
            st.caption(f"Weight: `{weights.get('fundamental', 0):.0%}`  |  "
                       f"データ有無: `{'✅' if fa.get('data_available') else '❌'}`")

            # CoT 詳細は折りたたみ
            analyses = fa.get("analyses") or []
            full_reason = fa.get("trend_reason", "")
            if analyses or len(full_reason) > 100:
                with st.expander("🔍 詳細な推論プロセス（Chain-of-Thought）を表示"):
                    if full_reason:
                        st.markdown(f"**推論サマリー:**\n\n{full_reason}")
                    if analyses:
                        st.markdown("**RAG 検索結果:**")
                        for i, chunk in enumerate(analyses[:5], 1):
                            st.markdown(f"**[{i}]** {str(chunk)[:300]}")

        st.markdown('<div class="divider-line"></div>', unsafe_allow_html=True)

        # ── ManagerAgent 最終判断 ─────────────────────────────────
        with st.chat_message("ManagerAgent", avatar="🤖"):
            st.markdown(
                '<span class="agent-badge badge-manager">🤖 ManagerAgent — 最終判断</span>',
                unsafe_allow_html=True,
            )

            decision   = out.get("decision", "HOLD")
            final_score = float(out.get("score") or cot.get("weighted_score") or 0.0)
            rationale   = cot.get("rationale", "")

            _decision_callout(decision, final_score, rationale[:120] if rationale else "")
            if len(rationale) > 120:
                with st.expander("📝 最終判断の完全な根拠（Rationale）を表示"):
                    st.markdown(rationale)

            # スコア内訳の可視化
            st.markdown("**シグナル加重スコア内訳:**")
            sig_breakdown = []
            sig_name_map = {
                "fundamental": ("🔬 ファンダメンタル", "badge-fundamental"),
                "technical":   ("📈 テクニカル",       "badge-technical"),
                "macro":       ("🌍 マクロ",            "badge-macro"),
                "news":        ("📰 ニュース",          "badge-news"),
                "social":      ("💬 ソーシャル",        "badge-social"),
            }
            for key, (label, _badge) in sig_name_map.items():
                sig_v = sigs.get(key, 0.0)
                w_v   = weights.get(key, 0.0)
                contrib = sig_v * w_v
                bar_len = int(abs(contrib) / 0.5 * 15)
                bar     = "█" * bar_len + "░" * (15 - bar_len)
                sig_breakdown.append({
                    "エージェント": label,
                    "シグナル":     f"{sig_v:+.2f}",
                    "ウェイト":     f"{w_v:.0%}",
                    "寄与":         f"{contrib:+.4f}",
                    "バー":         f"`{bar}`",
                })
            st.table(pd.DataFrame(sig_breakdown))

            conds = cot.get("strong_buy_conditions_check") or {}
            if conds:
                with st.expander("✅ STRONG BUY 条件チェック"):
                    for cond_key, cond_val in conds.items():
                        icon = "✅" if cond_val else "❌"
                        st.markdown(f"{icon} `{cond_key}`")

# ────────────────────────────────────────────────────────────────
# Tab 3 : システムログ・生データ
# ────────────────────────────────────────────────────────────────

with tab3:
    # ── システム稼働ステータス ──────────────────────────────────────
    st.markdown('<div class="section-title">🖥️ システム稼働ステータス</div>',
                unsafe_allow_html=True)

    LOG_PATH      = Path("bot_log.txt")
    CRON_LOG_PATH = Path("cron.log")

    _col_s1, _col_s2, _col_s3 = st.columns(3)

    if LOG_PATH.exists():
        _mtime = datetime.fromtimestamp(LOG_PATH.stat().st_mtime).strftime("%m/%d %H:%M")
        _col_s1.metric("ニュースモニター", "🟢 稼働中", delta=f"最終更新: {_mtime}", delta_color="off")
    else:
        _col_s1.metric("ニュースモニター", "🔴 停止", delta="bot_log.txt なし", delta_color="off")

    if CRON_LOG_PATH.exists():
        _cmtime = datetime.fromtimestamp(CRON_LOG_PATH.stat().st_mtime).strftime("%m/%d %H:%M")
        _col_s2.metric("パイプライン（cron）", "🟢 ログあり", delta=f"最終実行: {_cmtime}", delta_color="off")
    else:
        _col_s2.metric("パイプライン（cron）", "🟡 未実行", delta="月〜金 22:30 のみ実行", delta_color="off")

    _training_path = Path("data/training/training_data.jsonl")
    if _training_path.exists():
        _tmtime = datetime.fromtimestamp(_training_path.stat().st_mtime).strftime("%m/%d %H:%M")
        _col_s3.metric("学習データ", "🟢 最新", delta=f"更新: {_tmtime}", delta_color="off")
    else:
        _col_s3.metric("学習データ", "❌ なし", delta_color="off")

    # ── ログビューア ───────────────────────────────────────────────
    st.markdown('<div class="section-title">📜 リアルタイムログ</div>',
                unsafe_allow_html=True)

    _log_tab1, _log_tab2 = st.tabs(["📰 ニュースモニター (bot_log.txt)", "⚙️ パイプライン (cron.log)"])

    with _log_tab1:
        if LOG_PATH.exists():
            _lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            _n_lines = st.slider("表示行数", min_value=20, max_value=200, value=50, step=10,
                                  key="log_lines_bot")
            st.code("\n".join(_lines[-_n_lines:]), language="")
        else:
            st.info("bot_log.txt が見つかりません。")

    with _log_tab2:
        if CRON_LOG_PATH.exists():
            _cron_lines = CRON_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            _n_cron = st.slider("表示行数", min_value=20, max_value=200, value=50, step=10,
                                 key="log_lines_cron")
            st.code("\n".join(_cron_lines[-_n_cron:]), language="")
        else:
            st.info("cron.log が存在しません。パイプラインは月〜金 22:30 にのみ実行されます（今日は土曜日）。")

    st.markdown('<div class="divider-line"></div>', unsafe_allow_html=True)

    # ── シグナルレーダー & ヒートマップ ───────────────────────────
    st.markdown('<div class="section-title">🔬 シグナル詳細分析</div>',
                unsafe_allow_html=True)

    sig_cols = {
        "ファンダメンタル": "sig_fundamental",
        "テクニカル":       "sig_technical",
        "ニュース":         "sig_news",
        "マクロ":           "sig_macro",
        "ソーシャル":       "sig_social",
    }

    col_a, col_b = st.columns([2, 3], gap="large")

    with col_a:
        labels  = list(sig_cols.keys())
        values  = [df[v].mean() for v in sig_cols.values()]
        values += values[:1]

        fig_radar = go.Figure(go.Scatterpolar(
            r=values,
            theta=labels + [labels[0]],
            fill="toself",
            fillcolor="rgba(79,156,249,.25)",
            line_color="#4f9cf9",
            name="平均シグナル",
        ))
        fig_radar.update_layout(
            title="5 シグナル平均（レーダーチャート）",
            polar=dict(
                bgcolor="rgba(0,0,0,0)",
                radialaxis=dict(visible=True, range=[-1, 1],
                                tickfont_color="#cdd6f4",
                                gridcolor="rgba(255,255,255,.1)"),
                angularaxis=dict(tickfont_color="#cdd6f4",
                                 gridcolor="rgba(255,255,255,.15)"),
            ),
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#cdd6f4",
            margin=dict(l=20, r=20, t=50, b=20),
        )
        st.plotly_chart(fig_radar, use_container_width=True)

    with col_b:
        sig_ts = (
            df.groupby("date")[list(sig_cols.values())]
              .mean()
              .reset_index()
        )

        if len(sig_ts) > 1:
            fig_sig = px.line(
                sig_ts.melt(id_vars="date", var_name="signal", value_name="value"),
                x="date", y="value", color="signal",
                title="シグナル平均の時系列推移",
                labels={"date": "日付", "value": "平均シグナル", "signal": "シグナル"},
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig_sig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,.3)")
            fig_sig.update_layout(
                **_CHART_THEME,
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
                yaxis=dict(range=[-1.1, 1.1],
                           showgrid=True, gridcolor="rgba(255,255,255,.08)"),
                xaxis=dict(showgrid=False),
            )
            st.plotly_chart(fig_sig, use_container_width=True)
        else:
            sig_bar_data = pd.DataFrame({
                "シグナル": list(sig_cols.keys()),
                "値": [df[v].mean() for v in sig_cols.values()],
            })
            fig_sig_bar = px.bar(
                sig_bar_data, x="シグナル", y="値",
                title="シグナル平均（直近）",
                color="値",
                color_continuous_scale=["#f38ba8", "#cdd6f4", "#00c49f"],
                range_color=[-1, 1],
            )
            fig_sig_bar.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,.4)")
            fig_sig_bar.update_layout(
                **_CHART_THEME, coloraxis_showscale=False,
                xaxis=dict(showgrid=False),
                yaxis=dict(range=[-1.1, 1.1],
                           showgrid=True, gridcolor="rgba(255,255,255,.08)"),
            )
            st.plotly_chart(fig_sig_bar, use_container_width=True)

    # ── 分析履歴テーブル ─────────────────────────────────────────
    st.markdown('<div class="section-title">📋 分析履歴テーブル</div>',
                unsafe_allow_html=True)

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
    df_display["日付"]     = df_display["日付"].dt.strftime("%Y-%m-%d").fillna("")
    df_display["スコア"]   = df_display["スコア"].map("{:+.3f}".format)
    df_display["News信号"] = df_display["News信号"].map("{:+.2f}".format)
    df_display["SNS信号"]  = df_display["SNS信号"].map("{:+.2f}".format)
    df_display["Hype"]     = df_display["Hype"].map("{:.2f}".format)
    df_display["根拠（要約）"] = df_display["根拠（要約）"].str[:80]

    def _color_decision(val: str) -> str:
        if val == "STRONG BUY":
            return "background-color: rgba(0,196,159,.18); color:#00c49f; font-weight:bold"
        if val == "HOLD":
            return "background-color: rgba(79,156,249,.1); color:#4f9cf9"
        return ""

    def _color_outcome(val: str) -> str:
        if val == "WIN":
            return "color:#00c49f; font-weight:bold"
        if val == "LOSS":
            return "color:#f38ba8; font-weight:bold"
        return "color:#888"

    styled = (
        df_display.style
        .map(_color_decision, subset=["判定"])
        .map(_color_outcome,  subset=["勝敗"])
    )
    st.dataframe(styled, use_container_width=True,
                 height=min(420, 50 + 35 * len(df_display)), hide_index=True)

    # ── 生 JSON エクスポート ──────────────────────────────────────
    st.markdown('<div class="section-title">📦 生データ（JSON）エクスポート</div>',
                unsafe_allow_html=True)

    st.caption(f"フィルタ後レコード数: {len(df):,} 件")
    st.download_button(
        label="⬇️ フィルタ後データを CSV でダウンロード",
        data=df_display.to_csv(index=False).encode("utf-8-sig"),
        file_name="exa_investor_export.csv",
        mime="text/csv",
        use_container_width=True,
    )

    if st.toggle("📝 最新レコードの生 JSON を表示"):
        if raw_all:
            st.json(raw_all[-1])

# ================================================================
# フッタ
# ================================================================

st.markdown("---")
_now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
st.caption(
    f"🤖 Exa-Investor Dashboard  |  "
    f"表示レコード: {len(df):,} / {len(df_all):,}  |  "
    f"データソース: `{DATA_PATH}`  |  "
    f"最終更新: {_now_str}"
)

# ================================================================
# 自動更新（60秒デフォルト）
# ================================================================

if auto_refresh:
    time.sleep(refresh_secs)
    st.cache_data.clear()
    st.rerun()
