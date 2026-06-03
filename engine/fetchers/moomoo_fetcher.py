"""engine/fetchers/moomoo_fetcher.py — Moomoo (Futu OpenAPI) データフェッチャー

FutuOpenD ローカルゲートウェイ (デフォルト: 127.0.0.1:11111) 経由で
米国株のリアルタイム板情報・資金フローを取得する。

接続エラー・レートリミット・休場日などの場合はモックデータにフォールバックし、
システム全体をクラッシュさせない設計（try-except + フォールバック徹底）。

取得データ:
  - OrderBook : Ask/Bid の気配値・数量（最良5本値）
  - CapitalFlow: 超大口・大口・中口・小口の純資金流入出（当日累計 USD）

Futu API 戻り値仕様:
  get_order_book(code, num) → (RET_OK, {'Ask': [(price, vol, n, {}), ...], 'Bid': [...]})
  get_capital_flow(code)    → (RET_OK, DataFrame[
                                 last_valid_time, in_flow,
                                 super_in_flow, big_in_flow, mid_in_flow, sml_in_flow,
                                 main_in_flow, capital_flow_item_time
                               ])
  ※ *_in_flow は純流入額(USD, 正=買い越し/負=売り越し)

環境変数:
    MOOMOO_USE_MOCK   : "true" でモック強制 (デフォルト: "false" = ライブ優先)
    MOOMOO_HOST       : OpenD ゲートウェイホスト (デフォルト: "127.0.0.1")
    MOOMOO_PORT       : OpenD ゲートウェイポート (デフォルト: 11111)

使用例:
    fetcher = MoomooFetcher(ticker="AAPL")                 # ライブ優先 (OpenD 接続)
    fetcher = MoomooFetcher(ticker="AAPL", use_mock=True)  # モック強制
    book = fetcher.get_order_book()    # OrderBook dataclass
    flow = fetcher.get_capital_flow()  # CapitalFlow dataclass
    print(fetcher.data_source)         # "live" | "mock" | "mock_fallback"
"""

from __future__ import annotations

import logging
import os
import queue
import socket
import threading
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# データ型定義
# ──────────────────────────────────────────────

@dataclass
class OrderLevel:
    """気配値の1本値（価格 + 数量）。"""
    price: float
    size:  int


@dataclass
class OrderBook:
    """
    Ask / Bid の最良 N 本値。

    asks: 売り板（価格が低い順、最良ASKが先頭）
    bids: 買い板（価格が高い順、最良BIDが先頭）
    """
    asks: list[OrderLevel] = field(default_factory=list)
    bids: list[OrderLevel] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "asks": [{"price": a.price, "size": a.size} for a in self.asks],
            "bids": [{"price": b.price, "size": b.size} for b in self.bids],
        }


@dataclass
class CapitalFlowTier:
    """資金フローの1ティア（inflow / outflow 単位: USD）。"""
    inflow:  float
    outflow: float

    @property
    def net(self) -> float:
        return self.inflow - self.outflow

    @classmethod
    def from_net(cls, net_flow: float) -> "CapitalFlowTier":
        """
        Futu APIの純流入額（net）から CapitalFlowTier を生成する。
        net > 0 → inflow=net, outflow=0 （買い越し）
        net < 0 → inflow=0, outflow=-net （売り越し）
        tier.net は元の net_flow と等価。
        """
        if net_flow >= 0:
            return cls(inflow=round(net_flow, 2), outflow=0.0)
        return cls(inflow=0.0, outflow=round(-net_flow, 2))


@dataclass
class CapitalFlow:
    """
    当日の資金フロー集計（超大口〜小口の4ティア）。

    各ティアの一般的な定義:
      super_large : 注文1件 $500k 超（機関投資家・ヘッジファンド）
      large       : 注文1件 $100k〜$500k（Futu: big_in_flow）
      medium      : 注文1件 $10k〜$100k （Futu: mid_in_flow）
      small       : 注文1件 $10k 未満（個人投資家、Futu: sml_in_flow）
    """
    super_large: CapitalFlowTier = field(default_factory=lambda: CapitalFlowTier(0.0, 0.0))
    large:       CapitalFlowTier = field(default_factory=lambda: CapitalFlowTier(0.0, 0.0))
    medium:      CapitalFlowTier = field(default_factory=lambda: CapitalFlowTier(0.0, 0.0))
    small:       CapitalFlowTier = field(default_factory=lambda: CapitalFlowTier(0.0, 0.0))

    def to_dict(self) -> dict:
        return {
            tier: {"inflow": getattr(self, tier).inflow, "outflow": getattr(self, tier).outflow}
            for tier in ("super_large", "large", "medium", "small")
        }


