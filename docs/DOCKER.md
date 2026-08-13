# Docker 運用ガイド

研究室サーバ（uema2lab-search）から自宅の常時稼働機へ本番を移行するための、
環境コード化と運用手順。

---

## 1. なぜ Docker にするのか

これまでの本番環境には以下の問題があった。

| 問題 | Docker でどう解決されるか |
|---|---|
| 環境が手作業で構築されており再現不能 | `Dockerfile` が構築手順そのもの。`docker compose build` で誰でも同じ環境が作れる |
| 開発機 Python 3.14 / 本番 3.12 で依存が乖離 | イメージが Python 3.12 に固定されるので、開発機の Python が何であれ実行環境は同一 |
| cron 定義・起動スクリプトが git 管理外に散在 | `crontab.example` と compose 定義が git に入り、機械の引っ越しがコピーだけで済む |

**ただし Docker は「状態」までは面倒を見てくれない。**
そこが次章。

---

## 2. volume の設計意図（最重要）

### 2.1 コンテナのファイルシステムは毎回消える

このボットは `docker compose run --rm` で動かす。`--rm` は
「実行が終わったらコンテナを破棄する」という意味で、
**コンテナの中に書いたファイルは全部消える**。

これは利点でもある。「前回の実行の残骸が次回に影響する」ことが原理的に起きない。
だが、消えては困るファイルもある。

### 2.2 消えて良いもの／絶対に消してはいけないもの

| 分類 | 中身 | 置き場所 |
|---|---|---|
| 消えて良い | Python 本体、pip で入れた依存、アプリのソース | **イメージの中**（再ビルドで復元できる） |
| 消えては困る | ポートフォリオ、学習データ、セッション記録、ログ | **ホストのディレクトリ**（volume でマウント） |

過去に実際に状態ファイルを失う事故が起きているため、
ここは「たぶん大丈夫」で済ませてはいけない箇所。

### 2.3 マウントしているディレクトリと理由

`docker-compose.yml` の `volumes:` に定義済み。

| ホスト | コンテナ | 失うと何が起きるか |
|---|---|---|
| `./data` | `/app/data` | `portfolio.json`（保有ポジション）、`trade_guard_state.json`、`circuit_breaker_state.json`、`training/training_data.jsonl`（卒研用・**再生成不可**）、`knowledge_base/`、screener キャッシュがすべて消える。事実上の口座喪失 |
| `./bbs` | `/app/bbs` | 各エージェントの判断根拠の記録が消える。「なぜこの銘柄を買ったか」を後から追えなくなる |
| `./logs` | `/app/logs` | health_check / daemon / weekly_reflection のログが消える。障害調査の起点が無くなる |
| `./backups` | `/app/backups` | `portfolio.json` の日次バックアップ。`data/` が壊れたときの最後の砦 |
| `./chroma_db_saved` | `/app/chroma_db_saved` | FundamentalAgent の RAG ベクトルストア。消えると SEC EDGAR から決算書を取り直して再ベクトル化することになり、数十分＋EDGAR のレート制限に当たる |
| `hf-cache`（名前付き volume） | `/home/bot/.cache/huggingface` | 埋め込みモデル `intfloat/multilingual-e5-small`（約 470MB）を**実行のたびに再ダウンロード**することになる |

> **`chroma_db_saved/` について**
> タスク指示では「ChromaDB は `data/` 配下」とされていたが、
> 実際のコードは `persist_dir="chroma_db_saved"`（リポジトリ直下）を
> 相対パスで直書きしている（`agents/fundamental_agent.py`,
> `skills/rag_search.py`, `skills/edgar_fetcher.py`, `skills/financial_data_loader.py`）。
> そのためリポジトリ直下のまま独立した volume にしてある。

> **`hf-cache` だけ形式が違う理由**
> 他の 5 つは「ホストの `./data` を `/app/data` に見せる」バインドマウント。
> `hf-cache` は置き場所を Docker に任せる**名前付き volume**。
> 中身は消えても再ダウンロードできるので git 管理下に置く必要がなく、
> かといって毎回 470MB 落とすのは論外、という中間の性質だから。
> 実体は `/var/lib/docker/volumes/` 配下にある。

### 2.4 現時点でカバーできていないもの

