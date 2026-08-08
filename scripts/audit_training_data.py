#!/usr/bin/env python3
"""
audit_training_data.py — 学習データの汚染・破損範囲を洗い出す（読み取り専用）

2026-08-08、テストが本番の ExitAgent 経路を実行し、実在しない AAPL ポジションに
対して `_record_exit()` → `update_outcome()` が呼ばれた疑いがある。
`update_outcome()` は FIFO で `open_positions_index.json` からエントリを pop する
ため、対応レコードにラベルが付かないまま index から消えている恐れがある。

このスクリプトは **何も修復しない**。何が壊れているかを報告するだけ。

読み取り専用の保証:
  - ファイルの書き込み・削除・修正を一切行わない（出力は標準出力のみ）
  - Alpaca は `get_positions()`（list_positions 相当）の読み取りのみ。
    発注系メソッド（place_buy / place_sell / submit_order 等）には触れない
  - LLM 呼び出しなし

使い方:
    python scripts/audit_training_data.py
    python scripts/audit_training_data.py --file data/training/training_data_prod.jsonl
    python scripts/audit_training_data.py --no-alpaca        # オフライン
    python scripts/audit_training_data.py --incident-date 2026-08-08
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR   = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

TRAINING_DIR    = PROJECT_ROOT / "data" / "training"
DEFAULT_JSONL   = TRAINING_DIR / "training_data.jsonl"
POSITIONS_INDEX = TRAINING_DIR / "open_positions_index.json"
OBSIDIAN_LOGS   = PROJECT_ROOT / "data" / "knowledge_base" / "obsidian_logs"

_W = 78

# Alpaca 上で保有しているはずの銘柄（突き合わせの期待値。--expect で上書き可）
DEFAULT_EXPECTED_HOLDINGS = ("NOC", "ISRG", "LRCX")


# ─────────────────────────────────────────────────────────────
# 表示ヘルパー
# ─────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print()
    print("=" * _W)
    print(f"  {title}")
    print("=" * _W)


def _sub(title: str) -> None:
    print()
    print(f"  ── {title} " + "─" * max(0, _W - len(title) - 8))


# ─────────────────────────────────────────────────────────────
# 読み込み
# ─────────────────────────────────────────────────────────────

def load_records(path: Path) -> tuple[list[dict], list[tuple[int, str]]]:
    """JSONL を読み込む。(レコード一覧, パース失敗行) を返す。"""
    records: list[dict] = []
    broken:  list[tuple[int, str]] = []
    if not path.exists():
        print(f"  [!] ファイルが存在しません: {path}")
        return records, broken

    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                broken.append((lineno, str(e)))
                continue
            rec["_lineno"] = lineno
            records.append(rec)
    return records, broken


def load_index() -> dict:
    if not POSITIONS_INDEX.exists():
        return {}
    try:
        with open(POSITIONS_INDEX, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [!] open_positions_index.json の読み込みに失敗: {e}")
        return {}


def _order_state(rec: dict) -> str:
    """レコードの発注状態を分類する。"""
    order = (rec.get("manager_output") or {}).get("order")
    if not order:
        return "発注情報なし"
    if order.get("dry_run"):
        return "dry_run"
    if order.get("skipped"):
        return f"skipped ({order.get('skip_reason', '理由不明')})"
    if order.get("success"):
        return "発注成功"
    return "発注失敗"


def _is_real(rec: dict) -> bool:
    """本番実行（mock でも hybrid でもない）レコードか。"""
    return not rec.get("mock_mode") and not rec.get("hybrid_mode")


# ─────────────────────────────────────────────────────────────
# 1. training_data.jsonl の集計
# ─────────────────────────────────────────────────────────────

def report_summary(records: list[dict], broken: list, incident_date: str) -> None:
    _section("1. training_data.jsonl の集計")

    print(f"  総レコード数: {len(records)} 件")
    if broken:
        print(f"  [!] パース不能な行: {len(broken)} 件")
        for lineno, err in broken:
            print(f"      line {lineno}: {err}")

    # ── 実行モード別の内訳
    _sub("実行モード別の内訳")
    buckets = {
        "mock_mode=True":                    [r for r in records if r.get("mock_mode")],
        "hybrid_mode=True":                  [r for r in records if r.get("hybrid_mode")],
        "本番 (mock=False, hybrid=False)":   [r for r in records if _is_real(r)],
    }
    for label, rows in buckets.items():
        print(f"  {label:<34} {len(rows):>4} 件")

    missing_hybrid = [r for r in records if "hybrid_mode" not in r]
    if missing_hybrid:
        print(f"  [注] hybrid_mode キーを持たない旧スキーマのレコード: {len(missing_hybrid)} 件"
              f" (line {', '.join(str(r['_lineno']) for r in missing_hybrid[:10])})")

    # ── outcome_label の内訳
    _sub("outcome_label の内訳")
    labeled = [r for r in records if r.get("outcome_label")]
    wins    = [r for r in labeled if r["outcome_label"] == "WIN"]
    losses  = [r for r in labeled if r["outcome_label"] == "LOSS"]
    others  = [r for r in labeled if r["outcome_label"] not in ("WIN", "LOSS")]

    print(f"  ラベル付き        {len(labeled):>4} 件  (WIN {len(wins)} / LOSS {len(losses)}"
          + (f" / その他 {len(others)}" if others else "") + ")")
    print(f"  ラベルなし (null) {len(records) - len(labeled):>4} 件")
    for r in others:
        print(f"      [!] 想定外のラベル: line {r['_lineno']} {r.get('ticker')} "
              f"→ {r['outcome_label']!r}")

    # ── 事故当日に書かれた疑いのあるレコード
    _sub(f"outcome_updated_at が {incident_date} のレコード（事故で書かれた疑い）")
    suspects = [
        r for r in records
        if str(r.get("outcome_updated_at") or "").startswith(incident_date)
    ]
    if not suspects:
        print(f"  該当なし — {incident_date} に outcome を書かれたレコードは 0 件")
    else:
        print(f"  [!] {len(suspects)} 件が該当。全件詳細:")
        for r in suspects:
            _print_record_detail(r)

    # ── 事故当日に「追記」されたレコード（別種の汚染）
    _sub(f"date / created_at が {incident_date} のレコード（事故で追記された疑い）")
    appended = [
        r for r in records
        if str(r.get("date") or "").startswith(incident_date)
        or str(r.get("created_at") or "").startswith(incident_date)
    ]
    if not appended:
        print(f"  該当なし — {incident_date} に追記されたレコードは 0 件")
    else:
        print(f"  [!] {len(appended)} 件が該当。全件詳細:")
        for r in appended:
            _print_record_detail(r)


def _print_record_detail(r: dict) -> None:
    mo = r.get("manager_output") or {}
    print()
    print(f"    line {r['_lineno']}  record_id: {r.get('record_id')}")
    print(f"      ticker           : {r.get('ticker')}")
    print(f"      date / created_at: {r.get('date')} / {r.get('created_at')}")
    print(f"      session_id       : {r.get('session_id')}")
    print(f"      mock / hybrid    : {r.get('mock_mode')} / {r.get('hybrid_mode')}")
    print(f"      decision         : {mo.get('decision')}  "
          f"(score={mo.get('score')}, is_strong_buy={mo.get('is_strong_buy')})")
    print(f"      order            : {_order_state(r)}")
    print(f"      outcome_label    : {r.get('outcome_label')}")
    print(f"      outcome_updated  : {r.get('outcome_updated_at')}")
    outcome = r.get("outcome")
    if outcome:
        print(f"      outcome          : {json.dumps(outcome, ensure_ascii=False)}")


# ─────────────────────────────────────────────────────────────
# 2. 孤児レコードの検出
# ─────────────────────────────────────────────────────────────

def report_orphans(records: list[dict], index: dict) -> None:
    _section("2. 孤児レコードの検出（二度とラベルが付かないレコード）")

    indexed_ids = {
        entry.get("record_id")
        for entries in index.values()
        for entry in entries
    }

    candidates = [
        r for r in records
        if (r.get("manager_output") or {}).get("is_strong_buy")
        and _is_real(r)
        and r.get("outcome_label") is None
        and r.get("record_id") not in indexed_ids
    ]

    print(f"  条件: is_strong_buy=true / mock=false / hybrid=false /"
          f" outcome_label=null / index に record_id なし")
    print(f"  該当: {len(candidates)} 件")

    if not candidates:
        print("  → 孤児レコードなし")
        return

    # 発注状態で仕分けする。dry_run / skipped は「そもそも index 未登録が正常」
    # であり事故とは無関係。実発注済みなのに孤児になっているものが本物の被害。
    real_orders = [r for r in candidates if _order_state(r) == "発注成功"]
    no_orders   = [r for r in candidates if _order_state(r) != "発注成功"]

    _sub(f"[A] 実発注済みなのに孤児 — 真の被害候補: {len(real_orders)} 件")
    if not real_orders:
        print("  該当なし")
    else:
        _print_orphan_table(real_orders)

    _sub(f"[B] 発注していない（dry_run / skipped / 発注失敗）: {len(no_orders)} 件")
    print("  ※ index 未登録が仕様どおりの正常動作。ラベルが付かないのは想定内。")
    if no_orders:
        _print_orphan_table(no_orders)


def _print_orphan_table(rows: list[dict]) -> None:
    print()
    print(f"  {'line':>5}  {'date':<12} {'ticker':<8} {'record_id':<38} 発注状態")
    print("  " + "-" * (_W - 4))
    for r in rows:
        print(f"  {r['_lineno']:>5}  {str(r.get('date')):<12} {str(r.get('ticker')):<8} "
              f"{str(r.get('record_id')):<38} {_order_state(r)}")


# ─────────────────────────────────────────────────────────────
# 3. open_positions_index.json の現状
# ─────────────────────────────────────────────────────────────

def report_index(index: dict, records: list[dict], use_alpaca: bool,
                 expected: tuple[str, ...]) -> None:
    _section("3. open_positions_index.json の現状")

    by_id = {r.get("record_id"): r for r in records}

    if not index:
        print("  インデックスは空です（登録ポジションなし）")
    else:
        total = sum(len(v) for v in index.values())
        print(f"  登録ティッカー: {len(index)} 銘柄 / エントリ合計: {total} 件")
        print()
        print(f"  {'ticker':<8} {'entry_date':<12} {'entry_price':>12}  "
              f"{'record_id':<38} jsonl 側の状態")
        print("  " + "-" * (_W - 4))
        for ticker in sorted(index):
            for entry in index[ticker]:
                rid = entry.get("record_id")
                rec = by_id.get(rid)
                if rec is None:
                    state = "[!] jsonl に該当レコードなし"
                elif rec.get("outcome_label"):
                    state = f"[!] 既に {rec['outcome_label']} ラベル済（二重登録の疑い）"
                else:
                    state = "未ラベル（正常）"
                price = entry.get("entry_price")
                price_s = f"${price:,.2f}" if isinstance(price, (int, float)) else str(price)
                print(f"  {ticker:<8} {str(entry.get('entry_date')):<12} {price_s:>12}  "
                      f"{str(rid):<38} {state}")

    # ── Alpaca 実保有との突き合わせ
    _sub("Alpaca 実保有との突き合わせ")
    positions = _fetch_alpaca_positions(use_alpaca)

    if positions is None:
        print(f"  Alpaca 実 API 未取得。期待値で代替突き合わせします: {', '.join(expected)}")
        held = set(expected)
    else:
        held = {p["symbol"].upper() for p in positions}
        if not positions:
            print("  Alpaca 上の保有ポジション: 0 件")
        else:
            print(f"  Alpaca 上の保有ポジション: {len(positions)} 件")
            for p in positions:
                pl = p.get("unrealized_plpc")
                pl_s = f"{pl * 100:+.2f}%" if isinstance(pl, (int, float)) else "N/A"
                print(f"    {p['symbol']:<8} {p['qty']:>6} 株  "
                      f"平均取得 ${p['avg_entry_price']:,.2f}  含み損益 {pl_s}")

    indexed_tickers = set(index)
    src = "期待値" if positions is None else "Alpaca"
    print()
    print(f"  {'ticker':<8} index  {src:<6}  判定")
    print("  " + "-" * (_W - 4))
    for ticker in sorted(indexed_tickers | held):
        in_idx = ticker in indexed_tickers
        in_alp = ticker in held
        if in_idx and in_alp:
            verdict = "一致"
        elif in_idx and not in_alp:
            verdict = "[!] index にあるが実保有なし（決済済みだがラベル未付与の疑い）"
        else:
            verdict = "[!] 実保有だが index になし（ラベルが二度と付かない）"
        print(f"  {ticker:<8} {'✓' if in_idx else '−':^5}  {'✓' if in_alp else '−':^6}  {verdict}")

    if positions is None:
        print()
        print("  ※ 上記の右列は実 API の値ではなく期待値（--expect）です。")
        print("     実保有の確定には Alpaca 認証を通した再実行が必要です。")


def _fetch_alpaca_positions(use_alpaca: bool) -> list[dict] | None:
    """
    Alpaca の保有ポジションを読み取り専用で取得する。失敗時は None。

    `AlpacaClient.get_positions()` は例外を握って [] を返すため、
    「本当に 0 件」と「API エラー」が区別できない。監査では致命的な差なので、
    ここでは trading client の読み取りメソッドを直接呼んでエラーを表面化させる。
    呼ぶのは `get_all_positions()`（list_positions 相当）のみで、
    発注系メソッドには一切触れない。
    """
    if not use_alpaca:
        return None
    try:
        from tools.alpaca_client import AlpacaClient

        client = AlpacaClient()
        raw = client._tc.get_all_positions()   # 読み取り専用 API
        return [
            {
                "symbol":          p.symbol,
                "qty":             int(float(p.qty)),
                "avg_entry_price": float(p.avg_entry_price),
                "unrealized_plpc": float(p.unrealized_plpc) if p.unrealized_plpc else None,
            }
            for p in raw
        ]
    except Exception as e:  # noqa: BLE001 — 監査スクリプトなので握って継続
        print(f"  [!] Alpaca 参照に失敗しました（読み取りのみ試行）: {e}")
        print("      → 以下の Alpaca 列は「未取得」であり、0 件という意味ではありません。")
        return None


# ─────────────────────────────────────────────────────────────
# 4. obsidian_logs/ の整合性
# ─────────────────────────────────────────────────────────────

_FM_PATTERN = re.compile(r"^(\w+):\s*(.*)$", re.MULTILINE)
_SELL_LINK  = re.compile(r"\*\*売却ログ\*\*:\s*\[\[([^\]]+)\]\]")

_PENDING_VALUES = {"PENDING", "決済待ち", "未確定"}


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    return {m.group(1): m.group(2).strip() for m in _FM_PATTERN.finditer(text[3:end])}


def report_obsidian() -> None:
    _section("4. obsidian_logs/ の整合性")

    if not OBSIDIAN_LOGS.exists():
        print(f"  ディレクトリが存在しません: {OBSIDIAN_LOGS}")
        return

    logs: list[tuple[Path, dict, str]] = []
    for path in sorted(OBSIDIAN_LOGS.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"  [!] 読み込み失敗: {path.name}: {e}")
            continue
        logs.append((path, _parse_frontmatter(text), text))

    print(f"  ログ総数: {len(logs)} 件")
    existing_names = {p.name for p, _, _ in logs}

    # ── PENDING / 決済待ちのまま残っている BUY ログ
    _sub("outcome が PENDING / 決済待ち のまま残っている BUY ログ")
    pending = [
        (p, fm) for p, fm, _ in logs
        if fm.get("action") == "BUY" and fm.get("outcome") in _PENDING_VALUES
    ]
    if not pending:
        print("  該当なし")
    else:
        print(f"  {len(pending)} 件")
        print()
        print(f"  {'file':<42} {'ticker':<8} {'date':<12} outcome")
        print("  " + "-" * (_W - 4))
        for p, fm in pending:
            print(f"  {p.name:<42} {fm.get('ticker', '?'):<8} "
                  f"{fm.get('date', '?'):<12} {fm.get('outcome')}")

    # ── 対応する SELL ログが存在しない CLOSED ログ
    _sub("対応する SELL ログが存在しない CLOSED ログ")
    dangling: list[tuple[Path, dict, str]] = []
    for p, fm, text in logs:
        if fm.get("outcome") != "CLOSED" or fm.get("action") != "BUY":
            continue
        m = _SELL_LINK.search(text)
        if not m:
            dangling.append((p, fm, "売却ログへのリンクが本文にない"))
        elif m.group(1) not in existing_names:
            dangling.append((p, fm, f"リンク先が存在しない: {m.group(1)}"))

    if not dangling:
        print("  該当なし")
    else:
        print(f"  {len(dangling)} 件")
        print()
        print(f"  {'file':<42} {'ticker':<8} 問題")
        print("  " + "-" * (_W - 4))
        for p, fm, why in dangling:
            print(f"  {p.name:<42} {fm.get('ticker', '?'):<8} {why}")

    # ── 参考: SELL ログの一覧（対応 BUY が無いもの）
    _sub("参考: どの BUY ログからも参照されていない SELL ログ")
    referenced = {
        m.group(1) for _, _, text in logs for m in [_SELL_LINK.search(text)] if m
    }
    unreferenced = [
        p for p, fm, _ in logs
        if fm.get("action") == "SELL" and p.name not in referenced
    ]
    if not unreferenced:
        print("  該当なし")
    else:
        print(f"  {len(unreferenced)} 件（旧フォーマットで相互リンクがない可能性あり）")
        for p in unreferenced:
            print(f"    {p.name}")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="学習データの汚染・破損範囲を洗い出す（読み取り専用・修復しない）"
    )
    parser.add_argument(
        "--file", type=Path, default=DEFAULT_JSONL,
        help=f"監査する JSONL（デフォルト: {DEFAULT_JSONL.relative_to(PROJECT_ROOT)}）",
    )
    parser.add_argument(
        "--incident-date", default="2026-08-08",
        help="事故日 YYYY-MM-DD（デフォルト: 2026-08-08）",
    )
    parser.add_argument(
        "--no-alpaca", action="store_true",
        help="Alpaca API を参照しない（オフライン実行）",
    )
    parser.add_argument(
        "--expect", default=",".join(DEFAULT_EXPECTED_HOLDINGS),
        help="Alpaca 実保有の期待値（--no-alpaca 時の突き合わせ用。カンマ区切り）",
    )
    args = parser.parse_args()

    expected = tuple(t.strip().upper() for t in args.expect.split(",") if t.strip())

    print("=" * _W)
    print("  学習データ監査（読み取り専用 — 書き込み・修復は一切行いません）")
    print("=" * _W)
    print(f"  対象 JSONL : {args.file}")
    print(f"  index      : {POSITIONS_INDEX}")
    print(f"  obsidian   : {OBSIDIAN_LOGS}")
    print(f"  事故日     : {args.incident_date}")
    print(f"  Alpaca     : {'参照しない (--no-alpaca)' if args.no_alpaca else '読み取りのみ'}")

    records, broken = load_records(args.file)
    index = load_index()

    report_summary(records, broken, args.incident_date)
    report_orphans(records, index)
    report_index(index, records, use_alpaca=not args.no_alpaca, expected=expected)
    report_obsidian()

    print()
    print("=" * _W)
    print("  監査完了 — このスクリプトは何も修復していません")
    print("=" * _W)


if __name__ == "__main__":
    main()
