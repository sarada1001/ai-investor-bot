# STATUS — 現在の達成状況とロードマップ

> **これは変動情報です。** 安定した仕様（アーキテクチャ・エージェント設計・
> セーフティ機構・評価フレームワーク・インフラ）は `.claude/skills/` 配下の
> 各SKILL.mdを参照してください。本ファイルは「今どこまで進んでいて、
> 次に何をやるか」だけを記録します。
>
> **完了判定のチェック手順（再発防止）**: このファイルで項目を✅完了と
> 書く前に、行数やファイルの存在確認だけで判断しないこと。必ず中身を
> 実際に読み、`<!-- TODO -->` 等の内部マーカー・冒頭の状態宣言
> （「翻訳未了」「draft」等）の有無を確認してから完了と書く。
> （2026-07-08: README.en.mdを行数のみで✅完了と誤判定した反省より）
>
> **最終更新**: 2026-07-08（skill分割Phase 2作業時に実ファイルと突き合わせて更新。
> README.en.mdの完了判定を訂正）

## 現在の達成状況

| 機能 | ステータス |
|---|---|
| RAG自己修復フロー（EDGAR自律取得） | ✅ 稼働中 |
| Futu API連携（リアルタイム流動性） | ✅ 稼働中 |
| Alpaca実弾発注 / ストップロス管理 | ✅ 稼働中 |
| SocialAgent 実データ化（StockTwits + Gemini） | ✅ 完了（2026-07-08、詳細は下記） |
| DipScanサブループ | ✅ 稼働中 |
| AuditAgent / サスペンション機能 | ✅ 実装済み |
| ObsidianLogger / Wiki自動更新 | ✅ 稼働中 |
| バックテスト / アブレーション評価基盤 | ✅ 実装済み |
| Streamlit ダッシュボード（dashboard/app.py） | ✅ 実装済み（650行、queries.py分離済み） |
| GitHub Actions CI（テスト自動実行） | ✅ 実装済み（`.github/workflows/ci.yml`、push/PR/毎朝ナイトリー実行） |
| CD（自動デプロイ） | 🔧 部分実装 — `run_bot.sh` が実行前に `git pull origin main` を行う簡易版のみ。GitHub Actions側のCDジョブは未実装（詳細: `docs/CICD.md`） |

## 2026-07-08: SocialAgent実データ化と、その過程で発見した本番LLMバックエンド障害

### 経緯

SocialAgentは2026-05-24〜07-08の約1.5ヶ月間、常時NEUTRAL固定のスタブ実装のままだった
（`.claude/skills/agents-and-scoring/SKILL.md` 参照）。これをStockTwits公開APIから
実際の投稿を取得し、LLMでセンチメント分類する実装に置き換えた
（コミット `450250b`）。

本番投入前の最終確認として、本番サーバー（uema2lab-search）上でLLMバックエンドの
動作検証を行ったところ、**今回の変更とは無関係の、既存の本番障害を2件発見した**：

### 発見1: Ollamaトンネル切断時に全銘柄が強制HOLDになる障害

本番はローカルLLM（Ollama）を別のGPUサーバーからSSHトンネル経由で利用する設計
だった。**当初「トンネルの自動再接続機構（`tools/ollama_tunnel.py` の
`ensure_ollama_reachable()`）がどこからも呼ばれていないため」と記録したが、
これは誤りだった（2026-07-09訂正）。**

実際には、毎晩の実行ラッパー `/home/naito/run_investor_bot.sh`（リポジトリ外、
crontabから直接実行）が以下の処理で**トンネルを毎回自動的に張り直していた**：

```bash
pkill -f 11434
sleep 2
ssh -o BatchMode=yes -o StrictHostKeyChecking=no -f -N -L 11434:127.0.0.1:11434 uema2lab-gpu
sleep 3
# ボット実行 ...
pkill -f 11434
```