- `latest_summary.md`（`server_librarian.py` が生成する日報）はリポジトリ直下の
  **単一ファイル**。単一ファイルのバインドマウントは、ホスト側にファイルが
  無いと Docker がディレクトリを作ってしまい壊れるため、あえてマウントしていない。
  日報をコンテナ経由で生成する運用に移す場合は、出力先を `data/` 配下に
  変更するのが安全（＝アプリ側の修正が必要）。

---

## 3. セットアップ（自宅機での移行手順）

### 3.1 前提

- Docker Engine（または Docker Desktop）と `docker compose` v2 が入っていること
- 実行ユーザーが `docker` グループに属していること（`sudo docker` を毎回打たずに済む）

```bash
docker --version
docker compose version
```

### 3.2 clone

```bash
cd ~
git clone <このリポジトリのURL> ai-investor-bot
cd ai-investor-bot
```

### 3.3 `.env` を配置する

`.env` は git に入っていない（Alpaca 実弾取引の API キーが入っているため）。
旧本番機から **安全な経路で** コピーするか、テンプレートから作る。

```bash
cp .env.example .env
vi .env   # 各キーを埋める
```

必須キー（`scripts/health_check.py` が検査している）:

- `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` — Alpaca ペーパー取引
- `APCA_API_KEY_ID_LIVE` / `APCA_API_SECRET_KEY_LIVE` — Alpaca 実弾取引
- `ALPACA_PAPER_TRADING` — `True` でペーパー、`False` で実弾
- `GOOGLE_API_KEY` — Gemini
- `LINE_ACCESS_TOKEN` / `LINE_USER_ID` — LINE 通知

> **`.env` の書式に注意**
> `docker compose` は `.env` を自分でもパースする。
> 行頭の空白は compose 側で除去されるため現状は動くが、
> 依存する挙動ではないので `KEY=VALUE` は行頭から書くこと。
> 現在の開発機の `.env` には行頭に空白のある行が数行あるので、
> 移行時に直しておくとよい。

> **`docker compose config` は打たないこと**
> 設定の確認に使いたくなるが、このコマンドは `.env` から読み込んだ
> **API キーを平文で標準出力に流す**。
> 端末の履歴やログに残ると漏洩経路になる。
> 設定を確認したいときは、値を伏せて構造だけ見る:
> ```bash
> docker compose config | sed -E 's/(KEY|TOKEN|SECRET)[A-Z_]*:.*/\1: ***/I'
> ```

> **`DISABLE_GEMINI` を必ず `false` にすること（移行時の落とし穴）**
> 開発機の `.env` には Ollama 時代の名残で `DISABLE_GEMINI=true` と
> `OLLAMA_BASE_URL=http://localhost:11434` が残っている。
> この状態でコンテナを動かすと、`localhost` が「コンテナ自身」を指すため
> Ollama に到達できず、ExitAgent の初期化で以下のように落ちる:
>
> ```
> RuntimeError: DISABLE_GEMINI=true が設定されており、Ollama にも接続できません。
> ```
>
> ホスト上では同じコマンドが通ってしまう（ホストには Ollama が
> 動いているため）ので、コンテナに移して初めて顕在化する。
> 本プロジェクトは Gemini 一本化済みなので、移行先の `.env` では
>
> - `DISABLE_GEMINI=false`
> - `GOOGLE_API_KEY=<実際のキー>`
>
> とすること。`OLLAMA_*` の行は消してよい。

### 3.4 ビルド

```bash
docker compose build
```

初回は PyTorch(CPU版)・chromadb・sentence-transformers などを入れるため
数分〜十数分かかる。2 回目以降、`requirements.txt` を変えていなければ
依存インストールのレイヤーはキャッシュが効き、数秒で終わる。

> ホストのユーザーが UID 1000 でない場合は、
> `HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose build` とする。
> （理由は 5.1 を参照）

### 3.5 状態ファイルを旧本番機から移送する

**ここを飛ばすと、まっさらなポートフォリオでボットが動き出す。**

旧本番機（`uema2lab-search:/home/naito/ai-investor-bot`）から:

