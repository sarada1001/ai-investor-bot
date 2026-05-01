"""
training_data_collector.py — 学習データ収集モジュール

将来のモデル蒸留・ファインチューニングのために:
  - トレードサイクルの入力データ（技術/ニュース/マクロ/ソーシャル/ファンダメンタル）
  - ManagerAgent の思考プロセス（Chain of Thought）と最終判断
を JSONL 形式で data/training/training_data.jsonl に保存する。

ExitAgent が SELL を実行した際は update_outcome() を呼び出すことで
対応するレコードに WIN/LOSS ラベルを付与できる。
"""

from __future__ import annotations

import json
import uuid
import datetime
from pathlib import Path

TRAINING_DIR = Path(__file__).parent.parent / "data" / "training"
TRAINING_FILE = TRAINING_DIR / "training_data.jsonl"
POSITIONS_INDEX = TRAINING_DIR / "open_positions_index.json"

# ManagerAgent の加重（main.py と同期して変更すること）
_WEIGHTS = {
    "fundamental": 0.40,
    "technical":   0.20,
    "macro":       0.20,
    "news":        0.10,
    "social":      0.10,
}

_INPUT_KEYS = {
    "technical_analysis",
    "news_analysis",
    "macro_analysis",
    "social_analysis",
    "fundamental_analysis",
}


def _ensure_dirs() -> None:
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# 保存
# =========================================================

def save_training_record(
    session_id: str,
    ticker: str,
    bbs_entries: list[dict],
    judgment: dict,
    mock_mode: bool = False,
    hybrid_mode: bool = False,
) -> str:
    """
    トレードサイクル 1 回分の学習データを JSONL に追記する。

    Args:
        session_id:   BBS のセッション ID（YYYYMMDD_HHMMSS）
        ticker:       分析対象ティッカー
        bbs_entries:  bbs.read_all() の結果（各エージェントの生出力）
        judgment:     ManagerAgent の判断 dict（または Gate HOLD の場合の judgment）
        mock_mode:    モック実行かどうか（後でフィルタリング可能にする）

    Returns:
        record_id (str): アウトカム紐付けに使う UUID
    """
    _ensure_dirs()

    # BBS エントリから各エージェントの入力データを抽出
    inputs: dict = {}
    for entry in bbs_entries:
        key = entry.get("key", "")
        if key in _INPUT_KEYS:
            inputs[key] = entry.get("data")

    record_id = str(uuid.uuid4())

    # ManagerAgent の Chain of Thought を漏れなく保存
    sigs = judgment.get("signals", {})
    manager_cot = {
        "signals": sigs,
        "signal_reasons": judgment.get("signal_reasons", {}),
        "weights": _WEIGHTS,
        "weighted_score": judgment.get("score"),
        "threshold": judgment.get("threshold"),
        "macro_forced_hold": judgment.get("macro_forced_hold", False),
        "social_hype_penalty": judgment.get("social_hype_penalty", False),
        "social_hype_score": judgment.get("social_hype_score", 0.0),
        "gate_skipped": judgment.get("gate_skipped", False),
        "gate_reason": judgment.get("gate_reason"),
        "strong_buy_conditions_check": {
            "score_above_threshold": (
                (judgment.get("score") or 0) >= (judgment.get("threshold") or 0.60)
            ),
            "fa_positive": sigs.get("fundamental", 0) > 0,
            "tech_non_negative": sigs.get("technical", 0) >= 0,
            "news_non_negative": sigs.get("news", 0) >= 0,
            "macro_non_negative": not judgment.get("macro_forced_hold", False),
        },
        # 教師データの「正解」— 小型モデルに学習させる自然言語推論
        "rationale": judgment.get("rationale", ""),
    }

    record = {
        "record_id": record_id,
        "session_id": session_id,
        "date": datetime.date.today().isoformat(),
        "created_at": datetime.datetime.now().isoformat(),
        "ticker": ticker,
        "mock_mode": mock_mode,
        "hybrid_mode": hybrid_mode,
        # ── 入力フィーチャー ──
        "inputs": inputs,
        # ── 教師信号（ManagerAgent の思考 + 結論） ──
        "manager_chain_of_thought": manager_cot,
        "manager_output": {
            "decision": judgment.get("decision"),
            "score": judgment.get("score"),
            "threshold": judgment.get("threshold"),
            "is_strong_buy": judgment.get("is_strong_buy", False),
            "order": judgment.get("order"),
        },
        # ── アウトカムラベル（ExitAgent 実行後に付与） ──
        "outcome": None,
        "outcome_label": None,  # "WIN" | "LOSS" | null
        "outcome_updated_at": None,
    }

    with open(TRAINING_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # STRONG BUY なら EXIT 待ちポジションインデックスに登録
    if judgment.get("is_strong_buy"):
        _register_open_position(record_id, ticker, session_id, judgment)

    return record_id


# =========================================================
# アウトカム更新（ExitAgent から呼び出す）
# =========================================================

def update_outcome(
    ticker: str,
    pnl_pct: float,
    exit_price: float,
    exit_reason: str,
) -> int:
    """
    ExitAgent が SELL を実行した際に呼び出し、対応レコードに WIN/LOSS を付与する。

    FIFO 方式: 同一ティッカーで最も古いオープンポジションを対象とする。

    Args:
        ticker:      ティッカーシンボル
        pnl_pct:     損益率（%）
        exit_price:  売却価格
        exit_reason: 売却理由

    Returns:
        更新したレコード数（0 なら対応するオープンポジションなし）
    """
    _ensure_dirs()

    index = _load_positions_index()
    positions = index.get(ticker, [])
    if not positions:
        return 0

    # 最古のポジション（FIFO）
    position = positions.pop(0)
    record_id = position["record_id"]

    outcome_label = "WIN" if pnl_pct >= 0 else "LOSS"
    updated_count = _update_jsonl_record(
        record_id=record_id,
        updates={
            "outcome": {
                "exit_price": exit_price,
                "pnl_pct": pnl_pct,
                "exit_reason": exit_reason,
                "exit_date": datetime.date.today().isoformat(),
            },
            "outcome_label": outcome_label,
            "outcome_updated_at": datetime.datetime.now().isoformat(),
        },
    )

    # インデックスを更新
    if positions:
        index[ticker] = positions
    else:
        index.pop(ticker, None)
    _save_positions_index(index)

    return updated_count


# =========================================================
# 内部ヘルパー
# =========================================================

def _register_open_position(
    record_id: str,
    ticker: str,
    session_id: str,
    judgment: dict,
) -> None:
    index = _load_positions_index()
    if ticker not in index:
        index[ticker] = []

    order = judgment.get("order") or {}
    index[ticker].append({
        "record_id": record_id,
        "session_id": session_id,
        "entry_date": datetime.date.today().isoformat(),
        "entry_price": order.get("price"),
    })
    _save_positions_index(index)


def _update_jsonl_record(record_id: str, updates: dict) -> int:
    """JSONL ファイル内の指定 record_id のレコードを上書きする（全行再書き込み）。"""
    if not TRAINING_FILE.exists():
        return 0

    records = []
    updated_count = 0
    with open(TRAINING_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("record_id") == record_id:
                record.update(updates)
                updated_count += 1
            records.append(record)

    with open(TRAINING_FILE, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return updated_count


def _load_positions_index() -> dict:
    if not POSITIONS_INDEX.exists():
        return {}
    with open(POSITIONS_INDEX, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_positions_index(index: dict) -> None:
    with open(POSITIONS_INDEX, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
