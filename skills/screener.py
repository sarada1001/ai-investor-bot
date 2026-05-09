"""
Skill: screener
Permission: ScreenerAgent / run_daemon のみ

S&P500 全銘柄から LLM を使わず純粋なテクニカルスコアで上位 N 銘柄を絞り込む
1次審査（プレスクリーニング）層。

スコアリング基準（合計最大 12 点）:
  +3  RSI < 35              売られすぎ（リバウンド候補）
  +1  35 ≤ RSI < 45         弱め（押し目候補）
  +2  MACD histogram > 0    ゴールデンクロス状態
  +1  MACD histogram 上向き  前日比で加速中
  +2  SMA25 下方乖離 -5%〜-1%  押し目買いゾーン
  +2  出来高急増             5日平均/20日平均 > 1.5
  +1  52週高値から -15%〜-35% スイングトレード対象ゾーン

キャッシュ:
  data/screener/cache.json に当日のスクリーニング結果を保存。
  当日中の再呼び出しはキャッシュを返し、yfinance 取得を省略する。

使用例:
  from skills.screener import screen_sp500
  results = screen_sp500(top_n=5)
  # [{"ticker": "AAPL", "score": 8, "rsi": 32.1, "reason": "..."}, ...]
"""

from __future__ import annotations

import json
import warnings
import datetime
from pathlib import Path

import io
import requests
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

_WIKI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; ECCInvestorBot/1.0; "
        "research-only; +https://github.com/example/ecc-investor-bot)"
    )
}

# ------------------------------------------------------------------ #
# 定数                                                                 #
# ------------------------------------------------------------------ #

_CACHE_DIR  = Path("data/screener")
_CACHE_FILE = _CACHE_DIR / "cache.json"

_SP500_WIKI_URL = (
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
)

# yfinance で一括取得する期間（スクリーニングは 3ヶ月で十分）
_FETCH_PERIOD = "3mo"


# ------------------------------------------------------------------ #
# S&P500 銘柄リスト取得                                                #
# ------------------------------------------------------------------ #

def get_sp500_tickers() -> list[str]:
    """
    Wikipedia から S&P500 構成銘柄一覧を取得する。
    取得失敗時は data/screener/sp500_tickers.csv のキャッシュにフォールバック。
    """
    csv_path = _CACHE_DIR / "sp500_tickers.csv"
    try:
        resp = requests.get(_SP500_WIKI_URL, headers=_WIKI_HEADERS, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text), header=0)
        df = tables[0]
        tickers = (
            df["Symbol"]
            .str.replace(".", "-", regex=False)  # BRK.B → BRK-B (yfinance形式)
            .tolist()
        )
        # 次回フォールバック用に保存
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"Symbol": tickers}).to_csv(csv_path, index=False)
        return tickers
    except Exception as e:
        print(f"  [Screener] Wikipedia 取得失敗: {e}")
        if csv_path.exists():
            print(f"  [Screener] ローカルキャッシュ ({csv_path}) を使用します")
            return pd.read_csv(csv_path)["Symbol"].tolist()
        raise RuntimeError(
            "S&P500 銘柄リストの取得に失敗しました。"
            f"{csv_path} も存在しません。"
        ) from e


# ------------------------------------------------------------------ #
# テクニカル指標計算（LLM なし）                                        #
# ------------------------------------------------------------------ #

def _calc_rsi(close: pd.Series, period: int = 14) -> float:
    try:
        import pandas_ta as ta
        result = ta.rsi(close, length=period)
        val = result.dropna()
        if val.empty:
            return 50.0
        return round(float(val.iloc[-1]), 2)
    except ImportError:
        pass
    # pure-pandas fallback (Wilder smoothing)
    delta = close.diff().dropna()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    vals = (100 - 100 / (1 + rs)).dropna()
    return round(float(vals.iloc[-1]), 2) if not vals.empty else 50.0


