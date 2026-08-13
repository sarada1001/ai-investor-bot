# ============================================================
# ai-investor-bot — 実行環境イメージ
# ------------------------------------------------------------
# このイメージが持つもの : Python 3.12 + requirements.txt の依存 + アプリのソース
# このイメージが持たないもの: .env（APIキー）と、data/ bbs/ logs/ などの状態ファイル
#
# 「状態を持たないイメージ」＋「状態はホストの volume」という分離が
# このプロジェクトの設計の中心。理由は docs/DOCKER.md を参照。
# ============================================================

# ── ベースイメージ ──────────────────────────────────────────
# python:3.12-slim を使う。3.12 で固定するのは好みではなく必然:
#
#   Python 3.13 以降だと pip が pandas 3.0.2 を解決する際に
#   numpy>=2.3.3 を要求する。一方 pandas-ta が引き込む
#   numba 0.61.2 は numpy<2.3 しか許さない。
#   → requirements.txt が「解決不能」でビルドが必ず失敗する。
#
# 本番で実績のある組み合わせが Python 3.12 + numpy 2.2.6 なので、
# それをそのまま固定する。
#
# -slim は Debian の最小構成。フル版(python:3.12)との差は約 700MB。
# ビルドツールや man ページが入っていないだけで、Python 本体は同じ。
FROM python:3.12-slim

# ── ビルド引数: コンテナ内ユーザーの UID/GID ────────────────
# なぜ必要か:
#   data/ や bbs/ はホストのディレクトリをそのままマウントする。
#   コンテナが root(UID 0) で動くと、コンテナが新規作成した
#   portfolio.json などが「ホスト側で root 所有」になり、
#   ホストのユーザーが編集も削除もできなくなる（sudo が要る）。
#   コンテナ内ユーザーの UID をホストのユーザーと一致させれば
#   この問題は起きない。
#
# 開発機・本番機ともに最初のユーザーは 1000:1000 なのでこれを既定値にする。
# 違う場合は `docker compose build --build-arg UID=$(id -u) --build-arg GID=$(id -g)`
ARG UID=1000
ARG GID=1000

# ── 環境変数 ────────────────────────────────────────────────
# PYTHONUNBUFFERED=1
#   Python は標準出力がパイプ（＝cron のログリダイレクト先）だと
#   出力を数KB溜め込んでから吐く。これだと `docker logs` を見ても
#   何も出ず、実行が止まっているのか進んでいるのか判別できない。
#   1 にすると print が即座に流れるので、cron ログが実時間で追える。
ENV PYTHONUNBUFFERED=1

# TZ=Asia/Tokyo
#   コンテナの既定タイムゾーンは UTC。このボットは cron で
#   23:00 JST（＝米国市場の寄り付き前後）に動く前提で作られており、
#   UTC のままだと 9 時間ずれて市場が閉じている時間に動いてしまう。
#   ログのタイムスタンプも JST でないと本番ログと突き合わせられない。
#   （下で tzdata を入れないと、この値を設定しても Python が
#     Asia/Tokyo を解決できないので注意）
ENV TZ=Asia/Tokyo

# HF_HOME
#   FundamentalAgent の RAG が intfloat/multilingual-e5-small という
#   埋め込みモデルを HuggingFace から自動ダウンロードする（約 470MB）。
#   既定のダウンロード先は ~/.cache/huggingface だが、
#   このボットは `docker compose run --rm` で毎回コンテナを捨てる運用なので、
#   そのままだと実行のたびに 470MB を再ダウンロードすることになる。
#   ここでパスを固定し、docker-compose.yml でこのパスに名前付き volume を
#   割り当てて、コンテナを捨ててもキャッシュが残るようにする。
ENV HF_HOME=/home/bot/.cache/huggingface

# ── システムパッケージ ──────────────────────────────────────
# tzdata:
#   タイムゾーンの定義データ本体。slim イメージには入っていない。
#   これが無いと TZ=Asia/Tokyo を設定しても Python の
#   ZoneInfo("Asia/Tokyo") が ZoneInfoNotFoundError で落ちる。
#
# --no-install-recommends:
#   「推奨」扱いのパッケージを引き込まない。イメージが無駄に太らない。
#
# rm -rf /var/lib/apt/lists/*:
#   apt のパッケージ一覧キャッシュ(数十MB)を同じ RUN の中で消す。
#   別の RUN で消しても、レイヤーは追記式なので前のレイヤーに
#   残ったままサイズが減らない。だから「同じ行で」消す必要がある。
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# ── 実行用ユーザーの作成 ────────────────────────────────────
# --system: ログイン用ではない、サービス実行専用のユーザーにする
# --home  : HOME を作る。HF_HOME がこの下を指しているので必須
RUN groupadd --gid ${GID} bot \
    && useradd --uid ${UID} --gid ${GID} --create-home --home-dir /home/bot bot