```bash
# 旧本番機で、まず cron を止める（二重稼働を防ぐ）
crontab -e   # 該当行をコメントアウト

# 旧本番機 → 自宅機へ転送
#   -a: 権限とタイムスタンプを保持
#   末尾のスラッシュの有無で挙動が変わるので下記の形をそのまま使うこと
rsync -av naito@uema2lab-search:/home/naito/ai-investor-bot/data/            ./data/
rsync -av naito@uema2lab-search:/home/naito/ai-investor-bot/bbs/             ./bbs/
rsync -av naito@uema2lab-search:/home/naito/ai-investor-bot/logs/            ./logs/
rsync -av naito@uema2lab-search:/home/naito/ai-investor-bot/backups/         ./backups/
rsync -av naito@uema2lab-search:/home/naito/ai-investor-bot/chroma_db_saved/ ./chroma_db_saved/
```

移送後の確認:

```bash
# 保有ポジションが移っているか
cat data/portfolio.json | head -20

# 所有者が自分になっているか（root だとコンテナから書けない）
ls -la data/ bbs/
```

### 3.6 動作確認

```bash
# Python と主要ライブラリのバージョン確認
# 期待値: 3.12.x と numpy 2.2.6
docker compose run --rm bot python -c "import sys, numpy, pandas_ta; print(sys.version, numpy.__version__)"

# タイムゾーンが JST になっているか
docker compose run --rm bot date

# 本番の状態に一切触れないスモークテスト（8章を参照）

# preflight（API キーの疎通確認）
docker compose run --rm bot python scripts/preflight_check.py

# health check
docker compose run --rm bot python scripts/health_check.py
```

---

## 4. 日常の使い方

すべて `docker compose run --rm bot <コンテナ内で実行するコマンド>` の形。

```bash
# スクリーニング + ドライラン（発注しない）
docker compose run --rm bot python main.py --screen --dry-run

# スクリーニング + LINE 通知（本番と同じ動作）
docker compose run --rm bot python main.py --screen --notify-line

# 単一銘柄
docker compose run --rm bot python main.py --ticker AAPL --dry-run

# 日報生成
docker compose run --rm bot python server_librarian.py

# Wiki 更新
docker compose run --rm bot python server_librarian.py --ingest

# Wiki ヘルスチェック
docker compose run --rm bot python scripts/lint_wiki.py

# テスト
docker compose run --rm bot python -m pytest
```

### コマンドの読み方

```
docker compose run --rm bot python main.py --screen
└──────┬──────┘ └┬┘ └┬┘ └┬┘ └────────┬───────────┘
       │          │   │   │            └ コンテナの中で実行されるコマンド
       │          │   │   └ docker-compose.yml で定義したサービス名
       │          │   └ 終わったらコンテナを消す（残骸を残さない）
       │          └ サービスを1回だけ実行（up と違って常駐しない）
       └ docker-compose.yml を読んで実行する
```

### ソースを更新したとき

イメージにはソースが焼き込まれているので、**`git pull` だけでは反映されない**。

```bash
git pull origin main
docker compose build      # ソースだけの変更なら数秒
docker compose run --rm bot python main.py --screen --notify-line
```

---

## 5. cron の設定（`crontab.example` の docker 版）

### 5.1 コンテナ内で cron を常駐させない理由

- ログが `docker logs` に埋もれ、`grep` しづらい
- コンテナの中に入らないとスケジュールを確認できない
- プロセスが死んでもコンテナは生きているように見える（旧本番でゾンビプロセス事故が発生済み）

スケジューリングはホストの cron に任せ、コンテナは「1回走って死ぬバッチ」に徹する。

### 5.2 crontab の例

`BOT_DIR` は自宅機での clone 先に置き換えること。