def _calc_macd_histogram(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float]:
    """Returns (current_histogram, prev_histogram)."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    sig_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - sig_line
    if len(hist) < 2:
        return 0.0, 0.0
    return float(hist.iloc[-1]), float(hist.iloc[-2])


def _calc_sma25_diff(close: pd.Series) -> float:
    """SMA25 からの現在価格の乖離率(%)を返す。"""
    sma = close.rolling(25).mean()
    sma_val = sma.dropna()
    if sma_val.empty:
        return 0.0
    latest_sma = float(sma_val.iloc[-1])
    latest_price = float(close.iloc[-1])
    if latest_sma == 0:
        return 0.0
    return round((latest_price - latest_sma) / latest_sma * 100, 2)


def _calc_volume_ratio(volume: pd.Series) -> float:
    """5日平均出来高 / 20日平均出来高 を返す（出来高急増の指標）。"""
    if len(volume) < 20:
        return 1.0
    avg5  = float(volume.iloc[-5:].mean())
    avg20 = float(volume.iloc[-20:].mean())
    if avg20 == 0:
        return 1.0
    return round(avg5 / avg20, 2)


def _calc_drawdown_from_52w_high(close: pd.Series) -> float:
    """52週高値からの現在価格の乖離率(%) を返す（負の値）。"""
    if len(close) < 2:
        return 0.0
    high_52w = float(close.max())
    latest   = float(close.iloc[-1])
    if high_52w == 0:
        return 0.0
    return round((latest - high_52w) / high_52w * 100, 2)


# ------------------------------------------------------------------ #
# 1銘柄のスコアリング                                                   #
# ------------------------------------------------------------------ #

def _score_ticker(ticker: str, df: pd.DataFrame) -> dict | None:
    """
    1銘柄の OHLCV DataFrame からスコアを計算する。
    エラー時は None を返す。

    Returns:
        {
            "ticker": str,
            "score": int,          # 合計スコア (0〜12)
            "rsi": float,
            "macd_hist": float,
            "sma25_diff_pct": float,
            "volume_ratio": float,
            "drawdown_52w_pct": float,
            "reason": str,         # スコア根拠の日本語サマリー
        }
    """
    try:
        if df.empty or len(df) < 30:
            return None

        close  = df["Close"].squeeze().dropna()
        volume = df["Volume"].squeeze().dropna()

        if len(close) < 30:
            return None

        rsi          = _calc_rsi(close)
        hist, prev   = _calc_macd_histogram(close)
        sma_diff     = _calc_sma25_diff(close)
        vol_ratio    = _calc_volume_ratio(volume)
        drawdown     = _calc_drawdown_from_52w_high(close)

        score  = 0
        parts: list[str] = []

        # RSI
        if rsi < 35:
            score += 3
            parts.append(f"RSI売られすぎ({rsi:.1f})+3")
        elif rsi < 45:
            score += 1
            parts.append(f"RSI弱め({rsi:.1f})+1")

        # MACD
        if hist > 0:
            score += 2
            parts.append("MACDゴールデン+2")
        if hist > prev:
            score += 1
            parts.append("MACD加速+1")

        # SMA25 押し目ゾーン
        if -5.0 < sma_diff < -1.0:
            score += 2
            parts.append(f"SMA25押し目({sma_diff:+.1f}%)+2")

        # 出来高急増
        if vol_ratio > 1.5:
            score += 2
            parts.append(f"出来高急増({vol_ratio:.1f}x)+2")

        # 52週高値からの押し目ゾーン
        if -35.0 < drawdown < -15.0:
            score += 1
            parts.append(f"52w押し目({drawdown:+.1f}%)+1")

        return {
            "ticker":           ticker,
            "score":            score,
            "rsi":              rsi,
            "macd_hist":        round(hist, 4),
            "sma25_diff_pct":   sma_diff,
            "volume_ratio":     vol_ratio,
            "drawdown_52w_pct": drawdown,
            "reason":           " / ".join(parts) if parts else "シグナルなし",
        }
    except Exception as exc:
        print(f"  [Screener] {ticker} スコアリングエラー: {exc}")
        return None


# ------------------------------------------------------------------ #
# キャッシュ管理                                                        #
# ------------------------------------------------------------------ #

def _load_cache() -> list[dict] | None:
    """当日のキャッシュがあれば返す。なければ None。"""
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        cached_date = data.get("date", "")
        today = datetime.date.today().isoformat()
        if cached_date == today:
            return data.get("results", [])
    except Exception:
        pass
    return None


def _save_cache(results: list[dict]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "date":    datetime.date.today().isoformat(),
        "results": results,
    }
    _CACHE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ------------------------------------------------------------------ #
# メイン公開 API                                                        #
# ------------------------------------------------------------------ #

def screen_sp500(
    top_n:          int  = 5,
    use_cache:      bool = True,
    verbose:        bool = True,
) -> list[dict]:
    """
    S&P500 全銘柄をテクニカルスコアでスクリーニングし、上位 top_n 銘柄を返す。

    Args:
        top_n:      返す銘柄数（デフォルト 5）
        use_cache:  True の場合、当日キャッシュがあればそれを返す
        verbose:    進捗ログを標準出力に表示するか

    Returns:
        List[dict]: スコア降順の辞書リスト
        [
            {
                "ticker": "AAPL",
                "score": 8,
                "rsi": 32.1,
                "macd_hist": 0.12,
                "sma25_diff_pct": -2.3,
                "volume_ratio": 1.8,
                "drawdown_52w_pct": -22.0,
                "reason": "RSI売られすぎ(32.1)+3 / 出来高急増(1.8x)+2 ...",
            },
            ...
        ]
    """
    # キャッシュヒット
    if use_cache:
        cached = _load_cache()
        if cached is not None:
            if verbose:
                print(f"  [Screener] 当日キャッシュを使用 ({len(cached)} 件中上位 {top_n} 件)")
            return cached[:top_n]

    if verbose:
        print("  [Screener] S&P500 銘柄リストを取得中...")
    tickers = get_sp500_tickers()
    if verbose:
        print(f"  [Screener] {len(tickers)} 銘柄を取得しました。テクニカルデータを一括ダウンロード中...")

    # yfinance 一括取得（スペース区切りのティッカー文字列）
    ticker_str = " ".join(tickers)
    try:
        raw = yf.download(
            ticker_str,
            period=_FETCH_PERIOD,
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        if verbose:
            print(f"  [Screener] 一括取得エラー: {e}")
        return []

    if verbose:
        print(f"  [Screener] ダウンロード完了。スコアリング中...")

    # yfinance のバージョンによって MultiIndex の順序が変わる場合があるため、
    # 実際のデータから ticker がどちらの level にいるかを動的に検出する。
    _ticker_level: int | None = None
    if isinstance(raw.columns, pd.MultiIndex):
        sample = tickers[0] if tickers else ""
        if sample in raw.columns.get_level_values(0):
            _ticker_level = 0   # (ticker, field)
        elif sample in raw.columns.get_level_values(1):
            _ticker_level = 1   # (field, ticker)
        else:
            if verbose:
                print("  [Screener] MultiIndex に銘柄名が見つかりません。スキップします。")
            return []

    error_count = 0
    scored: list[dict] = []
    for ticker in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex) and _ticker_level is not None:
                if ticker not in raw.columns.get_level_values(_ticker_level):
                    continue
                df_one = raw.xs(ticker, axis=1, level=_ticker_level)
            else:
                df_one = raw
        except Exception:
            error_count += 1
            continue

        result = _score_ticker(ticker, df_one)
        if result is not None and result["score"] > 0:
            scored.append(result)

    if error_count > len(tickers) * 0.2:
        print(f"  [Screener] 警告: {error_count} 銘柄でデータ切り出しに失敗しました（全体の {error_count/len(tickers):.0%}）")

    # スコア降順・同点なら出来高急増を優先
    scored.sort(key=lambda x: (x["score"], x["volume_ratio"]), reverse=True)

    if verbose:
        print(f"  [Screener] スコアリング完了: {len(scored)} 銘柄が候補。上位 {top_n} 銘柄を選出。")
        _print_screener_results(scored[:top_n])

    _save_cache(scored)
    return scored[:top_n]


def invalidate_cache() -> None:
    """スクリーナーキャッシュを強制削除（翌サイクルで再スクリーニングを促す）。"""
    if _CACHE_FILE.exists():
        _CACHE_FILE.unlink()


# ------------------------------------------------------------------ #
# セッション（時間単位）キャッシュ管理                                    #
# ------------------------------------------------------------------ #

_INTRADAY_CACHE_FILE = _CACHE_DIR / "intraday_cache.json"


def _load_intraday_cache() -> list[dict] | None:
    """当時間のキャッシュがあれば返す。なければ None。"""
    if not _INTRADAY_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_INTRADAY_CACHE_FILE.read_text(encoding="utf-8"))
        now = datetime.datetime.now()
        cache_key = f"{now.date().isoformat()}_{now.hour:02d}"
        if data.get("session_key") == cache_key:
            return data.get("results", [])
    except Exception:
        pass
    return None


def _save_intraday_cache(results: list[dict]) -> None:
    now = datetime.datetime.now()
    cache_key = f"{now.date().isoformat()}_{now.hour:02d}"
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"session_key": cache_key, "results": results}
    _INTRADAY_CACHE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ------------------------------------------------------------------ #
# Intraday スコアリング補助関数                                          #
# ------------------------------------------------------------------ #

def _calc_intraday_volume_spike(volume: pd.Series) -> float:
    """最新1本のボリューム / 当日平均ボリューム を返す（日中出来高急増指標）。"""
    if len(volume) < 2:
        return 1.0
    avg = float(volume.iloc[:-1].mean())
    if avg == 0:
        return 1.0
    return round(float(volume.iloc[-1]) / avg, 2)


def _calc_intraday_momentum(close: pd.Series, open_series: pd.Series) -> float:
    """(当日最新終値 - 当日始値) / 当日始値 × 100 を返す（日中モメンタム %）。"""
    if open_series.empty or close.empty:
        return 0.0
    day_open = float(open_series.iloc[0])
    latest_close = float(close.iloc[-1])
    if day_open == 0:
        return 0.0
    return round((latest_close - day_open) / day_open * 100, 2)


def _score_intraday(
    ticker: str,
    df: pd.DataFrame,
    daily_score: int,
    daily_reason: str,
) -> dict | None:
    """1h足DataFrameから日中スコアを計算し、日次スコアと合算する。"""
    try:
        if df.empty or len(df) < 2:
            return None

        close  = df["Close"].squeeze().dropna()
        volume = df["Volume"].squeeze().dropna()
        open_s = df["Open"].squeeze().dropna()

        if close.empty:
            return None

        vol_spike  = _calc_intraday_volume_spike(volume)
        momentum   = _calc_intraday_momentum(close, open_s)

        intraday_score = 0
        parts: list[str] = []

        if vol_spike >= 2.0:
            intraday_score += 3
            parts.append(f"日中出来高急増({vol_spike:.1f}x)+3")
        elif vol_spike >= 1.5:
            intraday_score += 2
            parts.append(f"日中出来高増加({vol_spike:.1f}x)+2")
        elif vol_spike >= 1.2:
            intraday_score += 1
            parts.append(f"日中出来高増加({vol_spike:.1f}x)+1")

        if momentum >= 2.0:
            intraday_score += 2
            parts.append(f"日中急騰({momentum:+.1f}%)+2")
        elif momentum >= 1.0:
            intraday_score += 1
            parts.append(f"日中上昇({momentum:+.1f}%)+1")
        elif momentum <= -2.0:
            intraday_score -= 2
            parts.append(f"日中急落({momentum:+.1f}%)-2")

        combined_score = daily_score + intraday_score
        intraday_reason = " / ".join(parts) if parts else "日中シグナルなし"
        combined_reason = (
            f"[日次] {daily_reason}  |  [日中] {intraday_reason}"
            if daily_reason and daily_reason != "シグナルなし"
            else f"[日中] {intraday_reason}"
        )

        return {
            "ticker":                ticker,
            "score":                 combined_score,
            "intraday_score":        intraday_score,
            "intraday_vol_spike":    vol_spike,
            "intraday_momentum_pct": momentum,
            "reason":                combined_reason,
        }
    except Exception as exc:
        print(f"  [Screener] {ticker} 日中スコアリングエラー: {exc}")
        return None


# ------------------------------------------------------------------ #
# Intraday メイン公開 API                                               #
# ------------------------------------------------------------------ #

def screen_sp500_intraday(
    top_n:        int  = 5,
    pre_filter_n: int  = 50,
    use_cache:    bool = True,
    verbose:      bool = True,
) -> list[dict]:
    """
    S&P500 を日次スクリーニングでプレフィルタし、当日 1h 足データで
    出来高急増・日中モメンタムを検出して上位 top_n 銘柄を返す。

    キャッシュは時間単位（セッションごと = 毎時間の最初の呼び出しで再スクリーニング）。

    Args:
        top_n:        返す銘柄数（デフォルト 5）
        pre_filter_n: 日次スクリーナーのプレフィルタ件数（デフォルト 50）
        use_cache:    True の場合、当時間のキャッシュがあればそれを返す
        verbose:      進捗ログを表示するか

    Returns:
        List[dict]: daily フィールドに加え以下を追加:
            "intraday_score":        int
            "intraday_vol_spike":    float
            "intraday_momentum_pct": float
    """
    if use_cache:
        cached = _load_intraday_cache()
        if cached is not None:
            now = datetime.datetime.now()
            if verbose:
                print(
                    f"  [Screener/Intraday] 当時間キャッシュを使用 "
                    f"({now.hour:02d}:xx, {len(cached)} 件中上位 {top_n} 件)"
                )
            return cached[:top_n]

    if verbose:
        print(f"  [Screener/Intraday] 日次上位 {pre_filter_n} 銘柄をプレフィルタリング中...")

    # Step 1: 日次スクリーナーでプレフィルタ（当日キャッシュを積極利用して高速化）
    daily_results = screen_sp500(top_n=pre_filter_n, use_cache=True, verbose=False)
    if not daily_results:
        if verbose:
            print("  [Screener/Intraday] 日次スクリーナー結果 0 件。フォールバック不可。")
        return []

    candidate_tickers = [r["ticker"] for r in daily_results]
    daily_map = {r["ticker"]: r for r in daily_results}

    if verbose:
        print(f"  [Screener/Intraday] {len(candidate_tickers)} 銘柄の 1h 足データを取得中...")

    # Step 2: 1時間足データを一括ダウンロード
    ticker_str = " ".join(candidate_tickers)
    try:
        raw = yf.download(
            ticker_str,
            period      = "1d",
            interval    = "1h",
            group_by    = "ticker",
            auto_adjust = True,
            progress    = False,
            threads     = True,
        )
    except Exception as e:
        if verbose:
            print(f"  [Screener/Intraday] 1h 足取得エラー: {e} → 日次スクリーナー結果を返します")
        return daily_results[:top_n]

    if raw.empty:
        if verbose:
            print("  [Screener/Intraday] 1h 足データが空（市場閉場/休日）→ 日次スクリーナー結果を返します")
        return daily_results[:top_n]

    # MultiIndex レベル検出
    _ticker_level: int | None = None
    if isinstance(raw.columns, pd.MultiIndex):
        sample = candidate_tickers[0] if candidate_tickers else ""
        if sample in raw.columns.get_level_values(0):
            _ticker_level = 0
        elif sample in raw.columns.get_level_values(1):
            _ticker_level = 1

    if verbose:
        print("  [Screener/Intraday] 日中スコアリング中...")

    _intraday_fallback = {"intraday_score": 0, "intraday_vol_spike": 1.0, "intraday_momentum_pct": 0.0}
    scored: list[dict] = []

    for ticker in candidate_tickers:
        daily_info   = daily_map.get(ticker, {})
        daily_score  = daily_info.get("score", 0)
        daily_reason = daily_info.get("reason", "シグナルなし")

        try:
            if isinstance(raw.columns, pd.MultiIndex) and _ticker_level is not None:
                if ticker not in raw.columns.get_level_values(_ticker_level):
                    scored.append({**daily_info, **_intraday_fallback})
                    continue
                df_one = raw.xs(ticker, axis=1, level=_ticker_level)
            else:
                df_one = raw
        except Exception:
            scored.append({**daily_info, **_intraday_fallback})
            continue

        result = _score_intraday(ticker, df_one, daily_score, daily_reason)
        if result is not None:
            scored.append({**daily_info, **result})
        else:
            scored.append({**daily_info, **_intraday_fallback})

    # スコア降順・同点なら日中出来高急増を優先
    scored.sort(key=lambda x: (x["score"], x.get("intraday_vol_spike", 1.0)), reverse=True)

    if verbose:
        now = datetime.datetime.now()
        print(
            f"  [Screener/Intraday] スコアリング完了 ({now.strftime('%H:%M')}): "
            f"{len(scored)} 銘柄。上位 {top_n} を選出。"
        )
        _print_intraday_results(scored[:top_n])

    _save_intraday_cache(scored)
    return scored[:top_n]


# ------------------------------------------------------------------ #
# 表示ヘルパー                                                          #
# ------------------------------------------------------------------ #

def _print_screener_results(results: list[dict]) -> None:
    bar = "─" * 70
    print(f"\n  ┌{bar}┐")
    print(f"  │  {'スクリーニング結果':^68}  │")
    print(f"  ├{bar}┤")
    print(f"  │  {'#':>3}  {'Ticker':<6}  {'Score':>5}  {'RSI':>5}  {'SMA25乖離':>8}  根拠")
    print(f"  ├{bar}┤")
    for i, r in enumerate(results, 1):
        line = (
            f"  │  {i:>3}  {r['ticker']:<6}  {r['score']:>5}  "
            f"{r['rsi']:>5.1f}  {r['sma25_diff_pct']:>+7.1f}%  "
            f"{r['reason'][:35]}"
        )
        print(line)
    print(f"  └{bar}┘\n")


def _print_intraday_results(results: list[dict]) -> None:
    bar = "─" * 78
    print(f"\n  ┌{bar}┐")
    print(f"  │  {'日中動的スクリーニング結果':^76}  │")
    print(f"  ├{bar}┤")
    print(f"  │  {'#':>3}  {'Ticker':<6}  {'合計':>4}  {'日中':>4}  {'VolSpike':>8}  {'Momentum':>9}  根拠（日中）")
    print(f"  ├{bar}┤")
    for i, r in enumerate(results, 1):
        line = (
            f"  │  {i:>3}  {r['ticker']:<6}  {r['score']:>4}  "
            f"{r.get('intraday_score', 0):>4}  "
            f"{r.get('intraday_vol_spike', 1.0):>7.1f}x  "
            f"{r.get('intraday_momentum_pct', 0.0):>+8.1f}%  "
            f"{r.get('reason', '')[-30:]}"
        )
        print(line)
    print(f"  └{bar}┘\n")