# ──────────────────────────────────────────────
# フェッチャーインターフェース (Protocol)
# ──────────────────────────────────────────────

@runtime_checkable
class LiquidityFetcher(Protocol):
    """板情報・資金フローを取得するフェッチャーのインターフェース（差し替え用）。"""

    def get_order_book(self, levels: int = 5) -> OrderBook: ...
    def get_capital_flow(self) -> CapitalFlow: ...


# ──────────────────────────────────────────────
# モックデータセット（フォールバック・テスト用）
# ──────────────────────────────────────────────

# 強気シナリオ: 大口買い越し + Bid側優勢
_MOCK_BULLISH: dict[str, dict] = {
    "order_book": {
        "asks": [
            {"price": 185.10, "size": 100},
            {"price": 185.20, "size": 200},
            {"price": 185.30, "size": 150},
            {"price": 185.40, "size":  80},
            {"price": 185.50, "size": 120},
        ],
        "bids": [
            {"price": 184.90, "size": 320},
            {"price": 184.80, "size": 280},
            {"price": 184.70, "size": 240},
            {"price": 184.60, "size": 200},
            {"price": 184.50, "size": 180},
        ],
    },
    "capital_flow": {
        "super_large": {"inflow": 5_200_000, "outflow": 1_800_000},
        "large":       {"inflow": 3_100_000, "outflow": 1_400_000},
        "medium":      {"inflow": 1_050_000, "outflow":   820_000},
        "small":       {"inflow":   480_000, "outflow":   550_000},
    },
}

# 弱気シナリオ: 大口売り越し + Ask側優勢
_MOCK_BEARISH: dict[str, dict] = {
    "order_book": {
        "asks": [
            {"price": 185.10, "size": 350},
            {"price": 185.20, "size": 290},
            {"price": 185.30, "size": 230},
            {"price": 185.40, "size": 180},
            {"price": 185.50, "size": 150},
        ],
        "bids": [
            {"price": 184.90, "size":  90},
            {"price": 184.80, "size": 110},
            {"price": 184.70, "size": 130},
            {"price": 184.60, "size":  80},
            {"price": 184.50, "size": 100},
        ],
    },
    "capital_flow": {
        "super_large": {"inflow": 1_200_000, "outflow": 4_800_000},
        "large":       {"inflow": 1_000_000, "outflow": 3_200_000},
        "medium":      {"inflow":   700_000, "outflow": 1_100_000},
        "small":       {"inflow":   600_000, "outflow":   400_000},
    },
}

# 中立シナリオ: 資金フロー拮抗 + 板ほぼ均等
_MOCK_NEUTRAL: dict[str, dict] = {
    "order_book": {
        "asks": [
            {"price": 185.10, "size": 190},
            {"price": 185.20, "size": 210},
            {"price": 185.30, "size": 175},
            {"price": 185.40, "size": 160},
            {"price": 185.50, "size": 140},
        ],
        "bids": [
            {"price": 184.90, "size": 210},
            {"price": 184.80, "size": 195},
            {"price": 184.70, "size": 180},
            {"price": 184.60, "size": 165},
            {"price": 184.50, "size": 145},
        ],
    },
    "capital_flow": {
        "super_large": {"inflow": 2_500_000, "outflow": 2_400_000},
        "large":       {"inflow": 1_800_000, "outflow": 1_750_000},
        "medium":      {"inflow":   900_000, "outflow":   850_000},
        "small":       {"inflow":   500_000, "outflow":   510_000},
    },
}

# ティッカー別モックシナリオ割り当て（テスト再現性のため固定）
_TICKER_SCENARIO: dict[str, dict] = {
    "AAPL": _MOCK_BULLISH,  "MSFT": _MOCK_BULLISH, "NVDA": _MOCK_BULLISH,
    "GOOGL": _MOCK_BULLISH, "META": _MOCK_BULLISH, "AMZN": _MOCK_BULLISH,
    "TSLA": _MOCK_BEARISH,  "INTC": _MOCK_BEARISH, "BA":   _MOCK_BEARISH,
    "DIS":  _MOCK_BEARISH,
}


def _get_mock_scenario(ticker: str) -> dict:
    """ティッカーに対応するモックシナリオを返す（未定義は中立）。"""
    return _TICKER_SCENARIO.get(ticker.upper(), _MOCK_NEUTRAL)