```cron
SHELL=/bin/bash
MAILTO=""

# cron の PATH は極端に短く /usr/bin が入らないことがある。docker が見つからない
# 事故を避けるため明示する。
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# ── health check（22:00 JST） ─────────────────────────────
0 22 * * 1-5 cd /home/USER/ai-investor-bot && docker compose run --rm bot python scripts/health_check.py >> logs/health.log 2>&1

# ── メイン実行（23:00 JST） ───────────────────────────────
# git pull → build → run の3段。build はソース変更が無ければ数秒で終わる。
# && で繋いでいるので、pull や build が失敗したら run は走らない
#（＝古いコードや壊れたイメージで発注判断が走ることを防ぐ）。
0 23 * * 1-5 cd /home/USER/ai-investor-bot && git pull origin main && docker compose build && docker compose run --rm bot python main.py --screen --notify-line >> logs/bot_cron.log 2>&1

# ── パフォーマンスレポート（23:50 JST） ───────────────────
50 23 * * 1-5 cd /home/USER/ai-investor-bot && docker compose run --rm bot python scripts/run_performance_report.py >> logs/performance.log 2>&1

# ── 日報生成 / Wiki 更新（翌 0:00, 0:05 JST） ──────────────
0 0 * * 2-6 cd /home/USER/ai-investor-bot && docker compose run --rm bot python server_librarian.py >> logs/server_librarian.log 2>&1
5 0 * * 2-6 cd /home/USER/ai-investor-bot && docker compose run --rm bot python server_librarian.py --ingest >> logs/server_librarian.log 2>&1

# ── portfolio.json の日次バックアップ（21:00 JST） ─────────
# これは docker を経由しない。ホストから見える ./data のファイルを
# ホストの cp でコピーするだけ。docker を挟む理由がない。
0 21 * * * cp /home/USER/ai-investor-bot/data/portfolio.json /home/USER/ai-investor-bot/backups/portfolio_$(date +\%Y\%m\%d).json

# ── 週次リフレクション（土 15:00 JST） ────────────────────
0 15 * * 6 cd /home/USER/ai-investor-bot && docker compose run --rm bot python scripts/run_weekly_reflection.py >> logs/weekly_reflection.log 2>&1
```

### 5.3 タイムゾーンの二重確認

**ホストの cron はホストの TZ で動く。コンテナの TZ とは別物。**

- ホスト側: `timedatectl` が `Asia/Tokyo` になっていること。
  なっていなければ `sudo timedatectl set-timezone Asia/Tokyo`。
- コンテナ側: `docker-compose.yml` の `TZ=Asia/Tokyo` と
  Dockerfile の `tzdata` インストールで担保済み。
  確認は `docker compose run --rm bot date`。

どちらかが UTC のままだと 9 時間ずれて市場が閉じている時間に動く。

---

## 6. トラブルシューティング

### `permission denied` で `data/` に書けない

コンテナのユーザー（UID 1000）とホストのファイル所有者が食い違っている。

```bash
id -u    # ホストの UID を確認
ls -ln data/   # ファイルの所有 UID を確認
```

食い違っていたら、ホストの UID でビルドし直す:

```bash
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose build
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose run --rm bot python main.py --screen --dry-run
```

すでに root 所有のファイルが出来てしまっている場合:

```bash
sudo chown -R $(id -u):$(id -g) data bbs logs backups chroma_db_saved
```

### 埋め込みモデルを毎回ダウンロードしている

`hf-cache` volume が効いていない。確認:

```bash
docker volume ls | grep hf-cache
docker compose run --rm bot python -c "import os; print(os.environ['HF_HOME'])"
```

### ビルドが `No matching distribution found` で落ちる

Python のバージョンがずれている可能性が高い。Dockerfile の `FROM` が
`python:3.12-slim` であることを確認する。3.13 以降にすると
pandas 3.0.2（numpy>=2.3.3 要求）と numba 0.61.2（numpy<2.3 要求）が
衝突して依存解決が必ず失敗する。

### イメージが大きすぎる

`docker images ai-investor-bot` で確認。
PyTorch の CPU 版が正しく入っていれば **3.5GB 前後**（実測 3.49GB）。
7GB 超なら CUDA 版 torch が入っている（Dockerfile の
`--index-url https://download.pytorch.org/whl/cpu` の行が効いていない）。

```bash
docker compose run --rm bot python -c "import torch; print(torch.__version__)"
# 末尾が +cpu であること。+cu130 などなら CUDA 版

docker compose run --rm bot pip list | grep -i nvidia
# 何も出なければ正常。nvidia-* が並んでいたら CUDA 版が入っている
```

### ビルドが `setuptools>=40.8.0 ... from versions: none` で落ちる

`futu-api` と `sgmllib3k` は wheel が配布されておらず、ソースから
ビルドされる。このとき pip は「ビルド専用の隔離環境」を作り、そこへ
setuptools を **PyPI から取り直す**。ここで一瞬でも通信に失敗すると
このエラーになる。

