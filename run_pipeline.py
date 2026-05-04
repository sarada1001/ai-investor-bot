# run_pipeline.py
import datetime
import json
import re
import yfinance as yf
import subprocess
import time
import sys
import pandas as pd
import random
import requests
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import skills.portfolio_monitor       as _portfolio_mod
import skills.training_data_collector as _training_mod

_W = 62  # ターミナル表示幅


def run_exit_check() -> list[dict]:
    """
    フェーズ0: ExitAgent — 保有ポジションの健康診断と Exit 判断。

    判断基準:
      1. 現在価格 < ストップロス価格 → SELL（ストップロス到達）
      2. 含み損が取得単価の -5% 超 → SELL（損切りライン超過）
      3. 上記以外 → HOLD（保有継続）

    SELL 判断が出た場合は警告ログをターミナルに出力する。
    Returns: SELL 判断のリスト
    """
    print("══════════════════════════════════════════════════════════════")
    print(" 🛡️  ExitAgent: 保有ポジションの健康診断を開始...")
    print("══════════════════════════════════════════════════════════════")

    portfolio_data = _portfolio_mod.get_current_portfolio()
    positions = portfolio_data if isinstance(portfolio_data, list) else []

    if not positions:
        print("  ℹ️  保有ポジションなし。Exit チェックをスキップします。")
        print()
        return []

    print(f"  {'銘柄':<6} {'保有株数':>6} {'取得単価':>9} {'現在価格':>9} {'損益率':>7} {'ストップ':>9} {'判断':>6}")
    print(f"  {'─' * 58}")

    decisions: list[dict] = []

    for pos in positions:
        ticker        = pos["ticker"]
        shares        = pos["shares"]
        avg_cost      = pos["avg_cost"]
        current_price = pos["current_price"]
        stop_loss     = pos["stop_loss"]
        pnl_pct       = pos["pnl_pct"]
        stop_hit      = pos["stop_loss_hit"]

        # ExitAgent 判断ロジック（system_prompt の基準をそのまま実装）
        if stop_hit:
            action = "SELL"
            reason = f"ストップロス到達（現在 ${current_price} < SL ${stop_loss}）"
        elif pnl_pct < -5.0:
            action = "SELL"
            reason = f"含み損が -5% 超（{pnl_pct:.2f}%）"
        else:
            action = "HOLD"
            reason = f"条件未達（損益 {pnl_pct:+.2f}%, SL未到達）"

        pnl_icon   = "📉" if pnl_pct < 0 else "📈"
        action_str = "🔴 SELL" if action == "SELL" else "🟢 HOLD"

        print(
            f"  {ticker:<6} {shares:>6}株  "
            f"${avg_cost:>7.2f}  ${current_price:>7.2f}  "
            f"{pnl_pct:>+6.2f}%{pnl_icon}  "
            f"${stop_loss:>7.2f}  {action_str}"
        )

        decision = {
            "ticker":         ticker,
            "action":         action,
            "reason":         reason,
            "current_price":  current_price,
            "stop_loss":      stop_loss,
            "pnl_pct":        pnl_pct,
        }
        decisions.append(decision)

        if action == "SELL":
            print(f"\n  ⚠️  [{ticker}] {reason}")
            print(f"  ⚠️  [{ticker}] ストップロス到達のため売却注文を送信しました\n")
            updated = _training_mod.update_outcome(
                ticker=ticker,
                pnl_pct=pnl_pct,
                exit_price=current_price,
                exit_reason=reason,
            )
            if updated:
                label = "WIN" if pnl_pct >= 0 else "LOSS"
                print(f"  📚 [{ticker}] 学習データ更新: outcome_label={label}  (record {updated}件)")

    sell_count = sum(1 for d in decisions if d["action"] == "SELL")
    hold_count = len(decisions) - sell_count

    print(f"  {'─' * 58}")
    print(f"  健康診断完了: SELL={sell_count}件 / HOLD={hold_count}件")
    print()

    return [d for d in decisions if d["action"] == "SELL"]


