"""
audit_agent.py — AuditAgent: メタ評価・自己反省ループ管理

定期的に過去トレード履歴（obsidian_logs / training_data.jsonl）を集計し、
各エージェントの勝率を評価する。勝率が閾値（40%）未満かつ最低取引数（3）
以上のエージェントを SUSPENDED に設定し、data/agent_status.json に記録する。

SUSPENDED エージェント:
  - 本番スコア計算ウェイト = 0.0（発言権ミュート）
  - 処理自体は継続（Shadow Mode: 結果を "shadow_*" キーとして BBS に保存）
  - 補習課題プロンプトを shadow エントリに付与

Surface 環境対応:
  - 追加 LLM / Ollama 呼び出し一切なし
  - 直列（逐次）処理のみ
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# =========================================================
# パス定数
# =========================================================

AGENT_STATUS_PATH  = Path("data/agent_status.json")
OBSIDIAN_LOGS_DIR  = Path("data/knowledge_base/obsidian_logs")
TRAINING_DATA_PATH = Path("data/training/training_data.jsonl")

# =========================================================
# 評価パラメータ
# =========================================================

SUSPENSION_THRESHOLD      = 0.40  # 勝率がこれ未満 → SUSPENDED
MIN_TRADES_FOR_EVALUATION = 20    # 統計的有意水準: この取引数未満は Grace Period（評価保留）
RECOVERY_THRESHOLD        = 0.50  # SUSPENDED → ACTIVE への復帰勝率

# ウェイトキー（main.py の WEIGHTS と同じキー）とエージェント名の対応
AGENT_WEIGHT_MAP: dict[str, str] = {
    "TechnicalAgent":   "technical",
    "NewsAgent":        "news",
    "MacroAgent":       "macro",
    "SocialAgent":      "social",
    "FundamentalAgent": "fundamental",
}

# ManagerAgent は WEIGHTS に直接対応しないが評価対象として管理
ALL_EVAL_AGENTS: list[str] = [
    "ManagerAgent",
    "TechnicalAgent",
    "NewsAgent",
    "MacroAgent",
    "SocialAgent",
    "FundamentalAgent",
]

# =========================================================
# 補習課題プロンプト（SUSPENDED エージェントへの動的指示）
# Shadow BBS エントリに付与される。LLM 呼び出しは行わない。
# =========================================================

COACHING_PROMPTS: dict[str, str] = {
    "ManagerAgent": (
        "【補習課題】最終判断でリスク許容度を引き上げてください。"
        "加重スコアが 0.50〜0.60 の場合でも、FA・テクニカル両方が POSITIVE なら"
        "STRONG BUY を積極的に検討してください。"
        "過去の慎重すぎる判断が機会損失を生んでいます。"
    ),
    "TechnicalAgent": (
        "【補習課題】テクニカル判定が保守的すぎます。"
        "RSI が 40〜60 のレンジでも、MACD ゴールデンクロスや出来高増加がある場合は"
        "POSITIVE シグナルを積極的に評価してください。"
    ),
    "NewsAgent": (
        "【補習課題】ニュースセンチメント判定が消極的です。"
        "業績改善・新製品発表・アナリスト格上げなど将来見通しに"
        "ポジティブな要素が含まれる場合は POSITIVE 評価を積極的にしてください。"
    ),
    "MacroAgent": (
        "【補習課題】マクロ環境の判定基準を見直してください。"
        "VIX が 20 以下かつ SPY が 200 日移動平均上の場合は POSITIVE をデフォルトとし、"
        "明確な悪化シグナルがある場合のみ NEGATIVE としてください。"
    ),
    "SocialAgent": (
        "【補習課題】SNS センチメントの Hype ペナルティ適用が過剰です。"
        "FA・テクニカルの裏付けがある場合は Hype スコアが高くても"
        "ペナルティを軽減し、総合的な判断を優先してください。"
    ),
    "FundamentalAgent": (
        "【補習課題】ファンダメンタルズ分析でリスク要因の重み付けが大きすぎます。"
        "成長性・収益性の改善トレンドを重視し、"
        "短期的リスクへの過剰反応を避けてください。"
    ),
}


# =========================================================
# データクラス
# =========================================================

@dataclass
class AgentStats:
    agent_name:      str
    total_trades:    int   = 0
    winning_trades:  int   = 0
    losing_trades:   int   = 0
    win_rate:        float = 0.0
    status:          str   = "ACTIVE"
    suspension_reason: str = ""
    last_evaluated:  str   = field(default_factory=lambda: datetime.now().isoformat())


# =========================================================
# ユーティリティ
# =========================================================

def _parse_profit_loss(pl_str: str) -> Optional[float]:
    """'++5.84%' や '-3.2%' など様々な形式の profit_loss を float に変換する。"""
    if not pl_str or str(pl_str).strip() in ("N/A", "", "null"):
        return None
    clean = re.sub(r"[+%\s]", "", str(pl_str)).strip()
    try:
        return float(clean)
    except ValueError:
        return None


def _load_sell_logs() -> list[dict]:
    """obsidian_logs から outcome=CLOSED の SELL ログを全件読み込む。"""
    if not OBSIDIAN_LOGS_DIR.exists():
        return []

    sell_logs: list[dict] = []
    for log_file in sorted(OBSIDIAN_LOGS_DIR.glob("*_SELL*.md")):
        try:
            text = log_file.read_text(encoding="utf-8")
            if "outcome: CLOSED" not in text:
                continue
            pl_m      = re.search(r"profit_loss:\s*([^\n]+)", text)
            ticker_m  = re.search(r"ticker:\s*([^\n]+)", text)
            date_m    = re.search(r"date:\s*([^\n]+)", text)
            if not pl_m:
                continue
            pl_val = _parse_profit_loss(pl_m.group(1))
            sell_logs.append({
                "file":            log_file.name,
                "ticker":          ticker_m.group(1).strip().upper() if ticker_m else "UNKNOWN",
                "date":            date_m.group(1).strip() if date_m else "",
                "profit_loss_pct": pl_val,
                "is_win":          pl_val is not None and pl_val > 0,
            })
        except Exception as exc:
            logger.warning("obsidian_log 読み込みエラー %s: %s", log_file.name, exc)

    return sell_logs


def _load_training_records() -> list[dict]:
    """training_data.jsonl を全件読み込む（壊れた行はスキップ）。"""
    if not TRAINING_DATA_PATH.exists():
        return []
    records: list[dict] = []
    try:
        with TRAINING_DATA_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        logger.warning("training_data 読み込みエラー: %s", exc)
    return records


# =========================================================
# 評価ロジック
# =========================================================

def compute_agent_performance() -> dict[str, AgentStats]:
    """
    各エージェントのパフォーマンス統計を計算する。

    ManagerAgent: obsidian_logs の SELL 全件の勝率
    個別エージェント: training_data の STRONG BUY 決定時に
      そのエージェントのシグナルが POSITIVE だったサンプルと、
      obsidian_logs の勝敗を突き合わせた推定勝率。
      サンプル不足の場合は ManagerAgent の統計を代用。
    """
    sell_logs = _load_sell_logs()
    records   = _load_training_records()
    now_str   = datetime.now().isoformat()

    stats: dict[str, AgentStats] = {
        name: AgentStats(agent_name=name, last_evaluated=now_str)
        for name in ALL_EVAL_AGENTS
    }

    # ── ManagerAgent: SELL ログ全体の勝率 ────────────────────────
    ma = stats["ManagerAgent"]
    for log in sell_logs:
        if log.get("profit_loss_pct") is not None:
            ma.total_trades += 1
            if log["is_win"]:
                ma.winning_trades += 1
            else:
                ma.losing_trades += 1
    if ma.total_trades > 0:
        ma.win_rate = round(ma.winning_trades / ma.total_trades, 4)

    # ティッカー別 WIN/LOSS セット（training_data との突き合わせ用）
    ticker_wins:   set[str] = set()
    ticker_losses: set[str] = set()
    for log in sell_logs:
        t = log.get("ticker", "").upper()
        if log.get("is_win"):
            ticker_wins.add(t)
        elif log.get("profit_loss_pct") is not None:
            ticker_losses.add(t)

    # ── 個別エージェント: シグナル貢献度の推定 ───────────────────
    sig_key_map = {
        "TechnicalAgent":   "technical",
        "NewsAgent":        "news",
        "MacroAgent":       "macro",
        "SocialAgent":      "social",
        "FundamentalAgent": "fundamental",
    }

    for agent_name, sig_key in sig_key_map.items():
        ag = stats[agent_name]
        for rec in records:
            if rec.get("mock_mode"):
                continue
            mo = rec.get("manager_output", {})
            if not mo or mo.get("decision") != "STRONG BUY":
                continue

            # シグナル値を取得
            signals = mo.get("signals", {})
            if signals:
                sig_val = float(signals.get(sig_key, 0.0))
            else:
                inp      = rec.get("inputs", {})
                sig_data = inp.get(f"{sig_key}_analysis", {})
                trend    = (sig_data.get("trend") or "neutral").lower()
                sig_val  = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}.get(
                    trend, 0.0
                )

            if sig_val <= 0.0:
                continue

            ticker = rec.get("ticker", "").upper()
            if ticker in ticker_wins:
                ag.total_trades   += 1
                ag.winning_trades += 1
            elif ticker in ticker_losses:
                ag.total_trades += 1
                ag.losing_trades += 1

        if ag.total_trades > 0:
            ag.win_rate = round(ag.winning_trades / ag.total_trades, 4)
        else:
            # データ不足時は ManagerAgent 統計を代用
            ag.win_rate      = ma.win_rate
            ag.total_trades  = ma.total_trades
            ag.winning_trades = ma.winning_trades
            ag.losing_trades  = ma.losing_trades

    return stats


def evaluate_and_update_status() -> dict[str, dict]:
    """
    全エージェントを評価し、agent_status.json を更新する。

    Grace Period: 取引数 < MIN_TRADES_FOR_EVALUATION のエージェントは勝率不問で ACTIVE を維持。
    SUSPENDED 判定: 取引数 >= MIN_TRADES_FOR_EVALUATION かつ勝率 < SUSPENSION_THRESHOLD。
    ACTIVE 復帰: SUSPENDED 状態から勝率 >= RECOVERY_THRESHOLD に改善した場合。

    Returns:
        更新後の status dict（エージェント名 → info dict）
    """
    existing: dict[str, dict] = {}
    if AGENT_STATUS_PATH.exists():
        try:
            existing = json.loads(AGENT_STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    stats_map = compute_agent_performance()
    status_dict: dict[str, dict] = {}

    for agent_name, st in stats_map.items():
        prev = existing.get(agent_name, {})

        if st.total_trades < MIN_TRADES_FOR_EVALUATION:
            # Grace Period: 統計的有意水準に達していないため評価保留・本番ウェイト維持
            new_status = "ACTIVE"
            reason = (
                f"取引回数不足のため評価を保留（{st.total_trades}/{MIN_TRADES_FOR_EVALUATION}回）"
                f" — 勝率 {st.win_rate:.1%} は参考値"
            )
        elif st.win_rate < SUSPENSION_THRESHOLD:
            new_status = "SUSPENDED"
            reason = (
                f"勝率 {st.win_rate:.1%} < 閾値 {SUSPENSION_THRESHOLD:.1%} "
                f"（{st.total_trades}取引中{st.winning_trades}勝）"
            )
        elif st.win_rate >= RECOVERY_THRESHOLD and prev.get("status") == "SUSPENDED":
            new_status = "ACTIVE"
            reason = (
                f"勝率 {st.win_rate:.1%} >= 復帰閾値 {RECOVERY_THRESHOLD:.1%} — ACTIVE 復帰"
            )
        else:
            new_status = "ACTIVE"
            reason = (
                f"勝率 {st.win_rate:.1%} >= 閾値 {SUSPENSION_THRESHOLD:.1%} — 正常 "
                f"（{st.total_trades}取引中{st.winning_trades}勝）"
            )

        status_dict[agent_name] = {
            "status":             new_status,
            "win_rate":           st.win_rate,
            "total_trades":       st.total_trades,
            "winning_trades":     st.winning_trades,
            "losing_trades":      st.losing_trades,
            "suspension_reason":  reason,
            "coaching_prompt":    COACHING_PROMPTS.get(agent_name, ""),
            "last_evaluated":     st.last_evaluated,
        }

    AGENT_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_STATUS_PATH.write_text(
        json.dumps(status_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return status_dict


# =========================================================
# ステータス読み込み・ウェイト計算
# =========================================================

def load_agent_status() -> dict[str, dict]:
    """agent_status.json を読み込む。存在しない場合は全 ACTIVE を返す。"""
    if not AGENT_STATUS_PATH.exists():
        return {name: {"status": "ACTIVE"} for name in ALL_EVAL_AGENTS}
    try:
        return json.loads(AGENT_STATUS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("agent_status.json 読み込みエラー: %s", exc)
        return {name: {"status": "ACTIVE"} for name in ALL_EVAL_AGENTS}


def get_suspended_weight_keys(status_dict: dict[str, dict]) -> list[str]:
    """SUSPENDED エージェントのウェイトキー（例: 'technical'）リストを返す。"""
    result: list[str] = []
    for agent_name, info in status_dict.items():
        if info.get("status") == "SUSPENDED":
            key = AGENT_WEIGHT_MAP.get(agent_name)
            if key:
                result.append(key)
    return result


def apply_suspension_to_weights(
    base_weights: dict[str, float],
    status_dict:  dict[str, dict],
) -> tuple[dict[str, float], list[str]]:
    """
    SUSPENDED エージェントのウェイトを 0.0 に設定し、
    残存エージェントで再正規化した有効ウェイトと停止キーリストを返す。

    Args:
        base_weights: main.py の WEIGHTS 定数
        status_dict:  load_agent_status() の返り値

    Returns:
        (effective_weights, suspended_keys)
    """
    suspended_keys = get_suspended_weight_keys(status_dict)

    if not suspended_keys:
        return dict(base_weights), []

    suspended_weight = sum(base_weights.get(k, 0.0) for k in suspended_keys)
    remaining        = 1.0 - suspended_weight

    if remaining <= 0.0:
        return {k: 0.0 for k in base_weights}, suspended_keys

    scale = 1.0 / remaining
    effective = {
        k: 0.0 if k in suspended_keys else round(v * scale, 6)
        for k, v in base_weights.items()
    }
    return effective, suspended_keys


# =========================================================
# AuditAgent クラス
# =========================================================

class AuditAgent:
    """
    AuditAgent — メタ評価・自己反省ループの管理エージェント

    使用例:
        audit   = AuditAgent()
        status  = audit.run_evaluation()          # 評価実行 & JSON 保存
        eff_w, suspended_keys = audit.get_effective_weights(WEIGHTS)
        coaching = audit.get_coaching_prompt("TechnicalAgent")
    """

    NAME = "AuditAgent"

    def run_evaluation(self) -> dict[str, dict]:
        """全エージェントのパフォーマンスを評価し、agent_status.json を更新する。"""
        return evaluate_and_update_status()

    def get_effective_weights(
        self,
        base_weights: dict[str, float],
    ) -> tuple[dict[str, float], list[str]]:
        """
        SUSPENDED エージェントを除いた有効ウェイトと停止キーリストを返す。
        agent_status.json から現在のステータスを読み込む。
        """
        status_dict = load_agent_status()
        return apply_suspension_to_weights(base_weights, status_dict)

    def get_coaching_prompt(self, agent_name: str) -> str:
        """指定エージェントの補習課題プロンプトを返す。"""
        status_dict = load_agent_status()
        return status_dict.get(agent_name, {}).get(
            "coaching_prompt",
            COACHING_PROMPTS.get(agent_name, ""),
        )

    def print_status_report(self) -> None:
        """現在のエージェントステータスをターミナルに表示する。"""
        status_dict = load_agent_status()
        w = 64
        print(f"\n{'━' * w}")
        print(f"  ◆ AuditAgent — エージェント成績レポート")
        print(f"  ◇ 評価基準: 勝率 {SUSPENSION_THRESHOLD:.0%} 未満 かつ "
              f"{MIN_TRADES_FOR_EVALUATION}取引以上 → SUSPENDED")
        print(f"{'━' * w}")
        print(f"  {'エージェント':<22} {'ステータス':<16} {'勝率':>6}  {'取引数':>14}")
        print(f"  {'─' * (w - 4)}")
        for agent_name in ALL_EVAL_AGENTS:
            info   = status_dict.get(agent_name, {})
            status = info.get("status", "ACTIVE")
            wr     = info.get("win_rate", 0.0)
            trades = info.get("total_trades", 0)
            in_grace = trades < MIN_TRADES_FOR_EVALUATION and status == "ACTIVE"
            if status == "SUSPENDED":
                icon = "🔴 SUSPENDED  "
            elif in_grace:
                icon = "🟡 GRACE PERIOD"
            else:
                icon = "🟢 ACTIVE     "
            trade_str = f"{trades:>3}/{MIN_TRADES_FOR_EVALUATION}取引"
            print(f"  {agent_name:<22} {icon}  {wr:>5.1%}  {trade_str}")
            if status == "SUSPENDED" or in_grace:
                reason = info.get("suspension_reason", "")[:58]
                print(f"    ↳ {reason}")
        print(f"{'━' * w}\n")