# HF キャッシュのディレクトリを先に作り、bot 所有にしておく。
# なぜここで作るのか:
#   空の名前付き volume を初めてマウントすると、Docker は
#   「イメージ側の同じパスの所有者・パーミッション」をコピーして
#   volume を初期化する。ここで bot 所有で作っておかないと、
#   volume が root 所有で初期化され、bot ユーザーが
#   モデルをキャッシュに書けずダウンロードに失敗する。
RUN mkdir -p ${HF_HOME} && chown -R ${UID}:${GID} /home/bot

# ── 作業ディレクトリ ────────────────────────────────────────
# アプリのコードは /app に置く。
# 重要: このボットは Path("bbs") や "chroma_db_saved" のように
# 「カレントディレクトリからの相対パス」でファイルを読み書きする。
# つまり実行時のカレントが /app であることが前提になる。
# docker-compose.yml の volume も /app/data のように /app 配下へ張る。
WORKDIR /app

# ── PyTorch (CPU版) を先に入れる ────────────────────────────
# なぜ requirements.txt と分けるのか:
#   requirements.txt の sentence-transformers が torch に依存している。
#   PyPI の既定の torch wheel は CUDA 同梱版で、nvidia-* の依存だけで
#   5GB 近くになる（開発機の venv が 6.5GB あるのはこれが理由）。
#   このボットは埋め込みベクトルを計算するだけで GPU を使わないし、
#   移行先の自宅常時稼働機にも GPU は無い。
#   PyTorch 公式の CPU 専用インデックスから先に入れておけば、
#   後段の pip は「torch は既に条件を満たしている」と判断して
#   CUDA 版を引き直さない。イメージが約 5GB 小さくなる。
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch

# ── 依存パッケージ ──────────────────────────────────────────
# requirements.txt「だけ」を先に COPY するのが肝。
#
# なぜか:
#   Docker はビルドを「レイヤー」に分け、各レイヤーの入力が
#   前回と同じならキャッシュを再利用する。
#   もし先に `COPY . .` でソース全部を入れてから pip install すると、
#   engine/trade_cycle.py を1行直しただけで COPY レイヤーが変わり、
#   その後ろの pip install も全部やり直しになる（毎回 5〜10分）。
#
#   requirements.txt だけを先に COPY すれば、依存が変わっていない限り
#   この pip install レイヤーはキャッシュが効く。
#   ソース変更だけのビルドは数秒で終わる。
#   このボットは cron から `git pull → build → run` する運用なので、
#   平日毎日ビルドが走る。ここの差がそのまま運用コストになる。
#
# --no-cache-dir:
#   pip のダウンロードキャッシュ(数百MB)をイメージに残さない。
#   イメージ内で pip を再実行することはないので、あっても無駄。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── アプリケーションのソース ────────────────────────────────
# .dockerignore で venv/ や data/ を除外済みなので、ここで入るのは
# main.py / engine/ / agents/ / skills/ / tools/ / scripts/ など
# 実行に必要なコードだけ。
# --chown で bot 所有にしておく（root 所有だと将来ソース隣に
# 一時ファイルを書く処理が入ったときに詰まる）。
COPY --chown=${UID}:${GID} . .

# ── 状態ディレクトリのマウントポイント ──────────────────────
# docker-compose.yml がここへホストのディレクトリを重ねる。
# 先に bot 所有で作っておくのは、compose の volume 指定を
# 一時的に外して動かしたときにも権限エラーで落ちないようにするため。
RUN mkdir -p /app/data /app/bbs /app/logs /app/backups /app/chroma_db_saved \
    && chown -R ${UID}:${GID} /app

# ── 非 root で実行 ──────────────────────────────────────────
# ここから先（コンテナ実行時）は bot ユーザーになる。
USER bot

# ── 既定コマンド ────────────────────────────────────────────
# `docker compose run --rm bot` を引数なしで叩いたときの動作。
# 実運用のコマンド（--screen --notify-line など）は cron 側から
# 明示的に渡すので、ここでは「誤って発注処理が走らない」ように
# ヘルプ表示に留めておく。
CMD ["python", "main.py", "--help"]