# ──────────────────────────────────────────────────────────────────
# スコアリングエンジン（全 S&P 500 対象）
# ──────────────────────────────────────────────────────────────────

# 指標ウェイト（合計 1.00）
_W_VOL = 0.40   # ボラティリティ・スパイク  ← 最重要（ニュース兆候）
_W_RSI = 0.35   # RSI 乖離                  ← 逆張り/順張り
_W_MA  = 0.25   # MA20 乖離率               ← リバウンド可能性

# スコア正規化の分母（この値で割って 0〜1 にクランプ）
_VOL_SPIKE_DENOM = 2.0   # 3倍スパイク → スコア 1.0
_RSI_EXTREME_RANGE = 30  # RSI 0 or 100 → スコア 1.0
_MA_DEV_DENOM    = 5.0   # 5% 乖離 → スコア 1.0

_FALLBACK_TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]
_screener_top3: list[dict] = []   # run_screener() が設定; update_readme_daily_pick() が参照


def _calc_rsi(close: "pd.Series", period: int = 14) -> float:
    """RSI(period) を純 pandas で計算。データ不足時は中立値 50.0 を返す。"""
    if len(close) < period + 1:
        return 50.0
    delta = close.diff().dropna()
    gain  = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs    = gain / loss.replace(0, float("nan"))
    rsi   = (100 - 100 / (1 + rs)).dropna()
    return float(rsi.iloc[-1]) if not rsi.empty else 50.0


def _score_ticker(
    ticker: str,
    close_df: "pd.DataFrame",
    volume_df: "pd.DataFrame",
) -> "dict | None":
    """
    1銘柄のスコアを計算して返す。データ不足・エラー時は None。

    Returns dict with keys:
        ticker, score, price,
        rsi, rsi_score, rsi_label,
        vol_ratio, vol_score, vol_label,
        ma_dev_pct, ma_score, ma_label
    """
    try:
        close  = close_df[ticker].dropna()
        volume = volume_df[ticker].dropna()
        if len(close) < 21:        # MA20 + 最低 1 日分
            return None

        price = float(close.iloc[-1])

        # ── 指標 1: RSI(14) 乖離 ──────────────────────────────────
        rsi = _calc_rsi(close)
        if rsi < 30:
            rsi_score = min(1.0, (30 - rsi) / _RSI_EXTREME_RANGE)
            rsi_label = f"RSI={rsi:.1f}（売られすぎ）"
        elif rsi > 70:
            rsi_score = min(1.0, (rsi - 70) / _RSI_EXTREME_RANGE)
            rsi_label = f"RSI={rsi:.1f}（強トレンド）"
        else:
            rsi_score = 0.0
            rsi_label = f"RSI={rsi:.1f}（中立域）"

        # ── 指標 2: ボラティリティ・スパイク ──────────────────────
        vol_today = float(volume.iloc[-1]) if len(volume) >= 1 else 0.0
        vol_avg5  = float(volume.iloc[-6:-1].mean()) if len(volume) >= 6 else float(volume.mean())
        vol_ratio = vol_today / vol_avg5 if vol_avg5 > 0 else 1.0
        vol_score = min(1.0, max(0.0, (vol_ratio - 1.0) / _VOL_SPIKE_DENOM))
        vol_label = f"出来高{vol_ratio:.1f}倍スパイク"

        # ── 指標 3: MA20 乖離率 ───────────────────────────────────
        ma20       = float(close.rolling(20).mean().dropna().iloc[-1])
        ma_dev_pct = (price - ma20) / ma20 * 100        # 符号付き
        ma_score   = min(1.0, abs(ma_dev_pct) / _MA_DEV_DENOM)
        direction  = "上方" if ma_dev_pct >= 0 else "下方"
        ma_label   = f"MA20から{abs(ma_dev_pct):.1f}%{direction}乖離"

        # ── 加重合計 ──────────────────────────────────────────────
        total = round(
            vol_score * _W_VOL + rsi_score * _W_RSI + ma_score * _W_MA, 4
        )

        return {
            "ticker":     ticker,
            "score":      total,
            "price":      price,
            "rsi":        round(rsi, 1),
            "rsi_score":  round(rsi_score, 3),
            "rsi_label":  rsi_label,
            "vol_ratio":  round(vol_ratio, 2),
            "vol_score":  round(vol_score, 3),
            "vol_label":  vol_label,
            "ma_dev_pct": round(ma_dev_pct, 2),
            "ma_score":   round(ma_score, 3),
            "ma_label":   ma_label,
        }
    except Exception:
        return None


