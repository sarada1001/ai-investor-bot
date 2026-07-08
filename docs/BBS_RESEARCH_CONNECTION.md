# BBS → 研究データ 接続ドキュメント

> 対象図: [`docs/diagrams/bbs_research_connection.mmd`](diagrams/bbs_research_connection.mmd)
> 作成日: 2026-07-02　｜　実装（コード・データファイル）を直接確認して作成した現状記録。
> 関連: [`docs/SAFETY.md`](SAFETY.md)（安全装置の詳細）, [`CLAUDE.md`](../CLAUDE.md)（Wikiスキーマの詳細）

## 0. 何のためのドキュメントか

本番稼働中の自動売買システムが日々書き込む `bbs/*.json`（エージェント間通信ログ）が、
どのような経路を辿って卒業研究（AIのHOLD判断評価フレームワーク）の評価データになるのかを
1枚の図と本ドキュメントで示す。**プロダクトとしての稼働と、研究としての評価が同じデータ基盤の上で
地続きになっている**ことが、この設計の一番の特徴である。

---

## 1. なぜBBS方式（ファイルベースの疎結合）が研究データ抽出と相性が良いか

エージェント間通信を関数呼び出しやメッセージキューではなく、**セッション単位のJSONファイル**
（`bbs/{session_id}.json`、各エントリは `{agent, key, timestamp, data}`）でやり取りする設計を
採用している。これは元々「エージェントの追加・差し替えを容易にする」ためのアーキテクチャ判断だが、
副産物として次の性質を持つ:

- **後から読む側が実行順序やプロセス境界を意識しなくてよい**。ファイルを開けば、そのサイクルで
  何が起きたかが完結して読み取れる。
- **本番のエージェント呼び出しコードに一切手を入れずに、新しい分析軸を追加できる**。
  実際、`engine/research_helpers.py` は「既存の実運用フローには一切影響しない
  （`research_mode` フラグで制御）」とコード内コメントに明記されており、BBSスナップショットを
  外部から読んで再計算するだけの純関数群として実装されている。
- 「エージェントを増やしやすい設計」と「評価軸を増やしやすい設計」が、同じファイルベース疎結合という
  1つの意思決定から自然に両立している。

この性質のおかげで、研究用のデータ抽出コード（`research_helpers.py` 以下一式）は本番の
`engine/trade_cycle.py` を一切変更せずに追加された。図で「本番運用」ゾーンと「変換・蓄積」
ゾーンの矢印が一方向にしか伸びていないのは、比喩ではなく実装上の事実である。

---

## 2. 図の各ノード説明

### 本番運用ゾーン（データの発生源）

| ノード | 説明 |
|---|---|
| `bbs/*.json` | 6エージェント（technical/news/macro/social/fundamental/liquidity）の分析結果が書き込まれる共有メモリ。1セッション1ファイル。 |
| ManagerAgent | BBS全エントリを読み、加重スコアを算出して最終判断（STRONG BUY/HOLD等）を出す。 |
| CriticAgent | ManagerAgentの推論を過去の教訓と照合し、`APPROVE`/`OVERRIDE`を独立LLMで監査する（詳細: [`docs/SAFETY.md`](SAFETY.md)）。 |
| TradeGuard | 発注頻度・保有数のガードレール。ブロック時もその判断はログに残る。 |
| LiveTradingGate | Alpaca発注直前の最終ゲート（コード内コメントで「最終防壁」と明記）。 |
| Alpaca発注 | 実際の注文執行（ライブ/ペーパー）。 |
| ObsidianLogger → `Log_*.md` | 発注結果（成立・スキップ問わず）をYAMLフロントマター付きMarkdownとして`data/knowledge_base/obsidian_logs/`に記録する raw 層。 |

### 変換・蓄積レイヤー

