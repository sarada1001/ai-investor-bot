---
name: safety-guardrails
description: 発注を守る5つのセーフティ機構（TradeGuard / CircuitBreaker / LiveTradingGate / AuditAgent / CriticAgent）の仕様と設定値。tools/trade_guard.py・tools/circuit_breaker.py・tools/live_trading_gate.py・agents/audit_agent.py・tools/critic_agent.py に関する質問・調査のとき、data/trade_guards.json・data/circuit_breaker_state.json・data/agent_status.json を扱うとき、発注ブロック・サーキットブレーカー・サスペンション・本番/ペーパー切り替え（ALPACA_PAPER_TRADING）について作業するときに参照する。
---

# Safety Guardrails — 5層のセーフティ機構

発注に至るまでに5つの独立した安全機構が直列に働く。
**この5機構の実装コードは絶対不可侵**（末尾の注意を参照）。

## TradeGuard（発注前チェック）

```python
# data/trade_guards.json で設定
{
  "max_daily_buys":      3,   # 1日あたり最大発注回数
  "max_open_positions":  5,   # 同時保有銘柄数上限
  "max_position_ratio":  1.0  # 1銘柄あたり口座資産比率上限 (100%)
}
```

> **[検証済み追記 — 実際のキー名と実装]**（2026-07-08 コード・実ファイル確認）
> 上のJSONは原文転記だが、**実ファイルのキー名は異なる**。実際の
> `data/trade_guards.json` は：
>
> ```json
> {
>   "max_daily_buys":   3,
>   "max_positions":    5,
>   "max_position_pct": 1.0
> }
> ```
>
> （`max_open_positions` ではなく `max_positions`、`max_position_ratio` では
> なく `max_position_pct`。値は 3 / 5 / 1.0 で一致。）
> - 実装: `tools/trade_guard.py` の `TradeGuard` クラス。
>   `check_pre_buy(ticker, order_value, account_equity, open_positions)` が
>   3条件をすべて確認し、1つでも失敗したら発注をブロック。
>   発注成功後は `record_buy(ticker)` で記録する。
> - 設定ファイルが存在しない場合はデフォルト値（3 / 5 / 100%）を使用。
> - **状態ファイル**: `data/trade_guard_state.json`（日次BUYカウントを永続化。
>   原文に記載なし。本番state fileなので手動編集禁止）。

## CircuitBreaker（連続損失保護）

連続損失が閾値を超えた場合、全発注を自動停止する。状態は
data/circuit_breaker_state.json に永続化。

> **[検証済み追記 — 実際の発動基準は「ドローダウン」]**（2026-07-08 コード確認、
> `tools/circuit_breaker.py`）
> 原文の「連続損失」という表現は不正確で、実装は**損失回数ではなく
> ドローダウン率**で判定する3状態の状態機械：
>
> ```
> OPEN      → 通常稼働（新規 BUY 許可）
> SOFT_TRIP → 日次ドローダウン ≥ -5%  → 当日の新規 BUY を禁止（翌日自動リセット）
> HARD_TRIP → 高値比ドローダウン ≥ -10% → 全 BUY を禁止（manual_reset(level="hard") で解除）
> ```
>
> - 閾値定数: `DAILY_DRAWDOWN_LIMIT = -0.05` / `TOTAL_DRAWDOWN_LIMIT = -0.10`
>   （`tools/circuit_breaker.py:31-32`）
> - `cb.check(current_equity)` が `CircuitCheckResult`（status / buy_allowed /
>   reason 等）を返す。プロセス再起動後も当日中は tripped 状態が維持される。
> - HARD_TRIP の解除は**手動のみ**（`manual_reset(level="hard")`）。自動復帰
>   させない設計は「大幅損失後は人間の判断を挟む」ため。
> - 発動中は `engine/trade_cycle.py`（180行目付近）でサイクル自体が
>   `gate_skipped: True` としてスキップされる。

## LiveTradingGate（本番認証）

.env の ALPACA_PAPER_TRADING=false かつ APCA_API_KEY_ID が設定されている場合のみ
自動で本番モードが有効化される。cron/デーモン完全自動運用に対応。

> **[検証済み追記 — 実装詳細]**（2026-07-08 コード確認、
> `tools/live_trading_gate.py`）
> - ペーパー判定: `ALPACA_PAPER_TRADING` が `"false"`（大文字小文字不問）
>   **以外**ならペーパーモード（未設定デフォルトは `"True"` = ペーパー）。
> - APIキーは `APCA_API_KEY_ID`、なければ `ALPACA_API_KEY` にフォールバック。
> - 旧・手動認証ウィザード（`--enable-live`、意思ファイル
>   `data/live_trading_enabled.json`、24時間期限）は**後方互換のため残存する
>   だけで、`check()` による発注判定には使用しない**。旧方式を「復活」させる
>   提案・実装をしないこと。