原因は環境ではなく通信の一時的な失敗なので、**もう一度
`docker compose build` を実行すればよい**（実際に初回ビルドで発生し、
再実行で成功している）。依存インストール前のレイヤーはキャッシュが
効くので、やり直しは速い。

### 掃除

```bash
docker compose down            # コンテナを止めて消す（./data 等は無事）
docker image prune             # 使われていないイメージを消す
docker builder prune           # ビルドキャッシュを消す（次回ビルドは遅くなる）
```

> **`docker compose down -v` は打たないこと。**
> `-v` は名前付き volume（`hf-cache`）を消す。
> `./data` などのバインドマウントは消えないので致命傷ではないが、
> 470MB の再ダウンロードが発生する。

---

## 7. やっていないこと（意図的な範囲外）

- 本番サーバへのデプロイ
- 既存 cron の変更（`crontab.example` は旧構成のまま残してある）
- アプリケーションコードの変更
- Ollama（ローカル LLM）関連 — Gemini 一本化済みのため
- マルチステージビルド等の最適化 — まず動くものを優先

---

## 8. スモークテスト（本番の状態に触れずに動作確認する）

`--mock` は LLM も Alpaca も呼ばないが、`bbs/` にはセッションファイルを
新規作成する（`engine/bbs.py` が `BBS_DIR = Path("bbs")` に書き込むため）。
つまり `bbs/` をマウントしたまま実行すると、それだけで差分が出る。

本番の状態ファイルに一切触れずに動作確認したい場合は、
volume の向き先を一時ディレクトリに差し替える:

```bash
# 1. 一時的な状態ディレクトリを用意（data/ は読み込みが必要なのでコピーする）
rm -rf .smoke && mkdir -p .smoke && cp -a data .smoke/data && mkdir -p .smoke/bbs .smoke/logs

# 2. 本番側のチェックサムを控える（bbs/ も data/ も大半が .gitignore 対象で
#    git status では差分が見えないため、ハッシュで確認する）
find data bbs -type f | sort | xargs sha256sum | sha256sum > /tmp/state_before.txt

# 3. -v で volume を上書きして実行
#    -e は .env の値を「このコンテナだけ」上書きする（.env は変更しない）
docker compose run --rm \
  -e DISABLE_GEMINI=false \
  -e GOOGLE_API_KEY=dummy-key-for-mock-run \
  -v "$PWD/.smoke/data:/app/data" \
  -v "$PWD/.smoke/bbs:/app/bbs" \
  -v "$PWD/.smoke/logs:/app/logs" \
  bot python main.py --ticker AAPL --mock

# 4. 本番側に差分が無いことを確認（両者が一致すれば無傷）
find data bbs -type f | sort | xargs sha256sum | sha256sum
cat /tmp/state_before.txt

# 5. 書き込みが .smoke 側に入っていることを確認（volume が効いている証拠）
ls .smoke/bbs/

# 6. 後片付け
rm -rf .smoke
```

`GOOGLE_API_KEY=dummy-key-for-mock-run` で問題ないのは、`--mock` では
LLM の呼び出し自体が行われず、クライアントオブジェクトを生成するだけだから。
`DISABLE_GEMINI=false` を渡さないと、上記の Ollama 到達不能で落ちる。

`--hybrid` 以降（実データを使う検証）は人間が判断して実行すること。

### 実施済みの検証結果（2026-08-13, 開発機 WSL2 + Docker Desktop 29.6.1）

| 検証項目 | 結果 |
|---|---|
| `docker compose build` | 成功（初回は通信起因で失敗、再実行で成功） |
| Python バージョン | `3.12.13` |
| numpy | `2.2.6`（本番実績と一致） |
| pandas / numba | `3.0.2` / `0.61.2`（衝突なし） |
| `pandas_ta` の import | 成功 |
| torch | `2.13.0+cpu`（CUDA パッケージ 0 件） |
| コンテナ内の時刻 | `JST (+09:00)` |
| 実行ユーザー | `uid=1000(bot)` — root ではない |
| `--mock` 実行 | STRONG BUY 判定まで完走 |
| 本番 `data/` `bbs/` の差分 | **無し**（sha256 が実行前と完全一致） |
| コンテナが書いたファイルの所有者 | `komek`（root 所有にならない） |
| イメージサイズ | 3.49GB |
