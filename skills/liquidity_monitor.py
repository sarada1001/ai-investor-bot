"""
Skill: liquidity_monitor
Permission: LiquidityAgent only

Verbal Technical Analysis (VTA) — 板情報・資金フローの定量分析と自然言語変換。

設計方針 (VTA論文準拠):
  生の時系列・数値配列をLLMに直接渡すのではなく、Python側で定量計算を行い
  「自然言語のテキスト（Verbal Annotation）」に変換してからBBSに書き込む。

計算指標:
  1. ask_ratio  : Ask合計数量 / (Ask+Bid合計)  → 板の圧力方向
  2. bid_ratio  : Bid合計数量 / (Ask+Bid合計)
  3. net_large_inflow : (超大口+大口)純流入額 (USD)  → 機関資金の方向
  4. net_small_inflow : 小口純流入額 (USD)  → 個人資金の方向

シグナルスコア (-1.0 〜 +1.0) 計算式:
  book_score = clamp((bid_ratio - 0.5) * 4,  -1.0, +1.0)
  flow_score = clamp(net_large_inflow / FLOW_SCALE, -1.0, +1.0)
  score      = 0.5 * book_score + 0.5 * flow_score

FLOW_SCALE = $5M (この金額を+1.0/-1.0の上限として定義)
"""

from __future__ import annotations

from engine.fetchers.moomoo_fetcher import MoomooFetcher, OrderBook, CapitalFlow

# 資金フローの正規化基準 ($5M = スコア±1.0)
_FLOW_SCALE_USD: float = 5_000_000.0

# 板圧力を「優勢」と判定するBid/Ask比率閾値
_DOMINANT_THRESHOLD: float = 0.55

# 実売買スコアへの寄与を許可する data_source 値の集合。
# "mock" (MOOMOO_USE_MOCK=true) と "mock_fallback" (OpenD 接続失敗) は
# 架空データであるため発注判断に寄与させない。
_TRADING_ELIGIBLE_SOURCES: frozenset[str] = frozenset({"live"})


# ──────────────────────────────────────────────
# 定量計算
# ──────────────────────────────────────────────

def _calc_book_ratios(book: OrderBook) -> tuple[float, float]:
    """
    Ask / Bid それぞれの数量合計比率を計算する。

    Returns
    -------
    (ask_ratio, bid_ratio)
        合計が常に 1.0 になるよう正規化。板が空の場合は (0.5, 0.5) を返す。
    """
    ask_total = sum(level.size for level in book.asks)
    bid_total = sum(level.size for level in book.bids)
    total = ask_total + bid_total
    if total == 0:
        return 0.5, 0.5
    return round(ask_total / total, 4), round(bid_total / total, 4)


def _calc_net_inflows(flow: CapitalFlow) -> tuple[float, float]:
    """
    大口（超大口+大口）および小口の純流入額（USD）を計算する。

    Returns
    -------
    (net_large_inflow, net_small_inflow)
        正 = 買い越し, 負 = 売り越し
    """
    net_large = flow.super_large.net + flow.large.net
    net_small = flow.small.net
    return round(net_large, 2), round(net_small, 2)


def _calc_score(bid_ratio: float, net_large_inflow: float) -> float:
    """
    板圧力と大口資金フローから複合シグナルスコアを計算する。

    book_score : Bid比率が0.5→0点, 0.7→+1点, 0.3→-1点 (線形)
    flow_score : 純流入$5M→+1点, -$5M→-1点（線形クランプ）
    """
    book_score = max(-1.0, min(1.0, (bid_ratio - 0.5) * 4.0))
    flow_score = max(-1.0, min(1.0, net_large_inflow / _FLOW_SCALE_USD))
    combined   = 0.5 * book_score + 0.5 * flow_score
    return round(combined, 4)


# ──────────────────────────────────────────────
# Verbal Annotation 生成 (VTA)
# ──────────────────────────────────────────────

def _fmt_usd(amount: float) -> str:
    """USD金額を読みやすい文字列に変換する（例: $5.5百万, $300千）。"""
    abs_amount = abs(amount)
    if abs_amount >= 1_000_000:
        return f"${abs_amount / 1_000_000:.1f}百万"
    elif abs_amount >= 1_000:
        return f"${abs_amount / 1_000:.0f}千"
    else:
        return f"${abs_amount:.0f}"


