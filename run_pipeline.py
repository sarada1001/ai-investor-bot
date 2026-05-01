# run_pipeline.py
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


def run_screener():
    print("══════════════════════════════════════════════════════════════")
    print(" 🔍 ScreenerAgent: 市場全体から有望銘柄を探索中...")
    print("══════════════════════════════════════════════════════════════")

    print("🌐 WikipediaからS&P500の銘柄リストを取得中...")

    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        tables = pd.read_html(StringIO(response.text))
        df = tables[0]
        sp500_tickers = df['Symbol'].str.replace('.', '-', regex=False).tolist()

        watch_list = random.sample(sp500_tickers, 30)
        print(f"🎲 本日は全500銘柄から {len(watch_list)} 銘柄を抽出してスキャンします。")

    except Exception as e:
        print(f"⚠️ リストの取得に失敗しました ({e})。デフォルトの銘柄を使用します。")
        watch_list = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]

    candidates = []
    print("📊 各銘柄の直近のモメンタム（価格変化と出来高）を分析中...")
    for ticker in watch_list:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5d")

            if len(hist) >= 2:
                prev_close = hist['Close'].iloc[-2]
                curr_close = hist['Close'].iloc[-1]
                prev_vol = hist['Volume'].iloc[-2]
                curr_vol = hist['Volume'].iloc[-1]

                change_pct = ((curr_close - prev_close) / prev_close) * 100

                if change_pct > 0 and curr_vol > prev_vol:
                    candidates.append((ticker, change_pct))
                    print(f"  [HIT] {ticker}: +{change_pct:.2f}% (出来高増)")
        except Exception as e:
            pass
        time.sleep(0.5)

    candidates.sort(key=lambda x: x[1], reverse=True)
    top_picks = [c[0] for c in candidates[:2]]

    if not top_picks:
        print("⚠️ 本日はスクリーニング条件を満たす有望銘柄が見つかりませんでした。")
        forced = random.sample(watch_list, min(random.randint(1, 2), len(watch_list)))
        print(f"⚠️ 学習データ収集のため、強制的に分析フェーズへ移行します: {forced}")
        return forced

    print(f"\n🎯 スクリーニング完了！本日のAI投資会議 候補銘柄: {top_picks}")
    return top_picks


if __name__ == "__main__":
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

            # ハイブリッドモード: リアル市場データ (yfinance + Gemini) で分析、発注はスキップ
            subprocess.run(["python3", "main.py", "--hybrid", "--ticker", ticker])

            print(f"✅ [{ticker}] の分析サイクル完了。\n")
            time.sleep(3)
    else:
        print("本日のシステム稼働を終了します。")