| ノード | 説明 |
|---|---|
| `training_data_collector.py` | トレードサイクルの入出力（BBSスナップショット＋最終判断）をJSONLで収集する。 |
| `training_data.jsonl` | 上記の蓄積先。※現状の用途と制約は§3参照。 |
| `research_helpers.py` | 本番BBSスナップショットを研究用に標準化し、HOLDケースを抽出する純関数群。`research_mode`フラグでON/OFF。 |
| `hold_cases.jsonl` | 抽出されたHOLD判断のケース集積。介入実験・忠実性評価の起点データ。 |
| `run_intervention_experiment.py` | `hold_cases.jsonl` の各ケースについて、エージェントを1体ずつ除外/反転させて再計算し、「どのエージェントがHOLDの真の原因か」を特定する。 |
| `intervention_results.jsonl` | 上記の結果。1ケースにつき `true_cause_agents`（真の原因エージェント）を含む。 |
| `compute_faithfulness_score.py` | `hold_cases.jsonl` の「ManagerAgentが説明した原因」と `intervention_results.jsonl` の「実際に介入して特定した原因」を突き合わせ、説明の正確性（Faithfulness）を定量化する。 |
| `faithfulness_report.csv` | 上記の集計結果。 |
| `server_librarian.py --ingest` | `Log_*.md` を読み込み、3層Wiki（ティッカー別ページ・コンセプトページ・`INDEX.md`）を自動再生成する。 |
| 3層Wiki / `INDEX.md` | 人間が読む知識ベース。詳細スキーマは[`CLAUDE.md`](../CLAUDE.md)。 |

### 研究・評価ゾーン（データの利用先）

| ノード | 説明 |
|---|---|
| `run_ablation_test.py` → `ablation_results.csv` | **蓄積データを経由しない独立系統。** `main.py` の `run_trade_cycle` を直接importし、エージェントを1体除外した状態でmock/hybridモード再実行する。図中で他の研究ノードと接続方法が異なるのはこのため（`bbs/*.json`に破線で直結し、中央の蓄積レイヤーを迂回している）。 |
| `evaluate_financebench.py` | **未実装。** README.mdのロードマップに「FinanceBench評価 — RAG検索品質の体系的ベンチマーク」として未着手（`[ ]`）で記載されている計画段階の項目。図では点線ノードとして他と区別している。 |
| 卒業研究（AIのHOLD判断評価フレームワーク） | `faithfulness_report.csv` と `intervention_results.jsonl`、および`ablation_results.csv`を主要な入力データとする。 |

---

## 3. 現状の制約・今後の課題（正直に）

ポートフォリオとして誇張しないために、現時点（2026-07-02）で確認できた制約を明記する。

- **`training_data.jsonl` はまだファインチューニングに使われていない。** コード自体のdocstringに
  「将来のモデル蒸留・ファインチューニングのために」と明記されており、現状は蓄積フェーズにある。
  評価パイプラインの主戦力は `hold_cases.jsonl` 以降の研究専用チェーンであり、
  `training_data.jsonl` はそれとは別系統の、より汎用的な将来投資である。
- **LiquidityAgentの全ケース発火は未確認。** `skills/liquidity_monitor.py` としてコードは実装され、
  `engine/constants.py` 等にも組み込まれているが、手元で確認した1件のBBSサンプルには
  `liquidity_analysis` キーが含まれていなかった。全銘柄・全サイクルで一様に発火するかは
  本ドキュメント作成時点では未検証。
- **`evaluate_financebench.py` は未着手。** ロードマップ上の計画であり、現状は
  `faithfulness_report.csv`（自作の忠実性評価）が外部ベンチマーク代わりの役割を部分的に
  担っている状態。外部標準ベンチマークとの比較はまだ存在しない。
- **研究パイプライン全体はまだPhase B（研究・評価強化）の途上。** テストスイートは332件
  （既知失敗2件、`docs/TEST_REPORT.md`参照）、Universe拡大バックテストの勝率は46.0%
  （取引数189件/3ヶ月、100銘柄）という実測値はあるが、これは「トレードロジックの検証」の数値であり、
  「AIのHOLD判断評価フレームワーク」としての研究成果はまだ論文化・体系化の途中段階にある。
  今回整理した接続パイプライン自体が、その体系化に向けた土台である。

---

## 4. 次に書きたいこと

本ドキュメントで示したBBSという疎結合な共有メモリの中身——各エージェントが何を根拠に
「HOLD」と判断したのか、その因果関係をどう遡れるようにしているか——については、
ブログ②「マルチエージェントAIの判断をBBSで因果トレースする」で詳しく書く。