def _generate_verbal_annotation(
    ticker:           str,
    ask_ratio:        float,
    bid_ratio:        float,
    net_large_inflow: float,
    net_small_inflow: float,
    score:            float,
) -> str:
    """
    定量指標から日本語の自然言語アノテーション（Verbal Annotation）を生成する。

    ※ LLMを使わず、Pythonのルールベースロジックで生成する (VTA設計方針)。
    ※ ManagerAgentはこのテキストをプロンプトの推論コンテキストとして使用する。

    Parameters
    ----------
    ticker           : 銘柄シンボル
    ask_ratio        : Ask側比率 (0.0–1.0)
    bid_ratio        : Bid側比率 (0.0–1.0)
    net_large_inflow : 大口純流入額（USD, 正=買い越し）
    net_small_inflow : 小口純流入額（USD, 正=買い越し）
    score            : 複合シグナルスコア (-1.0–+1.0)

    Returns
    -------
    str
        日本語の自然言語サマリー（100〜200文字程度）
    """
    parts: list[str] = []

    # ── 大口資金フロー ──────────────────────────────
    flow_str  = _fmt_usd(net_large_inflow)
    flow_sign = "+" if net_large_inflow >= 0 else ""
    flow_dir  = "買い越し（純流入）" if net_large_inflow > 0 else "売り越し（純流出）"
    flow_icon = "📥" if net_large_inflow > 0 else "📤"
    parts.append(f"{flow_icon} 大口資金は{flow_sign}{flow_str}の{flow_dir}。")

    # ── 板圧力 ─────────────────────────────────────
    if bid_ratio > _DOMINANT_THRESHOLD:
        book_desc = (
            f"板はBid側に{int(bid_ratio * 100)}%集中しており、"
            f"下値サポートが厚く買い圧力が優勢。"
        )
    elif ask_ratio > _DOMINANT_THRESHOLD:
        book_desc = (
            f"板はAsk側に{int(ask_ratio * 100)}%偏っており、"
            f"上値が重く戻り売り圧力が強い展開。"
        )
    else:
        book_desc = (
            f"板はほぼ均等（Ask{int(ask_ratio * 100)}%/Bid{int(bid_ratio * 100)}%）、"
            f"方向感に乏しい。"
        )
    parts.append(book_desc)

    # ── 小口（リテール）動向 ────────────────────────
    if abs(net_small_inflow) > 200_000:
        retail_dir  = "買い" if net_small_inflow > 0 else "売り"
        retail_str  = _fmt_usd(net_small_inflow)
        retail_sign = "+" if net_small_inflow >= 0 else ""
        parts.append(
            f"小口（個人）は{retail_sign}{retail_str}の{retail_dir}傾向。"
        )

    # ── 総合判定 ────────────────────────────────────
    if score > 0.4:
        conclusion = "総合的に強い買い圧力優勢。機関資金の積極的な流入が見られる。"
    elif score > 0.15:
        conclusion = "総合的に穏やかな買い優勢。需給バランスはやや好転。"
    elif score < -0.4:
        conclusion = "総合的に強い売り圧力優勢。機関資金の流出に注意が必要。"
    elif score < -0.15:
        conclusion = "総合的にやや売り優勢。需給バランスは悪化傾向。"
    else:
        conclusion = "総合的に中立圏。需給バランスは均衡しており方向感なし。"
    parts.append(conclusion)

    return "".join(parts)


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def analyze_liquidity(ticker: str, use_mock: bool | None = None) -> dict:
    """
    板情報・資金フローを取得し、VTA（Verbal Technical Analysis）形式で分析結果を返す。

    Parameters
    ----------
    ticker   : 分析対象銘柄シンボル
    use_mock : None = 環境変数 MOOMOO_USE_MOCK に従う（デフォルト: True）

    Returns
    -------
    dict with keys:
        ticker            : str
        verbal_annotation : str   — ManagerAgentへ渡す自然言語サマリー (VTA)
        ask_ratio         : float — Ask側板数量比率
        bid_ratio         : float — Bid側板数量比率
        net_large_inflow  : float — 大口（超大口+大口）純流入額 USD
        net_small_inflow  : float — 小口純流入額 USD
        pressure          : str   — "buy_dominant" | "sell_dominant" | "neutral"
        score             : float — 複合シグナルスコア (-1.0〜+1.0)
        data_source       : str   — "mock" | "live"
    """
    fetcher = MoomooFetcher(ticker=ticker, use_mock=use_mock)

    try:
        book = fetcher.get_order_book(levels=5)
        flow = fetcher.get_capital_flow()
    except Exception as e:
        return {
            "ticker":               ticker,
            "verbal_annotation":    f"流動性データ取得エラー: {e}。分析をスキップ。",
            "ask_ratio":            0.5,
            "bid_ratio":            0.5,
            "net_large_inflow":     0.0,
            "net_small_inflow":     0.0,
            "pressure":             "neutral",
            "score":                0.0,
            "data_source":          "error",
            "eligible_for_trading": False,
            "error":                str(e),
        }

    # 定量計算（LLMを使わないPythonロジック）
    ask_ratio, bid_ratio               = _calc_book_ratios(book)
    net_large_inflow, net_small_inflow = _calc_net_inflows(flow)
    score                              = _calc_score(bid_ratio, net_large_inflow)

    # 板圧力の判定
    if bid_ratio > _DOMINANT_THRESHOLD and net_large_inflow > 0:
        pressure = "buy_dominant"
    elif ask_ratio > _DOMINANT_THRESHOLD and net_large_inflow < 0:
        pressure = "sell_dominant"
    else:
        pressure = "neutral"

    verbal_annotation = _generate_verbal_annotation(
        ticker           = ticker,
        ask_ratio        = ask_ratio,
        bid_ratio        = bid_ratio,
        net_large_inflow = net_large_inflow,
        net_small_inflow = net_small_inflow,
        score            = score,
    )

    return {
        "ticker":               ticker,
        "verbal_annotation":    verbal_annotation,
        "ask_ratio":            ask_ratio,
        "bid_ratio":            bid_ratio,
        "net_large_inflow":     net_large_inflow,
        "net_small_inflow":     net_small_inflow,
        "pressure":             pressure,
        "score":                score,
        "data_source":          fetcher.data_source,
        "eligible_for_trading": fetcher.data_source in _TRADING_ELIGIBLE_SOURCES,
    }


def run(ticker: str = "AAPL") -> dict:
    """Skill entry point."""
    return analyze_liquidity(ticker)