問題は自動再接続の欠如ではなく、**`uema2lab-search` から `uema2lab-gpu`
（`~/.ssh/config` 上のHostName、プライベートIP `192.168.0.167`）への
ネットワーク経路そのものが到達不能**だったこと。実際に本番上で同じコマンドを
実行して再現したところ `ssh: connect to host 192.168.0.167 port 22:
No route to host` で失敗した。`run_investor_bot.sh` にはこの`ssh`コマンドの
終了コードを確認する処理が一切なく、接続に失敗しても後続のボット実行が
そのまま続行される。そのため**この障害は毎晩サイレントに発生し続けていた**
と考えられる。

`tools/ollama_tunnel.py` の孤立コード（未使用の`ensure_ollama_reachable()`）
は実在するが、**今回の障害の直接の原因ではなかった**。トンネル確立自体は
別の仕組み（`run_investor_bot.sh`）が毎回試みており、そこが失敗していた、
というのが正しい理解。

**この経路到達性の問題自体はネットワークインフラ側の話であり、
アプリケーションのコード修正では解決しない。** 今後ローカルLLM（Ollama）に
戻す判断をする場合は、`uema2lab-search`⇔GPUサーバー間のネットワーク経路
（同一LAN/VPNに属しているか、ファイアウォール設定等）を別途確認する必要がある。

さらに `.env` の `DISABLE_GEMINI=true`（Gemini課金ゼロ保証のための安全弁）が
同時に有効だったため、Ollama不通時に **Geminiへのフォールバックすら行われず、
`RuntimeError` でウォッチリスト全銘柄がその日エラーHOLDになる**という設計上の
落とし穴があった。

本番ログ（`bot_cron.log`）を調査した結果、この障害は **2026-06-04〜07-07の間に
延べ70回、23銘柄で発生**していたことが判明（発生回数トップ: NOC 5回、VRTX/CRM 各3回）。
同期間のSPY（S&P500連動ETF）は755.14→747.71（-0.98%）とほぼ横ばいだったが、これは
指数全体の動きに過ぎず、影響を受けた個別銘柄（NOC・VRTX・CRM等）がその間に
指数と逆行して上昇していた可能性は排除できない。**大きな逸失利益があったか
どうかは未検証**であり、厳密な検証には個別銘柄ごとの値動きとシグナルの
再構成が必要。

### 発見2: Gemini 2.0 Flashモデルの廃止

上記障害の調査中、`GOOGLE_API_KEY` はSocialAgent・CriticAgent・server_librarian.py
の3箇所で共用されているにもかかわらず、いずれも `gemini-2.0-flash` を
ハードコード（またはデフォルト値として）参照していたことが判明。このモデルIDは
Google側で廃止済み（`404 NOT_FOUND: This model ... is no longer available`）で
あり、**Ollamaに加えてGeminiフォールバックも機能しない状態が併発していた**
（CriticAgentは両方失敗時に安全側の`_FALLBACK_RESPONSE`へフォールバックする
設計のため誤発注はなかったが、実質的なLLM監査は行われていなかった可能性が高い）。

### 対応方針

インフラ運用上の懸念（GPUサーバー側のSSH鍵管理・トンネル常時稼働の手間）から、
**ローカルLLM（Ollama）依存を当面廃止し、Gemini固定運用に統一**する方針とした：

- 本番 `.env`: `DISABLE_GEMINI=false`, `FORCE_GEMINI=true`, `GEMINI_MODEL=gemini-2.5-flash` を設定
- `tools/critic_agent.py`, `server_librarian.py` のハードコードモデル名を
  `gemini-2.5-flash` に修正（コミット `5e200ce`）
- `tools/ollama_tunnel.py` は削除せず、将来ローカルLLMに戻す場合の参考実装として保持

### 追記（2026-08-10）: 上記対応の取りこぼしと、モデル名の一元化

2026-07 の対応は「ハードコードされていた3箇所」を現行モデル名に**書き換える**
ものだったが、`.env` を**上書きする側**の箇所が3つ残っていた：

| 箇所 | 形態 |
|---|---|
| `agents/exit_agent.py` | `__init__` の引数デフォルト `llm_model="gemini-2.0-flash"` |
| `skills/rag_search.py` | `get_llm_instance(gemini_model="gemini-2.0-flash")` 明示指定 |
| `scripts/preflight_check.py` | `ChatGoogleGenerativeAI(model="gemini-2.0-flash")` 直呼び |

