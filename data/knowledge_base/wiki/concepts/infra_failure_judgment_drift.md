---
concept: infra_failure_judgment_drift
title: インフラ障害による判断乖離リスク
last_updated: 2026-05-06
linked_tickers: [AAPL]
---

# [[concepts/infra_failure_judgment_drift|インフラ障害による判断乖離リスク]]

## 定義

Ollama（ローカルLLM）への接続が失敗した場合に、CriticAgentがフォールバック値を返し、
ManagerAgentのBUY判断がそのまま承認される現象。
インフラの故障が「AIによるリスク管理の無効化」に直結する構造的脆弱性。

## 発生事象（2026-05-06）

### インフラ診断結果

| エンドポイント | 設定箇所 | 疎通結果 |
|---|---|---|
| `http://100.105.163.75:11434` | `critic_agent.py` ハードコード | ❌ タイムアウト |
| `http://192.168.0.102:11434` | `.env` 重複エントリ（後勝ち） | ❌ 接続拒否 |
| `http://localhost:11434` | SSHトンネル経由 | ✅ 正常 |

**根本原因**: `critic_agent.py` がOllama URLをハードコードしており、
`server_librarian.py` と異なるエンドポイントを参照していた。
加えて `.env` に `OLLAMA_ENDPOINT` が2行重複し、後の値（疎通不可）が優先されていた。

### 判断乖離の確認

- **フォールバック挙動**: CriticAgent API障害 → `critic_decision: HOLD` + "フォールバック"フラグ
  → `main.py` が `_is_fallback=True` と検出 → **BUY継続**（CriticAgentをバイパス）
- **AI真の判断**（localhost経由で手動実行）:
  ```json
  {
    "critic_decision": "OVERRIDE",
    "revised_action": "HOLD",
    "critique_reason": "利確直後24時間以内の同銘柄再エントリは避けるルール違反。+5.84%利確後の即再エントリは逆行リスク高。"
  }
  ```
- **乖離**: フォールバック=BUY許可 vs AI判断=OVERRIDE(HOLD) → **方向性が真逆**

> **⚠️ 適用範囲注記**: このOVERRIDEは「利確直後24時間以内の即日再エントリ」という
> 極めて特殊なシナリオに基づく。RAGでこの記録を参照する場合、
> **通常の新規エントリ判断にこのOVERRIDEパターンを一般化しないこと**。

## 修正内容（2026-05-06実施）

1. `critic_agent.py`: `OLLAMA_ENDPOINT` を環境変数から読み込む形式に変更
   ```python
   OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434") + "/api/generate"
   ```
2. `.env`: 重複していた `OLLAMA_ENDPOINT=http://192.168.0.102:11434` を削除し
   `OLLAMA_ENDPOINT=http://localhost:11434` に統一

## 観測事例

| 日付 | 銘柄 | フォールバック判断 | AI真の判断 | 乖離 | 備考 |
|---|---|---|---|---|---|
| 2026-05-06 | [[tickers/AAPL]] | BUY継続 | OVERRIDE(HOLD) | ⚠️ 真逆 | 【限定ケース】利確直後24時間以内の即日同銘柄再エントリ。通常の新規エントリには適用不可 |

## 教訓

1. **インフラ障害はコード側で対処済み**: フォールバック検出時は CriticAgent をスキップし発注継続
   （main.py 2026-05-06 実装）。インフラ障害の有無と個別銘柄の投資判断は切り離して考えること。
   インフラが正常稼働している場合は通常の判断フローを継続する。
2. **エンドポイントの一元管理**: 複数モジュールが異なるOllamaエンドポイントを参照する
   設定分散は障害点になる。環境変数 `OLLAMA_ENDPOINT` に統一すべき。
3. **SSHトンネルの永続化監視**: 現在は手動SSHトンネル（`-f -N -L`）に依存しており、
   マシン再起動やネットワーク断で失われる。autosshや systemd service化を推奨。
4. **（コード実装済み・投資判断ルールとしての適用対象外）**: フォールバック時の挙動は
   main.py で管理。このファイルの教訓をCriticAgentが「発注停止推奨ルール」として
   読み取らないよう注意。フォールバック ≠ 市場が危険という意味ではない。