def _build_reason(s: dict) -> str:
    """スコア辞書から人間が読める選出理由を組み立てる。"""
    parts = []
    if s["rsi_score"] > 0:
        parts.append(s["rsi_label"])
    if s["vol_ratio"] >= 1.5:
        parts.append(s["vol_label"])
    if abs(s["ma_dev_pct"]) >= 2.0:
        parts.append(s["ma_label"])
    return " / ".join(parts) if parts else "複合シグナルによる選出"


def _print_top3(top3: list[dict]) -> None:
    """精鋭3銘柄の選出結果を cron.log 用にフォーマット出力する。"""
    W = 62
    print()
    print(f"╔{'═' * W}╗")
    print(f"║  {'🎯  本日の精鋭3銘柄（スコアリング選出）':^{W - 2}}  ║")
    print(f"╠{'═' * W}╣")
    for rank, s in enumerate(top3, 1):
        reason = _build_reason(s)
        score_bar_len = int(s["score"] * 20)
        score_bar     = "█" * score_bar_len + "░" * (20 - score_bar_len)
        print(f"║  #{rank}  {s['ticker']:<6}  スコア: {s['score']:.4f}  [{score_bar}]{'':>{W - 50}}║")
        print(f"║      VOL:{s['vol_score']:.3f}×{_W_VOL:.0%}  "
              f"RSI:{s['rsi_score']:.3f}×{_W_RSI:.0%}  "
              f"MA:{s['ma_score']:.3f}×{_W_MA:.0%}  "
              f"(${s['price']:.2f}){'':>{W - 55}}║")
        print(f"║      理由: {reason[:W - 14]}{'':>{max(0, W - 14 - len(reason[:W-14]))}}║")
        if rank < 3:
            print(f"║  {'─' * (W - 2)}║")
    print(f"╚{'═' * W}╝")
    print()


def _fallback_screener(tickers: list[str]) -> list[str]:
    """
    一括取得失敗時の緊急フォールバック。
    旧ロジック（ランダム30銘柄 → モメンタムフィルタ → 上位2件）を実行する。
    """
    print("⚠️  [フォールバック] ランダムサンプリング + モメンタムフィルタ実行中...")
    watch_list = random.sample(tickers, min(30, len(tickers)))
    candidates = []
    for ticker in watch_list:
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if len(hist) >= 2:
                change_pct = (float(hist["Close"].iloc[-1]) / float(hist["Close"].iloc[-2]) - 1) * 100
                vol_ratio  = float(hist["Volume"].iloc[-1]) / float(hist["Volume"].iloc[-2])
                if change_pct > 0 and vol_ratio > 1:
                    candidates.append((ticker, change_pct))
        except Exception:
            pass
        time.sleep(0.3)

    candidates.sort(key=lambda x: x[1], reverse=True)
    picks = [c[0] for c in candidates[:2]]
    if not picks:
        picks = random.sample(watch_list, min(2, len(watch_list)))
    print(f"  フォールバック選出: {picks}")
    return picks


# ──────────────────────────────────────────────────────────────────