`skills/llm_factory.py` の既定値も `gemini-2.0-flash` のままだった。
本番 `.env` が `gemini-2.5-flash` を指していても、これらが引数で上書きするため
**無効化されていた**。結果、`bot_cron.log` には ExitAgent の thesis 判定
（購入理由がニュースで否定されたかの LLM 判断）が毎日全保有銘柄で
`429 RESOURCE_EXHAUSTED (limit: 0)` になり、価格ベースのルールベース
フォールバックのみで運用されていた記録が残っている。
`limit: 0` は枠切れではなく「廃止モデルには割り当てが存在しない」の意味で、
リトライでは永久に回復しない。

**対応**: モデル名を書き換えるのではなく、**モデル名を知っているのは
`skills/llm_factory.py` だけ**という状態にした。

- `llm_factory.get_gemini_model()` が `.env` の `GEMINI_MODEL` を毎回解決
  （モジュールレベルで固定しないため、cron のインポート順にも影響されない）
- 呼び出し側（ExitAgent / rag_search / preflight_check / critic_agent /
  server_librarian）からモデル名リテラルを全廃
- `tests/test_llm_model_resolution.py` が (1) `.env` の変更が全経路に伝播すること
  (2) `llm_factory.py` 以外に `gemini-N` リテラルが存在しないこと の両方を検証。
  静的スキャンはコメント・docstring を AST で除外している

### 追記（2026-07-09）: FundamentalAgent判定の単一サンプル観察と今後の運用方針

Gemini切替後の動作確認として、CAT銘柄でFundamentalAgentをread-only実行した
ところ、直近の本番ログ（6/29、Ollama使用）とトレンド判定が食い違う結果が
観測された：

| | 6/29（Ollama、本番ログ） | 7/9（Gemini、手動テスト） |
|---|---|---|
| トレンド判定 | 📈 POSITIVE | 📉 NEGATIVE |
| 根拠の形式 | 短い箇条書き、出典明記なし | 5ステップCoTで数値ごとに参考資料番号を明記 |

**この結果からLLMバックエンドの違いが判定を左右したと断定することはできない。**
両者は同じ「10チャンク取得」ログを出しているが、参照した財務資料（四半期）
自体がEDGAR staleness再取得により更新された可能性を排除できておらず、
n=1のサンプルであるため統計的な結論は出せない。

本番へのリバーストンネルを張ってOllama側で同日再検証する案も検討したが、
本番サーバーへの持続的なリバースポートフォワードは影響範囲が読みにくいため
見送った。厳密な検証を今すぐ行うより、**実際の運用データを継続的に観察する
方が低コスト・低リスク**と判断し、以下の方針とする：

- Gemini移行後のFundamentalAgent判定（トレンド・スコア）を、本番ログ
  （`bot_cron.log`）で時々スポットチェックし、極端な判定のブレが
  続くようであれば個別に深掘りする
- 現時点では「FundamentalAgentのみOllama優先に戻す」といった個別対応は
  行わない

### 教訓（ポートフォリオ的な観点）

この一件のアピールポイントは「バグを見つけたこと」自体ではなく、
**本番環境に対する変更を、検証と承認のプロセスを踏みながら安全に進められる
運用フローを構築・実践できたこと**にある。

具体的には、新機能（SocialAgent）を本番投入する前の最終確認という当初は
小さなタスクの中で、以下の手順を一貫して守った：

1. 本番の状態を変更前にread-onlyで調査する（`.env`の内容確認、ログ検索、
   モデル一覧APIの照会など）
2. 調査で見つかった事実（Ollamaトンネル未接続、`DISABLE_GEMINI=true`による
   全銘柄HOLD、`gemini-2.0-flash`廃止など）をその都度ユーザーに報告する
3. 対処が必要な場合は変更内容を具体的な diff や実行コマンドの形で提案し、
   ユーザーの承認を得てから実行する（`.env`変更前のバックアップ取得、
   Safety機構である`tools/critic_agent.py`の修正は特に個別承認を得るなど、
   ファイルの重要度に応じて確認の粒度を変えた）