# ──────────────────────────────────────────────
# MoomooFetcher 実装
# ──────────────────────────────────────────────

class MoomooFetcher:
    """
    Moomoo (Futu OpenAPI) データフェッチャー。

    FutuOpenD ローカルゲートウェイ経由でリアルタイムデータを取得する。
    接続エラー・APIエラー・休場時は自動的にモックデータにフォールバックし、
    システムをクラッシュさせない。

    Parameters
    ----------
    ticker   : 取得対象銘柄シンボル（例: "AAPL"）。Futu用 "US.AAPL" に自動変換。
    use_mock : True = モック強制 / False = ライブ優先 / None = 環境変数に従う
    host     : OpenD ゲートウェイホスト（None = 環境変数 MOOMOO_HOST）
    port     : OpenD ゲートウェイポート（None = 環境変数 MOOMOO_PORT）
    """

    def __init__(
        self,
        ticker: str,
        use_mock: bool | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.ticker   = ticker.upper()
        _env_mock     = os.getenv("MOOMOO_USE_MOCK", "false").lower()
        self.use_mock = use_mock if use_mock is not None else (_env_mock == "true")
        self._host    = host or os.getenv("MOOMOO_HOST", "127.0.0.1")
        self._port    = port or int(os.getenv("MOOMOO_PORT", "11111"))
        # ライブフェッチでいずれかが失敗した場合にフラグを立てる
        self._had_live_failure: bool = False

    @property
    def data_source(self) -> str:
        """
        最後のフェッチで使ったデータソースを返す。

        "live"         : ライブAPI取得成功
        "mock"         : use_mock=True によるモック使用
        "mock_fallback": ライブ試行後にエラーでモックにフォールバック
        """
        if self.use_mock:
            return "mock"
        return "mock_fallback" if self._had_live_failure else "live"

    # ── Ticker conversion ─────────────────────────────────

    def _to_futu_code(self) -> str:
        """Futu API用の銘柄コードに変換する (例: AAPL → US.AAPL)。"""
        return f"US.{self.ticker}"

    # ── Public API ────────────────────────────────────────

    def get_order_book(self, levels: int = 5) -> OrderBook:
        """
        Ask / Bid の最良 levels 本値を取得する。

        Returns
        -------
        OrderBook
            asks: 売り板（価格が低い順）
            bids: 買い板（価格が高い順）
        """
        if self.use_mock:
            return self._mock_order_book(levels)
        return self._live_order_book(levels)

    def get_capital_flow(self) -> CapitalFlow:
        """
        当日の資金フロー集計（超大口〜小口）を取得する。

        Returns
        -------
        CapitalFlow
            4ティア（super_large / large / medium / small）の inflow・outflow（USD）
        """
        if self.use_mock:
            return self._mock_capital_flow()
        return self._live_capital_flow()

    # ── Live implementations ──────────────────────────────

    # OpenD 接続タイムアウト（秒）。Graphic verification 等で無限リトライするのを防ぐ
    _CONNECT_TIMEOUT_SEC: float = 4.0

    def _check_opend_reachable(self, timeout_sec: float = 2.0) -> None:
        """
        OpenD ゲートウェイへの TCP 疎通を事前チェックする（タイムアウト: timeout_sec 秒）。

        接続できない場合は ConnectionRefusedError を raise する。
        ※ ポートが OPEN でも画像認証などで接続拒否される場合があるため、
          この check のあとにさらにスレッドタイムアウトを使う。
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout_sec)
            result = sock.connect_ex((self._host, self._port))
        if result != 0:
            raise ConnectionRefusedError(
                f"FutuOpenD が {self._host}:{self._port} で応答しません "
                f"(errno={result}, timeout={timeout_sec}s)"
            )

    def _open_quote_ctx_with_timeout(self) -> "futu.OpenQuoteContext":  # type: ignore[name-defined]
        """
        OpenQuoteContext をスレッド内で生成し、_CONNECT_TIMEOUT_SEC 秒以内に
        接続が完了しなければ TimeoutError を raise する。

        OpenQuoteContext は画像認証要求・接続拒否時に while True で無限リトライするため、
        daemon スレッド + queue.Queue を使って強制タイムアウトする。
        timeout 超過時、スレッドは daemon なので main プロセス終了時に自動回収される。
        """
        import futu  # noqa: PLC0415

        # Futu SDK の内部接続リトライログを抑制する。
        # futu SDK は 'FTConsoleLog' / 'FTFileLog' という独自ロガーを使うため
        # 標準の 'futu' ロガーへの setLevel は効かない。両方を CRITICAL に設定する。
        # （タイムアウト後も daemon スレッドがリトライし続けるためノイズを消す）
        _ft_console = logging.getLogger("FTConsoleLog")
        _ft_file    = logging.getLogger("FTFileLog")
        _orig_console_lvl = _ft_console.level
        _orig_file_lvl    = _ft_file.level
        _ft_console.setLevel(logging.CRITICAL)
        _ft_file.setLevel(logging.CRITICAL)

        result_q: queue.Queue = queue.Queue(maxsize=1)

        def _connect() -> None:
            try:
                ctx = futu.OpenQuoteContext(host=self._host, port=self._port)
                result_q.put_nowait(("ok", ctx))
            except Exception as exc:  # noqa: BLE001
                result_q.put_nowait(("err", exc))

        t = threading.Thread(target=_connect, daemon=True, name="futu-connect")
        t.start()

        try:
            status, value = result_q.get(timeout=self._CONNECT_TIMEOUT_SEC)
            # 成功 or 即時エラー → futu ロガーをレベル復元
            _ft_console.setLevel(_orig_console_lvl)
            _ft_file.setLevel(_orig_file_lvl)
            if status == "err":
                raise value  # type: ignore[misc]
            return value  # type: ignore[return-value]
        except queue.Empty:
            # タイムアウト: daemon スレッドはそのまま daemon なので main 終了時に自動回収
            # ロガー抑制はそのまま維持（バックグラウンドのリトライログを消し続ける）
            raise TimeoutError(
                f"FutuOpenD ({self._host}:{self._port}) への接続が "
                f"{self._CONNECT_TIMEOUT_SEC}s 以内に完了しませんでした。\n"
                "【原因として考えられる】画像認証コード (Graphic Verification Code) が\n"
                "FutuOpenD アプリで要求されています。プロジェクトルートの PicVerifyCode.png\n"
                "に表示されたコードを FutuOpenD の認証ダイアログに入力してください。"
            )

    def _live_order_book(self, levels: int) -> OrderBook:
        """
        FutuOpenD から板情報をリアルタイム取得する。

        Futu API の戻り値形式:
            {'code': 'US.AAPL',
             'Ask': [(price, vol, order_count, {}), ...],
             'Bid': [(price, vol, order_count, {}), ...]}
        """
        ctx = None
        try:
            import futu as ft  # noqa: PLC0415  (lazy: 未インストール環境でも起動可)
            self._check_opend_reachable()  # TCP 疎通チェック（2秒以内、ECONNREFUSED を早期検出）
            logger.debug(
                "[MoomooFetcher] Connecting to OpenD %s:%s — %s order book",
                self._host, self._port, self.ticker,
            )
            ctx = self._open_quote_ctx_with_timeout()  # タイムアウト付き接続（画像認証対策）
            code = self._to_futu_code()

            # Futu API は get_order_book の前に ORDER_BOOK の subscribe が必須。
            # subscribe_push=False でプッシュコールバックなしのポーリングモードで購読する。
            ret_sub, msg_sub = ctx.subscribe([code], [ft.SubType.ORDER_BOOK], subscribe_push=False)
            if ret_sub != ft.RET_OK:
                raise RuntimeError(f"Futu subscribe ORDER_BOOK error: {msg_sub}")
            logger.debug("[MoomooFetcher] ORDER_BOOK subscribed for %s", self.ticker)

            ret, data = ctx.get_order_book(code, num=levels)

            if ret != ft.RET_OK:
                raise RuntimeError(f"Futu get_order_book error: {data}")

            asks = [
                OrderLevel(price=float(row[0]), size=int(row[1]))
                for row in data.get("Ask", [])[:levels]
            ]
            bids = [
                OrderLevel(price=float(row[0]), size=int(row[1]))
                for row in data.get("Bid", [])[:levels]
            ]

            logger.info(
                "[MoomooFetcher] ✅ %s order book (LIVE): %d asks / %d bids "
                "| best_ask=%.2f best_bid=%.2f",
                self.ticker,
                len(asks),
                len(bids),
                asks[0].price if asks else 0.0,
                bids[0].price if bids else 0.0,
            )
            return OrderBook(asks=asks, bids=bids)

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[MoomooFetcher] ⚠️  Order book live fetch failed for %s: %s"
                " — falling back to mock",
                self.ticker, exc,
            )
            self._had_live_failure = True
            return self._mock_order_book(levels)
        finally:
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    pass

    def _live_capital_flow(self) -> CapitalFlow:
        """
        FutuOpenD から当日の資金フローを取得する。

        Futu API の capital_flow DataFrame 列 (INTRADAY):
            super_in_flow : 超大口純流入額（正=買い越し / 負=売り越し）
            big_in_flow   : 大口純流入額
            mid_in_flow   : 中口純流入額
            sml_in_flow   : 小口純流入額
        最終行が当日の最新累計値。CapitalFlowTier.from_net() で inflow/outflow に変換。
        """
        ctx = None
        try:
            import futu as ft  # noqa: PLC0415  (lazy import)
            self._check_opend_reachable()  # TCP 疎通チェック（2秒以内）
            logger.debug(
                "[MoomooFetcher] Connecting to OpenD %s:%s — %s capital flow",
                self._host, self._port, self.ticker,
            )
            ctx = self._open_quote_ctx_with_timeout()  # タイムアウト付き接続（画像認証対策）
            code = self._to_futu_code()

            # SubType.CAPITAL_FLOW は Futu SDK に存在しない。
            # QUOTE サブタイプを購読することで銘柄の実データをOpenDにロードさせる。
            # capital_flow 自体はサブスクリプション不要の同期APIだが、
            # QUOTE 購読がないとデータ未ロードで空データを返すケースがある。
            ret_sub, msg_sub = ctx.subscribe([code], [ft.SubType.QUOTE], subscribe_push=False)
            if ret_sub != ft.RET_OK:
                logger.debug("[MoomooFetcher] QUOTE subscribe warning for %s: %s", self.ticker, msg_sub)
            else:
                logger.debug("[MoomooFetcher] QUOTE subscribed for %s", self.ticker)

            ret, data = ctx.get_capital_flow(code)

            if ret != ft.RET_OK:
                raise RuntimeError(f"Futu get_capital_flow error: {data}")

            if data is None or (hasattr(data, "empty") and data.empty):
                raise RuntimeError(
                    "Empty capital flow data (market may be closed or LV2 data unavailable)"
                )

            # 最終行 = 当日最新の累計値
            row = data.iloc[-1]

            def _safe_net(col: str) -> float:
                """列が存在しない・NaN の場合は 0.0 を返す。"""
                try:
                    val = row[col]
                    return float(val) if val is not None else 0.0
                except (KeyError, TypeError, ValueError):
                    return 0.0

            flow = CapitalFlow(
                super_large = CapitalFlowTier.from_net(_safe_net("super_in_flow")),
                large       = CapitalFlowTier.from_net(_safe_net("big_in_flow")),
                medium      = CapitalFlowTier.from_net(_safe_net("mid_in_flow")),
                small       = CapitalFlowTier.from_net(_safe_net("sml_in_flow")),
            )
            net_large = flow.super_large.net + flow.large.net
            logger.info(
                "[MoomooFetcher] ✅ %s capital flow (LIVE): "
                "net_large=$%.0f  super=$%.0f  big=$%.0f  mid=$%.0f  sml=$%.0f",
                self.ticker,
                net_large,
                flow.super_large.net,
                flow.large.net,
                flow.medium.net,
                flow.small.net,
            )
            return flow

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[MoomooFetcher] ⚠️  Capital flow live fetch failed for %s: %s"
                " — falling back to mock",
                self.ticker, exc,
            )
            self._had_live_failure = True
            return self._mock_capital_flow()
        finally:
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    pass

    # ── Mock implementations ─────────────────────────────

    def _mock_order_book(self, levels: int) -> OrderBook:
        scenario = _get_mock_scenario(self.ticker)
        raw_asks = scenario["order_book"]["asks"][:levels]
        raw_bids = scenario["order_book"]["bids"][:levels]
        return OrderBook(
            asks=[OrderLevel(price=a["price"], size=a["size"]) for a in raw_asks],
            bids=[OrderLevel(price=b["price"], size=b["size"]) for b in raw_bids],
        )

    def _mock_capital_flow(self) -> CapitalFlow:
        scenario = _get_mock_scenario(self.ticker)
        cf = scenario["capital_flow"]
        return CapitalFlow(
            super_large = CapitalFlowTier(**cf["super_large"]),
            large       = CapitalFlowTier(**cf["large"]),
            medium      = CapitalFlowTier(**cf["medium"]),
            small       = CapitalFlowTier(**cf["small"]),
        )