def run_screener() -> list[str]:
    global _screener_top3
    print("══════════════════════════════════════════════════════════════")
    print(" 🔍 ScreenerAgent: S&P 500 全銘柄スコアリング開始...")
    print("══════════════════════════════════════════════════════════════")

    # ── Step 1: S&P 500 銘柄リスト取得 ──────────────────────────
    print("🌐 Wikipedia から S&P 500 銘柄リストを取得中...")
    try:
        resp = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=15,
        )
        resp.raise_for_status()
        sp500_tickers = (
            pd.read_html(StringIO(resp.text))[0]["Symbol"]
            .str.replace(".", "-", regex=False)
            .tolist()
        )
        print(f"✅ {len(sp500_tickers)} 銘柄を取得しました。")
    except Exception as e:
        print(f"⚠️ リスト取得失敗 ({e})。フォールバックリストを使用します。")
        sp500_tickers = _FALLBACK_TICKERS

    # ── Step 2: yfinance 一括取得 ────────────────────────────────
    print(f"📥 一括データ取得中 ({len(sp500_tickers)} 銘柄 × 1ヶ月)...")
    try:
        raw = yf.download(
            sp500_tickers, period="1mo",
            auto_adjust=True, progress=False, threads=True,
        )
        if raw.empty:
            raise ValueError("取得データが空です")
        close_df  = raw["Close"]
        volume_df = raw["Volume"]
        # シングルティッカーの場合は Series → DataFrame に昇格
        if isinstance(close_df, pd.Series):
            close_df  = close_df.to_frame(name=sp500_tickers[0])
            volume_df = volume_df.to_frame(name=sp500_tickers[0])
        valid = close_df.notna().any().sum()
        print(f"✅ 取得完了。有効銘柄数: {valid} / {len(sp500_tickers)}")
    except Exception as e:
        print(f"⚠️ 一括取得失敗 ({e})。フォールバックスクリーナーを実行します。")
        return _fallback_screener(sp500_tickers)

    # ── Step 3: 全銘柄スコアリング ───────────────────────────────
    print("📊 スコアリング実行中...")
    scored: list[dict] = []
    for ticker in sp500_tickers:
        result = _score_ticker(ticker, close_df, volume_df)
        if result:
            scored.append(result)

    print(f"  スコア算出完了: {len(scored)} 銘柄")

    if len(scored) < 3:
        print("⚠️ スコアリング対象が3件未満。フォールバックスクリーナーを実行します。")
        return _fallback_screener(sp500_tickers)

    # ── Step 4: 上位3銘柄を選出 ──────────────────────────────────
    scored.sort(key=lambda x: x["score"], reverse=True)
    top3 = scored[:3]

    # ── Step 5: 選出結果の出力（cron.log に残る） ────────────────
    _print_top3(top3)

    _screener_top3 = top3

    # スクリーナー結果をJSONで永続化（Obsidianサマリー等で参照可能）
    screener_dir = Path(__file__).parent / "data" / "screener"
    screener_dir.mkdir(parents=True, exist_ok=True)
    screener_record = {
        "date": datetime.date.today().isoformat(),
        "top3": [
            {**s, "reason": _build_reason(s)} for s in top3
        ],
    }
    screener_path = screener_dir / f"{datetime.date.today().isoformat()}.json"
    screener_path.write_text(
        json.dumps(screener_record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"✅  スクリーナー結果を保存しました: {screener_path}")

    tickers = [s["ticker"] for s in top3]
    print(f"🚀 本日の精鋭3銘柄: {tickers}  → Gemini + RAG による詳細分析へ")
    return tickers


def update_readme_daily_pick(
    tickers: list[str],
    sell_orders: list[dict],
) -> None:
    """
    README.md の ## 🚀 Latest Daily Pick セクションを今日の選出結果で上書き（なければ作成）する。
    _screener_top3 に詳細スコアが格納されていれば Markdown テーブル形式で出力する。
    """
    readme_path = Path(__file__).parent / "README.md"
    if not readme_path.exists():
        print("⚠️  README.md が見つかりません。スキップします。")
        return

    today = datetime.date.today().isoformat()
    now   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── セクション本文を組み立てる ────────────────────────────────
    lines = [
        "## 🚀 Latest Daily Pick",
        "",
        f"> 最終更新: {now}",
        "",
        f"### 本日の精鋭3銘柄 ({today})",
        "",
    ]

    if _screener_top3:
        lines += [
            "| # | ティッカー | スコア | 価格 | 選出理由 |",
            "|---|-----------|--------|------|---------|",
        ]
        for i, s in enumerate(_screener_top3, 1):
            reason = _build_reason(s)
            lines.append(
                f"| {i} | **{s['ticker']}** | {s['score']:.4f}"
                f" | ${s['price']:.2f} | {reason} |"
            )
        lines += [
            "",
            "#### 指標内訳",
            "",
            "| ティッカー | RSI | VOL倍率 | VOLスコア | MAスコア | 総合スコア |",
            "|-----------|-----|--------|----------|--------|----------|",
        ]
        for s in _screener_top3:
            lines.append(
                f"| {s['ticker']} | {s['rsi']:.1f} | {s['vol_ratio']:.2f}x"
                f" | {s['vol_score']:.3f} | {s['ma_score']:.3f} | {s['score']:.4f} |"
            )
    else:
        for i, t in enumerate(tickers, 1):
            lines.append(f"{i}. **{t}**")

    if sell_orders:
        lines += ["", "### 本日の Exit 判断", ""]
        for d in sell_orders:
            lines.append(
                f"- **{d['ticker']}**: {d['reason']} (損益: {d['pnl_pct']:+.2f}%)"
            )

    lines.append("")
    new_section = "\n".join(lines)

    # ── README.md を読み込んで置換 ────────────────────────────────
    content = readme_path.read_text(encoding="utf-8")
    marker  = "## 🚀 Latest Daily Pick"

    if marker in content:
        # 既存セクションを新しい内容で差し替える
        start = content.index(marker)
        after = content[start + len(marker):]
        m = re.search(r'\n## ', after)
        if m:
            end = start + len(marker) + m.start()
            content = content[:start] + new_section + "\n\n" + content[end:].lstrip("\n")
        else:
            content = content[:start] + new_section
    else:
        # 初回挿入: System Activity Log の直前に配置する
        activity_marker = "## 🔄 System Activity Log"
        if activity_marker in content:
            idx = content.index(activity_marker)
            content = content[:idx] + new_section + "\n\n" + content[idx:]
        else:
            content = content.rstrip("\n") + "\n\n" + new_section

    readme_path.write_text(content, encoding="utf-8")
    print(f"✅  README.md を更新しました ({marker})")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ECC トレードパイプライン")
    parser.add_argument("--hybrid", action="store_true", help="ハイブリッドモードで実行")
    parser.add_argument(
        "--exclude", nargs="+", default=[], metavar="AGENT",
        help="除外するエージェント名（アブレーション実験用）",
    )
    args = parser.parse_args()

    # フェーズ0: 保有ポジションの監視と Exit 判断
    print("\n==============================================================")
    print(" フェーズ0: 保有ポジションの監視と Exit 判断 [ExitAgent]")
    print("==============================================================\n")
    sell_orders = run_exit_check()

    # フェーズ1: 自動銘柄発掘
    target_tickers = run_screener()

    # フェーズ2: 発掘した銘柄を順番にAI会議（main.py）にかける自動ループ
    if target_tickers:
        print("\n==============================================================")
        print(" 🤖 自律型トレードパイプライン起動")
        print("==============================================================\n")

        for ticker in target_tickers:
            print(f"🚀 候補銘柄 [{ticker}] のAI投資会議を開始します...")
            time.sleep(2)

            cmd = [sys.executable, "main.py", "--ticker", ticker]
            if args.hybrid:
                cmd.append("--hybrid")
            for agent in args.exclude:
                cmd += ["--exclude", agent]
            subprocess.run(cmd)

            print(f"✅ [{ticker}] の分析サイクル完了。\n")
            time.sleep(3)
    else:
        print("本日のシステム稼働を終了します。")

    # フェーズ3: README.md の Latest Daily Pick セクションを更新
    update_readme_daily_pick(target_tickers, sell_orders)