4. 想定外の事実が見つかるたびに（本番ファイルとローカルの不一致、意図せぬ
   未コミット変更の混入、機密情報を誤って出力してしまった際の対応など）、
   先に進める前に都度ユーザーに判断を仰いだ

結果として当初のタスク（SocialAgentのテスト）の過程で、本番環境に
長期間潜んでいた「LLMバックエンドの多重フォールバック障害」（廃止モデルID・
自動再接続機構の未配線・安全弁の意図しない相互作用が重なったもの）が
見つかり、ユーザーの承認のもとで修正するに至った。単体の機能テストでは
検出されず、本番相当の環境で実際にLLM呼び出しを行って初めて顕在化した点、
および一連の対応が都度の確認を挟みながら進められた点が特徴。

> **[更新点]** 前回把握していた進捗（貼付ドキュメント時点）から以下が
> 新たに完了・判明しています:
> - Streamlit ダッシュボードは「🔧 継続改善中」から**実装済み**に進捗
>   （`dashboard/app.py` 650行 + `dashboard/queries.py`）。稼働確認・磨き込みは
>   継続中の可能性があるため、UI経由の動作確認は別途必要。
> - GitHub Actions CI は「📋 計画中」から**実装済み**に進捗
>   （`.github/workflows/ci.yml`）。ただしCDは未実装、詳細は `docs/CICD.md`。
> - **重要な安全側の事実**: `docs/SAFETY.md` の記述によれば、
>   LiveTradingGate と TradeGuard は 2026-06-03 のコミット `d6f7bd1`
>   （「TradeGuardの緩和とLiveTradingGateの完全自動化」）で**意図的に
>   自動化・緩和**されており、旧README等に残っていた「24時間手動同意」
>   「1銘柄20%上限」という記述は現在の挙動と一致しない
>   （現在は `max_position_pct: 1.0` = 実質上限なし、本番認証も
>   `.env` 設定のみで自動判定。詳細は
>   `.claude/skills/safety-guardrails/SKILL.md` および `docs/SAFETY.md`）。

## やるべきことリストの進捗

貼付ドキュメント時点のTODOリストと現状を突き合わせた結果：

| 項目 | ステータス | 実体 |
|---|---|---|
| 英語README | 🔧 部分完了 | `README.en.md`（422行）。**訂正**: 行数のみで完了と誤判定していた。ファイル自身の冒頭（6-9行目）に「Sections 1–4 are fully translated. Sections 5–16 are condensed — see the inline `<!-- TODO -->` markers」と明記されている。実際に `<!-- TODO -->` マーカーが付くのは §5 Day-to-Day Operations / §6 monitor.py / §7 dashboard.py / §8 Enabling Live Trading（安全に関わるため要約禁止と自己注記） / §9 Setup / §10 Command Reference / §11 Knowledge Base-Wiki / §14 Directory Structure / §15 Engineering Highlights の9セクション（うち§8,9,10,15はREADME.md日本語版へのリンクで代替）。TODOマーカーなしで実文が入っているのは §1-4（完全翻訳）と §12 Infrastructure / §13 Tech Stack / §16 Roadmap のみ |
| アーキテクチャ図 | ✅ 完了 | `docs/diagrams/architecture.mmd` |
| セキュリティ・リスク管理設計の説明 | ✅ 完了 | `docs/SAFETY.md`（4層防御、2026-06-03緩和の経緯も明記） |
| テストの内訳 | ✅ 完了 | `docs/TEST_REPORT.md`（`scripts/gen_test_report.py` 自動生成。**334件**中332 PASSED / 2 FAILED-ERROR、成功率99.4% — 元TODOの「332件」は総数ではなくPASSED数だった可能性） |
| CI/CDの説明 | ✅ 完了 | `docs/CICD.md`（CIのみ実装、CD未実装と明記） |
| BBSと研究の接続図 | ✅ 完了 | `docs/BBS_RESEARCH_CONNECTION.md` + `docs/diagrams/bbs_research_connection.mmd` |
| デモ動画 | ⬜ 未着手 | — |
| 技術ブログ3本 | ✅ 全3本完成 | 下記参照 |

