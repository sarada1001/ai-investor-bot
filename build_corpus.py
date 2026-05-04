"""
build_corpus.py — 金融ナレッジベース（コーパス）構築スクリプト
RAG（検索拡張生成）向けのS&P 500企業情報をローカルに保存します。

使い方:
  TEST_MODE = True  → 上位5銘柄のみ取得（動作確認用）
  TEST_MODE = False → S&P 500全銘柄を取得（本番用）

レジューム機能:
  途中で中断しても、既存のJSONファイルがある銘柄はスキップして再開します。
"""

import json
import random
import time
from pathlib import Path

import pandas as pd
import yfinance as yf
from tqdm import tqdm

# ============================================================
# 設定
# ============================================================
TEST_MODE = False                   # True: 上位5銘柄のみ / False: S&P 500全銘柄
OUTPUT_DIR = Path("data/knowledge_base")
SLEEP_MIN = 1.0                     # リクエスト間の最小待機秒数
SLEEP_MAX = 3.0                     # リクエスト間の最大待機秒数

TEST_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]


def get_sp500_tickers() -> list[str]:
    """WikipediaからS&P 500のティッカーリストを取得する。"""
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(url, storage_options={"User-Agent": "Mozilla/5.0"})
        df = tables[0]
        # BRK.B → BRK-B のようにドットをハイフンに変換（yfinance形式）
        tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
        tqdm.write(f"  [CorpusBuilder] S&P 500ティッカー取得完了: {len(tickers)}銘柄")
        return tickers
    except Exception as e:
        tqdm.write(f"  [CorpusBuilder] エラー: Wikipediaからのティッカー取得に失敗 - {e}")
        tqdm.write(f"  [CorpusBuilder] フォールバック: テスト銘柄リストを使用します")
        return TEST_TICKERS


def fetch_company_info(ticker: str) -> dict | None:
    """yfinanceで企業情報を取得し、メタデータと本文を分けた辞書形式で返す。"""
    try:
        info = yf.Ticker(ticker).info

        if not info or not info.get("longName") and not info.get("shortName"):
            tqdm.write(f"  [CorpusBuilder] WARN: {ticker} - 企業情報が空です。スキップします。")
            return None

        name = info.get("longName") or info.get("shortName") or ticker
        sector = info.get("sector") or "Unknown"
        industry = info.get("industry") or "Unknown"
        summary = info.get("longBusinessSummary") or ""

        if not summary:
            tqdm.write(f"  [CorpusBuilder] WARN: {ticker} ({name}) - 事業概要が空です。メタデータのみ保存します。")

        return {
            # ChromaDBのメタデータフィルタリング用フィールド
            "metadata": {
                "ticker": ticker,
                "name": name,
                "sector": sector,
                "industry": industry,
                "exchange": info.get("exchange") or "Unknown",
                "market_cap": info.get("marketCap"),
                "currency": info.get("currency") or "USD",
            },
            # RAGの検索対象となる本文フィールド
            "content": {
                "long_business_summary": summary,
            },
        }
    except Exception as e:
        tqdm.write(f"  [CorpusBuilder] エラー: {ticker} の情報取得に失敗 - {e}")
        return None


def save_as_json(ticker: str, data: dict, output_dir: Path) -> None:
    """企業情報をJSONファイル（ticker.json）に保存する。"""
    try:
        filepath = output_dir / f"{ticker}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        name = data["metadata"]["name"]
        sector = data["metadata"]["sector"]
        tqdm.write(f"  [CorpusBuilder] 保存完了: {filepath.name}  ({name} / {sector})")
    except Exception as e:
        tqdm.write(f"  [CorpusBuilder] エラー: {ticker} のファイル保存に失敗 - {e}")


def build_corpus(tickers: list[str], output_dir: Path) -> None:
    """ティッカーリストをもとにコーパスを構築する。"""
    output_dir.mkdir(parents=True, exist_ok=True)

    # レジューム: 既存ファイルを確認し、未処理銘柄だけを抽出
    already_done = {f.stem for f in output_dir.glob("*.json")}
    remaining = [t for t in tickers if t not in already_done]
    resumed_count = len(already_done & set(tickers))

    print(f"  [CorpusBuilder] 出力ディレクトリ: {output_dir.resolve()}")
    print(f"  [CorpusBuilder] 全体: {len(tickers)}銘柄 / 取得済みスキップ: {resumed_count}件 / 処理対象: {len(remaining)}件")

    if not remaining:
        print(f"  [CorpusBuilder] 全銘柄取得済みです。処理をスキップします。")
        return

    print("-" * 60)

    success_count = 0
    error_count = 0

    progress = tqdm(remaining, desc="  [CorpusBuilder]", unit="銘柄", dynamic_ncols=True)
    for i, ticker in enumerate(progress):
        progress.set_postfix({"現在": ticker, "成功": success_count, "失敗": error_count})

        data = fetch_company_info(ticker)
        if data:
            save_as_json(ticker, data, output_dir)
            success_count += 1
        else:
            error_count += 1

        # 最後の銘柄以外はランダム待機（IPブロック対策）
        if i < len(remaining) - 1:
            time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print("-" * 60)
    print(f"  [CorpusBuilder] 構築完了 — 成功: {success_count}件 / 失敗: {error_count}件 / スキップ(既存): {resumed_count}件")
    print(f"  [CorpusBuilder] 保存先: {output_dir.resolve()}")


def main():
    mode_label = "テストモード（5銘柄）" if TEST_MODE else "フルモード（S&P 500全銘柄）"
    print(f"  [CorpusBuilder] 金融ナレッジベース構築を開始します（{mode_label}）")

    if TEST_MODE:
        tickers = TEST_TICKERS
        print(f"  [CorpusBuilder] 対象銘柄: {tickers}")
    else:
        tickers = get_sp500_tickers()

    build_corpus(tickers, OUTPUT_DIR)


if __name__ == "__main__":
    main()