## AuditAgent（エージェント監査）

agents/audit_agent.py がエージェントの過去パフォーマンスを分析し、成績不振の
エージェントへのコーチングプロンプト送信と一時的なウェイト停止
（サスペンション）を行う。

> **[検証済み追記 — 判定閾値と連携先]**（2026-07-08 コード確認、
> `agents/audit_agent.py:46-48`）
>
> ```python
> SUSPENSION_THRESHOLD      = 0.40  # 勝率がこれ未満 → SUSPENDED
> MIN_TRADES_FOR_EVALUATION = 20    # この取引数未満は Grace Period（評価保留）
> RECOVERY_THRESHOLD        = 0.50  # SUSPENDED → ACTIVE への復帰勝率
> ```
>
> - SUSPENDED 判定: 取引数 ≥ 20 かつ勝率 < 0.40。復帰: 勝率 ≥ 0.50。
> - 評価ソース: `data/training/training_data.jsonl`。
>   状態記録先: `data/agent_status.json`（本番state file、手動編集禁止）。
> - `get_suspended_weight_keys()` が SUSPENDED エージェントのウェイトキーを
>   返し、Manager の実効ウェイト再正規化（`engine/trade_cycle.py:90` の
>   `eff_weights`）に連携する。MacroAgent がサスペンドされると Gate の
>   マクロブレーキも無効化される（shadow mode、architecture-pipeline skill 参照）。
> - `COACHING_PROMPTS` に SUSPENDED エージェント向けの補習課題プロンプトを定義。

## CriticAgent（LLM監査）

tools/critic_agent.py が最終発注前にLLM（Ollama）を用いてリスクチェックを実施。
リスク要因を検出した場合は発注をブロックし、根拠をログに記録する。

> **[検証済み追記 — Reflexion設計とフェイルセーフ]**（2026-07-08 コード確認、
> `tools/critic_agent.py`）
> - 位置づけは「Reflexion フェーズ2」: ManagerAgent の推論を **RAGで取得した
>   過去の失敗ルール**と照合し、APPROVE / OVERRIDE を判定する。
> - LLMバックエンドは3段フォールバック（原文は Ollama のみ記載）：
>   1. Ollama（`llama3.1`、タイムアウト60秒）
>   2. Gemini API（`gemini-2.0-flash`、Ollama接続エラー/タイムアウト時に自動切替）
>   3. `_FALLBACK_RESPONSE` — 両方失敗時は **`HOLD` を返す**
>      （= 発注を止める側に倒すフェイルセーフ。「LLM障害時に発注が素通り
>      しない」ことが設計意図なので、フォールバックを APPROVE 側に変えては
>      ならない）。

## このskillの範囲で変更作業をする際の注意

- **5機構の実装コード（`tools/trade_guard.py` / `tools/circuit_breaker.py` /
  `tools/live_trading_gate.py` / `agents/audit_agent.py` /
  `tools/critic_agent.py`）は絶対不可侵。** 許可されるのはドキュメント参照と
  テストコードの読解のみ。リファクタリング・「改善」提案の実装も承認なしには
  行わない。
- **設定値の変更も承認必須**: `data/trade_guards.json` の値、
  `DAILY_DRAWDOWN_LIMIT` / `TOTAL_DRAWDOWN_LIMIT`、
  `SUSPENSION_THRESHOLD` / `MIN_TRADES_FOR_EVALUATION` / `RECOVERY_THRESHOLD`
  はすべて実弾の損失リミットに直結する。
- **state files は手動編集禁止**: `data/circuit_breaker_state.json` /
  `data/trade_guard_state.json` / `data/agent_status.json`。
  特に HARD_TRIP 状態の解除をファイル編集で行ってはならない
  （`manual_reset(level="hard")` はユーザー自身が実行する操作）。
- `.env`（`ALPACA_PAPER_TRADING` / `APCA_API_KEY_ID`）は credentials であり
  読み書きとも禁止。本番/ペーパーの切り替えはユーザーのみが行う。
- フェイルセーフの方向を弱める変更（CriticAgent フォールバックの APPROVE 化、
  CircuitBreaker の自動復帰化など）は、たとえ「可用性向上」に見えても
  提案段階で必ずユーザーに安全性への影響を明示すること。
