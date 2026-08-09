#!/usr/bin/env python3
"""
scripts/health_check.py — サイレント障害の検知と「人間への配達」

## なぜこのスクリプトが存在するか

このシステムは無人 cron 運用のため、失敗しても誰も気づかない事故が繰り返し起きた。

- cron に登録された `scripts/health_check.py` が半年間存在せず、毎平日
  「No such file or directory」を吐き続けていた（MAILTO="" のため誰にも届かない）
- `backups/` が無く日次バックアップの `cp` が毎晩失敗していた
- `run_weekly_reflection.py` のモデル名直書きで半年間 LLM 生成が失敗していた
- `AlpacaClient.get_positions()` が例外を握って `[]` を返していた

いずれも「ログには出ていたが人間に届いていなかった」。
したがって本スクリプトの主目的は**検知ではなく配達**である。
FAIL は必ず LINE に飛ばす。

## 設計原則

1. **通知が主目的**  — FAIL があれば必ず LINE 送信。ログのみで終わらせない。
2. **読み取り専用**  — 状態ファイルを変更しない。修復もしない。報告のみ。
                       例外は D-9 の `data/health_last_run.json` と
                       追記専用の `logs/health.log` の 2 つだけ。
3. **自己申告**      — 実行時刻を記録し、「health_check 自体が止まったこと」を
                       次回実行時に検知する。
4. **独立性**        — 各チェックは `_safe()` で包む。1 項目が例外を投げても
                       他のチェックは継続し、その項目だけが FAIL になる。

## WARN 通知ポリシー（設計判断）

FAIL は毎回通知する。WARN は **前回実行から WARN 集合が変化したときだけ** 通知する。

理由: index 滞留のような「既知だが放置されている WARN」が毎平日飛ぶと
通知そのものが読まれなくなり、本スクリプトの目的（配達）が自壊する。
一方で新しい WARN は 1 度だけ確実に届く必要がある。
前回の WARN 集合は `data/health_last_run.json` に保存して差分を取る。

`--notify-warn` で毎回送信、`--no-notify` で送信抑止に切り替えられる。

## 使い方

    python scripts/health_check.py                # 通常実行（cron 想定）
    python scripts/health_check.py --skip-network # 疎通チェックを飛ばす
    python scripts/health_check.py --no-notify    # LINE 送信しない
    python scripts/health_check.py --inject-fail  # LINE 配達経路の疎通確認用

## 終了コード

    0 — 全て OK / WARN のみ（cron 的には正常終了）
    1 — FAIL が 1 件以上
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

# crontab の文字列解析は scripts/cron_inspect.py に分離してある
# （解析ロジック単体でテストするため）。read_crontab はモジュール属性として
# 再公開し、テストから差し替えられる状態を保つ。
from scripts.cron_inspect import (  # noqa: E402
    cp_destinations,
    cron_cwd,
    extract_cron_commands,
    is_path_like,
    read_crontab,
    redirect_targets,
    resolve_cron_path,
    tokenize,
)

# ────────────────────────────────────────────────────────────
# パス定数
#
# モジュールグローバルとして持ち、参照時に解決する。
# tests/conftest.py の isolate_state_files がここを tmp へ張り替えるため、
# 関数内で ROOT から組み立て直してはいけない。
# ────────────────────────────────────────────────────────────

HEALTH_LOG_PATH      = ROOT / "logs" / "health.log"
LAST_RUN_PATH        = ROOT / "data" / "health_last_run.json"
PORTFOLIO_PATH       = ROOT / "data" / "portfolio.json"
POSITIONS_INDEX_PATH = ROOT / "data" / "training" / "open_positions_index.json"
TRAINING_DATA_PATH   = ROOT / "data" / "training" / "training_data.jsonl"

# ────────────────────────────────────────────────────────────
# 閾値定数
# ────────────────────────────────────────────────────────────

# training_data.jsonl の最終レコードがこの営業日数以上前なら WARN。
# cron は平日 1 回。1 営業日の欠損は「祝日 / 市場休場」で普通に起きるため 3 とする
# （2 営業日連続の欠損まで許容し、3 営業日目で異常とみなす）。
TRAINING_DATA_MAX_AGE_BUSINESS_DAYS = 3

# health_check 自体の実行間隔。平日 1 回想定なので、
# 2 営業日以上空いていたら「前回どこかで実行されなかった」とみなす。
HEALTH_RUN_MAX_GAP_BUSINESS_DAYS = 2

# LLM 疎通確認に使う最小プロンプト（課金・レイテンシを最小化する）
LLM_PROBE_PROMPT = "Reply with OK only."

# 通知本文の 1 行あたり最大長（LINE の可読性と情報漏洩面積の抑制）
_DETAIL_MAX_LEN = 160

# .env の必須キー。欠落すると代替手段が無く、機能が丸ごと死ぬもの。
# 値は絶対にログ・通知へ出さない。存在有無のみを見る。
# タプルは「いずれか 1 つあれば可」を表す（Alpaca は正式名とエイリアスがある）。
_REQUIRED_ENV_KEYS: tuple[tuple[str, ...], ...] = (
    ("APCA_API_KEY_ID", "ALPACA_API_KEY"),
    ("APCA_API_SECRET_KEY", "ALPACA_SECRET_KEY"),
    ("LINE_ACCESS_TOKEN",),   # 欠けると本スクリプトの通知経路そのものが死ぬ
    ("LINE_USER_ID",),
)

# 欠落しても既定値で動くが、その既定値が過去に事故を起こしたキー。
# 「暗黙のデフォルトで動いている」状態を可視化するため WARN で報告する。
_RECOMMENDED_ENV_KEYS: tuple[str, ...] = (
    "ALPACA_PAPER_TRADING",  # 未設定だと True（ペーパー）扱い。安全側だが明示すべき
    "FORCE_GEMINI",
    "DISABLE_GEMINI",
    "GEMINI_MODEL",          # 未設定だと llm_factory の暗黙既定に落ちる。モデル廃止で無言死する
)

# 通知・ログから値を伏せる対象。ここに挙げた環境変数の「値」が
# 出力文字列に混入していたら *** に置換する（例外メッセージ経由の漏洩対策）。
_SECRET_ENV_KEYS: tuple[str, ...] = (
    "APCA_API_KEY_ID", "APCA_API_SECRET_KEY",
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
    "GOOGLE_API_KEY", "LINE_ACCESS_TOKEN", "LINE_USER_ID",
)

OK, WARN, FAIL = "OK", "WARN", "FAIL"


# ────────────────────────────────────────────────────────────
# 結果型
# ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CheckResult:
    """1 チェックの結果。

    Attributes:
        key:    安定した ASCII 識別子。ログの grep と WARN 差分検出に使う。
                表示都合で label を変えても key は変えないこと。
        label:  人間向けの表示名。
        level:  OK | WARN | FAIL
        detail: 詳細。認証情報を含めてはならない（_redact で保険をかける）。
    """
    key:    str
    label:  str
    level:  str
    detail: str


@dataclass
class HealthReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.level == FAIL]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.level == WARN]

    @property
    def overall(self) -> str:
        if self.failures:
            return FAIL
        if self.warnings:
            return WARN
        return OK

    @property
    def warn_keys(self) -> list[str]:
        return sorted(r.key for r in self.warnings)


@dataclass
class _Context:
    """チェック間で共有する取得済みデータ。

    B-5 で取った Alpaca の保有銘柄を C-6 で再利用するなど、
    外部 API を二度叩かないために使う。取得できていなければ None のまま。
    """
    alpaca_symbols: set[str] | None = None
    crontab_lines:  list[str] | None = None


# ────────────────────────────────────────────────────────────
# 共通ユーティリティ
# ────────────────────────────────────────────────────────────

def _redact(text: str) -> str:
    """認証情報の値が混入していたら伏せ字にし、長さを切り詰める。

    例外メッセージには API キーが混ざりうる（URL クエリや SDK のエラー本文）。
    通知本文へ載せる前の最後の保険としてここを必ず通す。
    """
    redacted = str(text)
    for key in _SECRET_ENV_KEYS:
        value = os.getenv(key, "")
        # 短い値まで置換すると無関係な文字列を壊すため、十分長いものだけ対象にする
        if len(value) >= 8:
            redacted = redacted.replace(value, "***")
    redacted = " ".join(redacted.split())  # 改行・連続空白を潰す
    if len(redacted) > _DETAIL_MAX_LEN:
        redacted = redacted[:_DETAIL_MAX_LEN] + "…"
    return redacted


def _business_days_between(start: date, end: date) -> int:
    """start（除く）から end（含む）までの営業日数（土日を除く単純計算）。

    米国市場の祝日は考慮しない。祝日をまたぐと 1 日ぶん過大に出るが、
    閾値側に余裕（3 営業日）を持たせて誤報を避けている。
    """
    if end <= start:
        return 0
    days = 0
    cursor = start
    while cursor < end:
        cursor = date.fromordinal(cursor.toordinal() + 1)
        if cursor.weekday() < 5:  # 0=月 … 4=金
            days += 1
    return days


def _safe(key: str, label: str, fn, ctx: _Context) -> CheckResult:
    """チェック関数を実行し、例外を握らず「その項目の FAIL」に変換する。

    設計原則 4（1 項目の失敗で全体を止めない）の実装本体。
    例外を握り潰すのではなく FAIL として可視化する点が重要。
    """
    try:
        return fn(ctx)
    except Exception as e:
        return CheckResult(key, label, FAIL,
                           _redact(f"チェック自体が例外で失敗: {type(e).__name__}: {e}"))


def _read_json(path: Path) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ────────────────────────────────────────────────────────────
# A. 設定・ファイル系（外部通信なし）
# ────────────────────────────────────────────────────────────

def check_cron_scripts(ctx: _Context) -> CheckResult:
    """A-1. cron が実行するスクリプトが実在するか。

    今回の「半年間 No such file or directory」を直接検知する項目。
    """
    key, label = "cron_scripts", "cron 登録スクリプトの実在"
    if ctx.crontab_lines is None:
        return CheckResult(key, label, WARN, "crontab を読めないため未検証")

    commands = extract_cron_commands(ctx.crontab_lines)
    if not commands:
        # 本番でこれが出たら「ボットが一切スケジュールされていない」という
        # 最も静かな障害。OK にしてはいけない。
        return CheckResult(key, label, WARN,
                           "cron ジョブが 1 件も登録されていません")

    missing: list[str] = []
    unreachable: list[str] = []
    not_executable: list[str] = []
    checked = 0

    for command in commands:
        tokens = tokenize(command)
        cwd = cron_cwd(tokens)
        for index, token in enumerate(tokens):
            if not is_path_like(token):
                continue
            checked += 1
            cron_path, project_path = resolve_cron_path(token, cwd)
            if cron_path is None and project_path is None:
                missing.append(token)
            elif cron_path is None:
                unreachable.append(token)
            elif index == 0 and cron_path.is_file() and not os.access(cron_path, os.X_OK):
                # 先頭トークン = インタプリタ指定なしの直接実行。実行ビットが無いと
                # 「Permission denied」で無言に落ちる
                not_executable.append(token)

    if missing:
        return CheckResult(key, label, FAIL,
                           f"{len(missing)} 件のパスが存在しません: {', '.join(missing)}")
    problems = []
    if unreachable:
        problems.append(
            f"cron の CWD 基準で解決できない（プロジェクト基準でのみ存在）: {', '.join(unreachable)}")
    if not_executable:
        problems.append(f"実行ビットなし: {', '.join(not_executable)}")
    if problems:
        return CheckResult(key, label, WARN, " / ".join(problems))
    return CheckResult(key, label, OK, f"{checked} 件のパスを検証、全て実在")


def check_cron_output_dirs(ctx: _Context) -> CheckResult:
    """A-2. cron のリダイレクト先・コピー先ディレクトリが実在するか。

    `backups/` が無く日次バックアップの cp が毎晩失敗していた件を検知する。
    """
    key, label = "cron_output_dirs", "cron 出力先ディレクトリの実在"
    if ctx.crontab_lines is None:
        return CheckResult(key, label, WARN, "crontab を読めないため未検証")

    missing: list[str] = []
    checked = 0

    for command in extract_cron_commands(ctx.crontab_lines):
        tokens = tokenize(command)
        cwd = cron_cwd(tokens)
        base = cwd if cwd is not None else Path.home()

        for target in redirect_targets(command):
            checked += 1
            directory = (base / Path(target).expanduser()).parent
            if not directory.is_dir():
                missing.append(f"{target} → {directory}")

        for dest in cp_destinations(tokens):
            checked += 1
            path = base / Path(dest).expanduser()
            # 末尾 / はディレクトリ確定。そうでなければ親ディレクトリを見る
            directory = path if dest.endswith("/") else path.parent
            if not directory.is_dir():
                missing.append(f"{dest} → {directory}")

    if missing:
        return CheckResult(key, label, FAIL,
                           f"{len(missing)} 件の出力先ディレクトリが存在しません: "
                           f"{', '.join(missing)}")
    return CheckResult(key, label, OK, f"{checked} 件の出力先を検証、全て実在")


def check_env_keys(ctx: _Context) -> CheckResult:
    """A-3. .env の必須キーが設定されているか。

    **値は一切読み出さない・出力しない。** os.getenv() の真偽のみを見る。
    """
    key, label = "env_keys", ".env 必須キー"

    missing_required = [
        " / ".join(aliases) for aliases in _REQUIRED_ENV_KEYS
        if not any(os.getenv(name) for name in aliases)
    ]
    if missing_required:
        return CheckResult(key, label, FAIL,
                           f"必須キーが未設定: {', '.join(missing_required)}")

    # LLM 認証情報は使用中のバックエンドによって必要性が変わる
    force_gemini   = os.getenv("FORCE_GEMINI", "false").lower() == "true"
    disable_gemini = os.getenv("DISABLE_GEMINI", "false").lower() == "true"
    if not disable_gemini and not os.getenv("GOOGLE_API_KEY"):
        level = FAIL if force_gemini else WARN
        reason = ("FORCE_GEMINI=true だが GOOGLE_API_KEY 未設定"
                  if force_gemini else
                  "GOOGLE_API_KEY 未設定 — Ollama 障害時のフォールバックが効かない")
        return CheckResult(key, label, level, reason)

    missing_recommended = [k for k in _RECOMMENDED_ENV_KEYS if os.getenv(k) is None]
    if missing_recommended:
        return CheckResult(key, label, WARN,
                           f"暗黙のデフォルトで動作中（明示推奨）: "
                           f"{', '.join(missing_recommended)}")
    return CheckResult(key, label, OK, "必須キー・推奨キーとも設定済み")


# ────────────────────────────────────────────────────────────
# B. 疎通系（実際に 1 回叩く）
# ────────────────────────────────────────────────────────────

def check_llm_connectivity(ctx: _Context) -> CheckResult:
    """B-4. LLM に実際に短いプロンプトを 1 回投げ、応答が返るか確認する。

    「設定が存在する」ではなく「実際に応答が返る」を検証する。
    run_weekly_reflection.py が廃止モデル名の直書きで半年間無言に失敗していた
    ような事故は、設定の存在確認では絶対に捕まらない。
    """
    key, label = "llm_connectivity", "LLM 疎通"
    from skills.llm_factory import get_llm

    llm, source = get_llm(temperature=0)
    response = llm.invoke(LLM_PROBE_PROMPT)
    content = getattr(response, "content", "")
    if not str(content).strip():
        return CheckResult(key, label, FAIL, f"source={source} だが応答が空")
    return CheckResult(key, label, OK,
                       f"source={source} 応答={_redact(str(content))[:40]}")


def _fetch_alpaca_positions(client) -> list[dict]:
    """Alpaca の保有ポジションを「失敗を失敗として」取得する。

    `AlpacaClient.get_positions()` は例外を握って `[]` を返すため、
    そのまま使うと「認証失敗」と「保有 0 件」を区別できない。
    本スクリプトの目的はまさにその区別なので、例外が伝播する内部クライアントを
    直接使い、失敗時は呼び出し元へ例外を投げる。
    """
    trading_client = getattr(client, "_tc", None)
    if trading_client is None:
        # 将来 get_positions() 側が例外を投げるよう修正されたらこちらで足りる
        return client.get_positions()
    return [{"symbol": p.symbol, "qty": int(float(p.qty))}
            for p in trading_client.get_all_positions()]


def check_alpaca_connectivity(ctx: _Context) -> CheckResult:
    """B-5. Alpaca の読み取り系 API のみを叩いて疎通を確認する。

    **発注系メソッド（place_buy / place_sell / submit_order）には触れない。**
    認証失敗と「保有 0 件」を明確に区別して報告する。
    """
    key, label = "alpaca_connectivity", "Alpaca 疎通"
    from tools.alpaca_client import AlpacaClient, _PAPER

    client  = AlpacaClient()
    account = client.get_account()
    if "error" in account:
        return CheckResult(key, label, FAIL,
                           _redact(f"口座情報を取得できません（認証失敗の可能性）: "
                                   f"{account['error']}"))

    try:
        positions = _fetch_alpaca_positions(client)
    except Exception as e:
        # ここで OK にすると「保有 0 件」と誤報し、C-6 の突合まで無意味になる
        return CheckResult(key, label, FAIL,
                           _redact(f"口座は取得できたがポジション取得に失敗: "
                                   f"{type(e).__name__}: {e}"))

    ctx.alpaca_symbols = {p["symbol"].upper() for p in positions}
    mode = "PAPER" if _PAPER else "LIVE"
    holding = (f"保有 {len(positions)} 件: "
               f"{', '.join(sorted(ctx.alpaca_symbols))}"
               if positions else "保有 0 件（取得成功・ポジションなし）")
    return CheckResult(key, label, OK,
                       f"mode={mode} equity=${account.get('equity', 0):,.0f} {holding}")


# ────────────────────────────────────────────────────────────
# C. データ整合性系
# ────────────────────────────────────────────────────────────

def _portfolio_open_tickers() -> set[str]:
    """portfolio.json の OPEN ポジションの ticker 集合を返す。"""
    if not Path(PORTFOLIO_PATH).exists():
        raise FileNotFoundError(f"{PORTFOLIO_PATH} が存在しません")
    data = _read_json(PORTFOLIO_PATH)
    positions = data.get("positions", []) if isinstance(data, dict) else []
    return {
        str(p["ticker"]).upper() for p in positions
        if p.get("status", "OPEN") == "OPEN" and p.get("ticker")
    }


def check_portfolio_vs_alpaca(ctx: _Context) -> CheckResult:
    """C-6. portfolio.json と Alpaca の実保有を突合する。

    不一致は WARN（報告のみ）。自動修復はしない — sync_portfolio() は
    portfolio.json を書き換えるため、読み取り専用の原則から絶対に呼ばない。
    """
    key, label = "portfolio_vs_alpaca", "portfolio.json ↔ Alpaca 突合"
    if ctx.alpaca_symbols is None:
        return CheckResult(key, label, WARN,
                           "Alpaca 保有を取得できなかったため未検証")

    local  = _portfolio_open_tickers()
    remote = ctx.alpaca_symbols
    if local == remote:
        return CheckResult(key, label, OK, f"一致（{len(local)} 銘柄）")

    details = []
    if local - remote:
        details.append(f"portfolio.json のみ: {', '.join(sorted(local - remote))}")
    if remote - local:
        details.append(f"Alpaca のみ: {', '.join(sorted(remote - local))}")
    return CheckResult(key, label, WARN, " / ".join(details))


def _last_jsonl_record(path: Path, tail_bytes: int = 65_536) -> dict | None:
    """JSONL の最終レコードを返す。ファイル全体は読まない。"""
    file_path = Path(path)
    if not file_path.exists() or file_path.stat().st_size == 0:
        return None
    with file_path.open("rb") as f:
        size = file_path.stat().st_size
        f.seek(max(0, size - tail_bytes))
        chunk = f.read().decode("utf-8", errors="ignore")
    for line in reversed(chunk.splitlines()):
        if line.strip():
            return json.loads(line)
    return None


def check_training_data_freshness(ctx: _Context) -> CheckResult:
    """C-7. training_data.jsonl の最終レコードが古すぎないか。"""
    key, label = "training_data_freshness", "training_data.jsonl の鮮度"

    record = _last_jsonl_record(TRAINING_DATA_PATH)
    if record is None:
        return CheckResult(key, label, FAIL,
                           f"レコードがありません: {Path(TRAINING_DATA_PATH).name}")
    raw_date = record.get("date")
    if not raw_date:
        return CheckResult(key, label, WARN, "最終レコードに date フィールドがありません")

    last_date = date.fromisoformat(str(raw_date)[:10])
    age = _business_days_between(last_date, date.today())
    detail = f"最終レコード {last_date} （{age} 営業日前）"
    if age >= TRAINING_DATA_MAX_AGE_BUSINESS_DAYS:
        return CheckResult(key, label, WARN,
                           f"{detail} — 閾値 {TRAINING_DATA_MAX_AGE_BUSINESS_DAYS} 営業日を超過。"
                           f" パイプラインが停止している可能性")
    return CheckResult(key, label, OK, detail)


def check_positions_index_staleness(ctx: _Context) -> CheckResult:
    """C-8. open_positions_index.json に決済済み銘柄が滞留していないか。

    滞留エントリは `update_outcome()` の FIFO 対象として残り続け、
    無関係な決済に誤って紐づく汚染源になる。報告のみで削除はしない。
    """
    key, label = "positions_index_staleness", "open_positions_index.json の滞留"

    if not Path(POSITIONS_INDEX_PATH).exists():
        return CheckResult(key, label, OK, "index が未作成（初回は正常）")
    index = _read_json(POSITIONS_INDEX_PATH)
    if not isinstance(index, dict) or not index:
        return CheckResult(key, label, OK, "滞留なし（index は空）")

    open_tickers = _portfolio_open_tickers()
    stale = {
        ticker: len(entries) if isinstance(entries, list) else 1
        for ticker, entries in index.items()
        if ticker.upper() not in open_tickers
    }
    if not stale:
        total = sum(len(v) if isinstance(v, list) else 1 for v in index.values())
        return CheckResult(key, label, OK, f"滞留なし（{total} 件全てが OPEN ポジション）")

    listed = ", ".join(f"{t}×{n}" for t, n in sorted(stale.items()))
    return CheckResult(key, label, WARN,
                       f"portfolio.json に無い銘柄が {sum(stale.values())} 件滞留: {listed}")


# ────────────────────────────────────────────────────────────
# D. 自己申告
# ────────────────────────────────────────────────────────────

def read_last_run() -> dict:
    """前回実行の記録を読む。壊れていても実行は止めない。"""
    path = Path(LAST_RUN_PATH)
    if not path.exists():
        return {}
    try:
        data = _read_json(path)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def check_self_report(ctx: _Context, previous: dict) -> CheckResult:
    """D-9. 前回実行からの間隔を検証する。

    health_check 自体が止まっていたことを、次に動いたときに検知するための項目。
    「動かなくなったことが分かる」ようにするのが目的。
    """
    key, label = "self_report", "health_check の実行間隔"

    last_run_at = previous.get("last_run_at")
    if not last_run_at:
        return CheckResult(key, label, OK, "初回実行（前回記録なし）")

    last_date = datetime.fromisoformat(str(last_run_at)).date()
    gap = _business_days_between(last_date, date.today())
    detail = f"前回実行 {last_run_at[:19]} （{gap} 営業日前）"
    if gap >= HEALTH_RUN_MAX_GAP_BUSINESS_DAYS:
        return CheckResult(key, label, WARN,
                           f"{detail} — 平日 1 回の想定より間隔が空いています。"
                           f" health_check 自体が実行されていなかった可能性")
    return CheckResult(key, label, OK, detail)


def write_last_run(report: HealthReport) -> None:
    """実行時刻と WARN 集合を記録する（本スクリプト唯一の状態書き込み）。

    WARN 集合を残すのは、次回実行時に「新しく出現した WARN」だけを
    通知するため（毎回同じ WARN が飛ぶと通知が読まれなくなる）。
    """
    path = Path(LAST_RUN_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "overall":     report.overall,
        "warn_keys":   report.warn_keys,
        "fail_keys":   sorted(r.key for r in report.failures),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


# ────────────────────────────────────────────────────────────
# 実行・出力・通知
# ────────────────────────────────────────────────────────────

def run_health_check(skip_network: bool = False,
                     inject_fail: bool = False) -> tuple[HealthReport, dict]:
    """全チェックを実行して報告書を返す。

    Returns:
        (report, previous_last_run)
        previous_last_run は WARN 差分の判定に使う（呼び出し側で参照する）。
    """
    ctx = _Context()
    lines, note = read_crontab()
    ctx.crontab_lines = lines
    previous = read_last_run()

    report = HealthReport()
    if lines is None:
        report.add(CheckResult("crontab_readable", "crontab の読み取り", WARN,
                               f"{note} — cron 系チェックは実施されません"))

    checks: list[tuple[str, str, object]] = [
        ("cron_scripts",              "cron 登録スクリプトの実在",    check_cron_scripts),
        ("cron_output_dirs",          "cron 出力先ディレクトリの実在", check_cron_output_dirs),
        ("env_keys",                  ".env 必須キー",                check_env_keys),
    ]
    if not skip_network:
        checks += [
            ("llm_connectivity",      "LLM 疎通",                     check_llm_connectivity),
            ("alpaca_connectivity",   "Alpaca 疎通",                  check_alpaca_connectivity),
        ]
    checks += [
        ("portfolio_vs_alpaca",       "portfolio.json ↔ Alpaca 突合", check_portfolio_vs_alpaca),
        ("training_data_freshness",   "training_data.jsonl の鮮度",   check_training_data_freshness),
        ("positions_index_staleness", "open_positions_index の滞留",  check_positions_index_staleness),
    ]

    for key, label, fn in checks:
        report.add(_safe(key, label, fn, ctx))

    report.add(_safe("self_report", "health_check の実行間隔",
                     lambda c: check_self_report(c, previous), ctx))

    if inject_fail:
        report.add(CheckResult("inject_fail", "通知経路の疎通テスト", FAIL,
                               "--inject-fail による意図的な FAIL（配達経路の確認用）"))
    return report, previous


def append_log(report: HealthReport, notified: bool) -> None:
    """logs/health.log に人間が読める形式で追記する。

    1 行 1 項目。`grep ' | FAIL | '` で過去の傾向が追えることを意図している。
    """
    path = Path(HEALTH_LOG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        f"{stamp} | {r.level:<4} | {r.key:<26} | {r.label} — {_redact(r.detail)}"
        for r in report.results
    ]
    lines.append(
        f"{stamp} | {report.overall:<4} | {'SUMMARY':<26} | "
        f"ok={len(report.results) - len(report.warnings) - len(report.failures)} "
        f"warn={len(report.warnings)} fail={len(report.failures)} "
        f"notified={'yes' if notified else 'no'}"
    )
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def build_notification(report: HealthReport, new_warnings: list[CheckResult]) -> str:
    """LINE 通知の本文を組み立てる。

    認証情報は `_redact()` を必ず通す。値そのものは一切載せない。
    """
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    header = ("🚨【ECC ヘルスチェック】異常検知" if report.failures
              else "⚠️【ECC ヘルスチェック】新しい警告")
    lines = [f"{header} {stamp}"]

    if report.failures:
        lines.append(f"\n❌ FAIL {len(report.failures)} 件")
        lines += [f"・{r.label}: {_redact(r.detail)}" for r in report.failures]
    if new_warnings:
        lines.append(f"\n⚠️ 新規 WARN {len(new_warnings)} 件")
        lines += [f"・{r.label}: {_redact(r.detail)}" for r in new_warnings]

    lines.append(f"\n詳細: logs/health.log （自動修復は行いません）")
    return "\n".join(lines)


def notify_if_needed(report: HealthReport, previous: dict,
                     no_notify: bool = False,
                     notify_warn: bool = False) -> tuple[bool, list[CheckResult]]:
    """通知ポリシーを適用して LINE 送信する。

    - FAIL が 1 件でもあれば必ず送信する
    - WARN は前回から新しく出現したものだけ送信する（--notify-warn で毎回）
    - 全て OK なら送信しない（静かな成功は良い）

    Returns:
        (送信したか, 新規 WARN の一覧)
    """
    previous_warn_keys = set(previous.get("warn_keys", []))
    new_warnings = ([r for r in report.warnings] if notify_warn else
                    [r for r in report.warnings if r.key not in previous_warn_keys])

    should_notify = bool(report.failures) or bool(new_warnings)
    if not should_notify or no_notify:
        return False, new_warnings

    from engine.notify import send_line_message
    send_line_message(build_notification(report, new_warnings))
    return True, new_warnings


def print_report(report: HealthReport) -> None:
    icons = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}
    width = 68
    print(f"\n{'═' * width}")
    print("  ECC Health Check")
    print(f"{'═' * width}")
    for r in report.results:
        print(f"  {icons[r.level]} {r.label:<28} {_redact(r.detail)}")
    print(f"{'─' * width}")
    print(f"  overall={report.overall}  "
          f"FAIL={len(report.failures)}  WARN={len(report.warnings)}")
    print(f"{'═' * width}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ECC ヘルスチェック（読み取り専用・異常を LINE に配達する）")
    parser.add_argument("--no-notify", action="store_true",
                        help="LINE 通知を行わない")
    parser.add_argument("--notify-warn", action="store_true",
                        help="WARN を毎回通知する（既定は新規 WARN のみ）")
    parser.add_argument("--skip-network", action="store_true",
                        help="LLM / Alpaca の疎通チェックを省略する")
    parser.add_argument("--inject-fail", action="store_true",
                        help="意図的に FAIL を 1 件追加し、LINE 配達経路を検証する")
    parser.add_argument("--quiet", action="store_true",
                        help="標準出力への表形式レポートを抑制する")
    args = parser.parse_args(argv)

    report, previous = run_health_check(skip_network=args.skip_network,
                                        inject_fail=args.inject_fail)
    notified, _ = notify_if_needed(report, previous,
                                   no_notify=args.no_notify,
                                   notify_warn=args.notify_warn)
    append_log(report, notified)
    write_last_run(report)

    if not args.quiet:
        print_report(report)
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
