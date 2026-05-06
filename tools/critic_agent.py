"""
critic_agent.py — Reflexion フェーズ2: 過去の教訓に基づくManagerAgent監査モジュール

ManagerAgentの推論をRAGで取得した過去の失敗ルールと照合し、
APPROVE / OVERRIDE を判定して返す。
"""

from __future__ import annotations

import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434") + "/api/generate"
OLLAMA_MODEL    = "llama3.1"
OLLAMA_TIMEOUT  = 60  # seconds

# フォールバック値（API障害時にシステムを止めない）
_FALLBACK_RESPONSE = {
    "critic_decision": "HOLD",
    "revised_action":  "HOLD",
    "critique_reason": "CriticAgent API障害のためフォールバック。手動確認を推奨。",
}

_PROMPT_TEMPLATE = """\
You are the "CriticAgent", a strict and highly logical Chief Risk Officer for an autonomous trading system.
Your objective is to prevent the system from repeating past mistakes by reviewing the "ManagerAgent"'s proposed trade against our database of past failure rules.

### INPUT DATA
[1. Proposed Trade by ManagerAgent]
- Ticker: {ticker}
- Proposed Action: {manager_action}
- Manager's Reasoning: {manager_context}

[2. Past Lessons (Retrieved via RAG)]
{retrieved_rules}

### INSTRUCTIONS
1. Analyze the Manager's Reasoning. Is the ManagerAgent repeating a mistake outlined in the "Past Lessons"?
2. Only OVERRIDE if the current trade *clearly and directly* violates one of the listed Past Lessons. Minor similarities or speculative risks do NOT qualify as violations.
3. If there is no clear conflict with past rules, you MUST APPROVE the trade.
4. Default to APPROVE when uncertain.

### OUTPUT FORMAT
You must respond ONLY in the following JSON format:
{{
  "critic_decision": "APPROVE" | "OVERRIDE",
  "revised_action": "STRONG BUY" | "BUY" | "HOLD" | "SELL" | "STRONG SELL",
  "critique_reason": "Provide a concise reason based STRICTLY on the Past Lessons."
}}"""


class CriticAgent:
    """ManagerAgentの推論を過去の教訓と照合して監査するエージェント。"""

    def __init__(
        self,
        endpoint: str = OLLAMA_ENDPOINT,
        model: str    = OLLAMA_MODEL,
        timeout: int  = OLLAMA_TIMEOUT,
    ) -> None:
        self.endpoint = endpoint
        self.model    = model
        self.timeout  = timeout

    def evaluate_trade(
        self,
        ticker:           str,
        manager_action:   str,
        manager_context:  str,
        retrieved_rules:  str,
    ) -> dict:
        """
        ManagerAgentの推論を過去ルールと照合して評価する。

        Args:
            ticker:          対象銘柄ティッカー（例: "NVDA"）
            manager_action:  ManagerAgentの提案アクション（例: "BUY"）
            manager_context: ManagerAgentの推論・根拠
            retrieved_rules: RAGで取得した過去の教訓テキスト

        Returns:
            {
                "critic_decision": "APPROVE" | "OVERRIDE",
                "revised_action":  str,
                "critique_reason": str,
            }
        """
        prompt = _PROMPT_TEMPLATE.format(
            ticker          = ticker,
            manager_action  = manager_action,
            manager_context = manager_context,
            retrieved_rules = retrieved_rules,
        )

        logger.info("  [CriticAgent] %s の評価を開始 (ManagerAction=%s)", ticker, manager_action)

        raw = self._call_ollama(prompt)
        if raw is None:
            return _FALLBACK_RESPONSE.copy()

        result = self._parse_response(raw)
        logger.info(
            "  [CriticAgent] 判定完了 → %s / 修正アクション: %s",
            result.get("critic_decision"),
            result.get("revised_action"),
        )
        return result

    # ----------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------

    def _call_ollama(self, prompt: str) -> str | None:
        """Ollama API を呼び出し、レスポンス文字列を返す。失敗時は None。"""
        payload = {
            "model":  self.model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
        }
        try:
            resp = requests.post(
                self.endpoint,
                json    = payload,
                timeout = self.timeout,
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
        except requests.exceptions.ConnectionError as e:
            logger.error("  [CriticAgent] 接続エラー: Ollamaサーバーに到達できません — %s", e)
        except requests.exceptions.Timeout:
            logger.error("  [CriticAgent] タイムアウト: %d秒以内に応答なし", self.timeout)
        except requests.exceptions.HTTPError as e:
            logger.error("  [CriticAgent] HTTPエラー: %s", e)
        except Exception as e:
            logger.error("  [CriticAgent] 予期しないエラー: %s", e)
        return None

    def _parse_response(self, raw: str) -> dict:
        """LLMのレスポンスをJSONとしてパースする。失敗時はフォールバックを返す。"""
        try:
            data = json.loads(raw)
            # 必須キーの存在確認
            for key in ("critic_decision", "revised_action", "critique_reason"):
                if key not in data:
                    raise KeyError(f"必須キー '{key}' が応答に含まれていません")
            return data
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("  [CriticAgent] JSONパース失敗: %s\n  raw=%s", e, raw[:200])
            return _FALLBACK_RESPONSE.copy()


# ============================================================
# 動作テスト
# ============================================================
if __name__ == "__main__":
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s [%(levelname)s] %(message)s",
        datefmt= "%H:%M:%S",
    )

    # --- ダミー入力（RAGで取得した過去ルールを想定） ---
    dummy_rules = """
- ルール: VIX が 25 を超えている局面では STRONG BUY / BUY を避ける。
  理由: 高VIX相場では好決算銘柄でも急落するケースが多く、過去3回連続で損失を出した。

- ルール: NVDA は決算直後の24時間以内はポジションを取らない。
  理由: ガイダンスへの市場反応が読みにくく、初動が逆方向に振れることが多い。
""".strip()

    # --- ManagerAgentの出力を模したダミーデータ ---
    test_cases = [
        {
            "label":          "ケース1: ルール違反（VIX高・BUY提案）",
            "ticker":         "NVDA",
            "manager_action": "BUY",
            "manager_context": (
                "NVDA が 200 日移動平均を上抜けた。決算 EPS が予想を上回り強気シグナル。"
                "現在 VIX は 28。半導体セクターに強い資金流入が見られる。"
            ),
        },
        {
            "label":          "ケース2: ルール適合（低VIX・BUY提案）",
            "ticker":         "AMD",
            "manager_action": "BUY",
            "manager_context": (
                "AMD が重要なサポートラインでリバウンド。VIX は 16 と低水準。"
                "決算から2週間経過しており、テクニカル・ファンダメンタルズともに強い。"
            ),
        },
    ]

    agent = CriticAgent()

    for case in test_cases:
        print(f"\n{'='*60}")
        print(f"  {case['label']}")
        print("=" * 60)

        result = agent.evaluate_trade(
            ticker          = case["ticker"],
            manager_action  = case["manager_action"],
            manager_context = case["manager_context"],
            retrieved_rules = dummy_rules,
        )

        print(f"  判定       : {result.get('critic_decision')}")
        print(f"  修正アクション: {result.get('revised_action')}")
        print(f"  理由       : {result.get('critique_reason')}")