**残タスクは「デモ動画」＋「README.en.md §5-16の完全翻訳（9セクション、うち§8は安全に関わるため要約せず逐語翻訳が必要）」の2件**です。

## 技術ブログ3本（全て完成済み）

| # | タイトル | ファイル | 行数 |
|---|---|---|---|
| ① | 「LLMトレードボットに四層安全装置を実装した話」 | `docs/blog/01_four_layer_safety.md` | 87行 |
| ② | 「マルチエージェントAIの判断をBBSで因果トレースする」 | `docs/blog/02_bbs_causal_tracing.md` | 103行 |
| ③ | 「AIのBUYではなくHOLDを評価する研究設計」 | `docs/blog/03_hold_evaluation_research_design.md` | 111行 |

> ①③の執筆・更新時は `docs/DRIFT_CHECK.md` と `docs/POTENTIAL_BUGS.md`
> （skill分割Phase 2で作成、draft）を突き合わせに使うこと。特に①は
> CircuitBreakerの発動基準（連続損失回数ではなくドローダウン率）、
> ③は「中立ゾーン0.40〜0.60」の実体（単一定数ではなく2箇所の定数の組み合わせ）
> に関する食い違いが見つかっている。

## 今後のロードマップ

### Phase A: システム安定化

- [x] GitHub Actions による自動テスト（CI）
- [~] uema2lab-search への自動デプロイ（CD / git pull hook）— `run_bot.sh` の
      `git pull origin main` が簡易版として機能中。GitHub Actions側のCD
      ジョブ化は未着手
- [ ] `scripts/preflight_check.py` のCI組み込み（同スクリプトの存在は
      本skill作成時に未確認。着手前に実在確認が必要）

### Phase B: 研究・評価強化（論文向け）

- [ ] アブレーション実験の系統的実施・結果集計
- [ ] FinanceBenchによるRAG精度ベンチマーク（市販システムとの比較）—
      `evaluate_financebench.py` は削除済み
      （`.claude/skills/evaluation-research/SKILL.md` 参照、
      `git show c7f4ada^:evaluate_financebench.py` で旧実装を復元可能）
- [ ] Faithfulnessスコアのトレンド分析
- [ ] 介入実験（CriticAgentあり vs なし）の有意差検定

### Phase C: UI / 可視化

- [~] Streamlit ダッシュボードの完成（`dashboard/app.py` 実装済み、
      リアルタイムBBS状態・ポートフォリオP&L表示の網羅性は未検証）
- [ ] Wiki（Obsidian）との双方向連携

### Phase D: 学習ループ

- [ ] `data/training/training_data.jsonl` を用いたLLMファインチューニング
- [ ] エージェントウェイトのオンライン最適化（強化学習）

> ⚠️ Phase D着手前に `docs/POTENTIAL_BUGS.md` #2
> （`skills/training_data_collector.py` のローカル `_WEIGHTS` が本番と
> 非同期）を解消しておくこと。学習データの重み記録が本番と食い違ったまま
> ファインチューニングに進むと、収集データの妥当性に疑義が残る。

## ポートフォリオ関連の残タスク（原文の「いずれやりたいことリスト」より）

- デモ動画（未着手）
- README.en.md §5-16 の完全翻訳（9セクション、`<!-- TODO -->` マーカー箇所。
  §8 Enabling Live Trading は安全に関わるため要約せず逐語翻訳が必要と
  ファイル自身が注記している）

---

## Draft状態の内部メモ（コミット判断待ち）

skill分割Phase 2作業中に作成した以下の2ファイルは **draft** であり、
このコミットには含めない：

- `docs/DRIFT_CHECK.md` — ドキュメントと実装の食い違い（実装が正）を記録
- `docs/POTENTIAL_BUGS.md` — 実装が設計意図と違う可能性がある箇所を記録
  （ステータス⚠️、修正はユーザー承認必須）

両ファイルとも「skill分割完了後、ブログ②・BBS-研究接続図・README §1-4との
突き合わせに使う」目的で蓄積中。コミットタイミングはユーザー判断待ち。
