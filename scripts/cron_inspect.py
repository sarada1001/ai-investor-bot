"""
scripts/cron_inspect.py — crontab の静的解析（読み取り専用）

`scripts/health_check.py` の A-1 / A-2 チェックが使う純粋な解析ロジック。
crontab を読むだけで、書き換えは一切行わない。

## なぜ独立モジュールなのか

cron の行フォーマット・リダイレクト・`cd` によるカレントディレクトリ変更の
解釈は、ヘルスチェックの判定ロジックとは独立した「文字列解析の問題」であり、
単体でテストできる。health_check 本体から切り離すことで、
パーサの取りこぼし（今回の事故の再発）をピンポイントで検証できる。

## cron のパス解決セマンティクス（最重要）

cron はジョブを **$HOME をカレントディレクトリとして** 起動する。
したがって crontab の `scripts/health_check.py` は
`$HOME/scripts/health_check.py` に解決される。
リポジトリ内に同名ファイルを作っても cron からは見えない。

この区別を潰して「どこかに在れば OK」と判定すると、
「ファイルを作ったのに cron は失敗し続ける」状態を見逃す。
`resolve_cron_path()` が cron 基準とプロジェクト基準を別々に返すのはこのため。
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 環境変数代入行（MAILTO="" / PATH=... など）
_CRON_ENV_RE     = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")
# @reboot / @daily などの特殊スケジュール
_CRON_SPECIAL_RE = re.compile(r"^@\w+\s+")
# `> file` `>> file` `2>> file` `< file`。`2>&1` は target が "&1" になる
_REDIRECT_RE     = re.compile(r"(?P<op>\d?>>?|\d?<)\s*(?P<target>&?[^\s;&|<>]+)")

_SHELL_OPERATORS = {"&&", "||", ";", "|", "(", ")", "{", "}", "&"}


def read_crontab() -> tuple[list[str] | None, str]:
    """`crontab -l` の出力行を返す。

    Returns:
        (lines, note)  読めなければ (None, 理由)
    """
    try:
        proc = subprocess.run(["crontab", "-l"], capture_output=True,
                              text=True, timeout=10)
    except FileNotFoundError:
        return None, "crontab コマンドが見つかりません"
    except Exception as e:
        return None, f"crontab -l 実行失敗: {type(e).__name__}"
    if proc.returncode != 0:
        # 「no crontab for <user>」も returncode != 0 で返る
        return None, f"crontab -l が異常終了 (rc={proc.returncode})"
    return proc.stdout.splitlines(), ""


def extract_cron_commands(lines: list[str]) -> list[str]:
    """crontab の各行からコマンド部分だけを取り出す。

    コメント行・環境変数代入行（MAILTO="" など）・空行は除外する。
    """
    commands: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or _CRON_ENV_RE.match(line):
            continue
        if line.startswith("@"):
            special = _CRON_SPECIAL_RE.match(line)
            if not special:
                continue
            command = line[special.end():]
        else:
            fields = line.split(None, 5)
            if len(fields) < 6:
                continue  # 時刻フィールドが揃っていない → cron 行ではない
            command = fields[5]
        # cron は未エスケープの % を「以降を標準入力に流す」記号として扱う
        command = re.split(r"(?<!\\)%", command)[0].replace(r"\%", "%")
        if command.strip():
            commands.append(command.strip())
    return commands


def tokenize(command: str) -> list[str]:
    """リダイレクトを除去したうえでコマンドをトークン分割する。"""
    stripped = _REDIRECT_RE.sub(" ", command)
    try:
        return shlex.split(stripped)
    except ValueError:
        # 引用符が閉じていない等。素朴な分割にフォールバックし、検知を止めない
        return stripped.split()


def is_path_like(token: str) -> bool:
    """パスとして実在確認する価値のあるトークンか判定する。"""
    if not token or token in _SHELL_OPERATORS or token.startswith("-"):
        return False
    if "=" in token.split("/")[0]:
        return False  # FOO=bar 形式の環境変数指定
    if any(c in token for c in "*?$`"):
        return False  # グロブ・変数展開は静的に解決できない
    return token.endswith((".py", ".sh")) or "/" in token


def cron_cwd(tokens: list[str]) -> Path | None:
    """コマンド列に `cd <dir>` があればその作業ディレクトリを返す。"""
    for i, token in enumerate(tokens[:-1]):
        if token == "cd":
            candidate = Path(tokens[i + 1]).expanduser()
            if candidate.is_absolute():
                return candidate
    return None


def resolve_cron_path(token: str, cwd: Path | None) -> tuple[Path | None, Path | None]:
    """cron が実際に解決するパスと、プロジェクト基準のパスを返す。

    cron はジョブを $HOME 直下で起動するため、相対パスは $HOME 基準になる。
    `cd <dir> && ...` があればそちらが基準。

    Returns:
        (cron_path, project_path)
        cron_path    — cron の CWD 基準で実在するパス（無ければ None）
        project_path — プロジェクトルート基準で実在するパス（無ければ None）

    両方 None なら「どこにも無い」= FAIL。
    cron_path が None で project_path があるなら
    「ファイルはあるが cron からは見えない」= WARN。
    """
    raw = Path(token).expanduser()
    if raw.is_absolute():
        return (raw if raw.exists() else None), None

    cron_base = cwd if cwd is not None else Path.home()
    cron_path = cron_base / raw
    project_path = PROJECT_ROOT / raw
    return (
        cron_path if cron_path.exists() else None,
        project_path if project_path.exists() else None,
    )


def redirect_targets(command: str) -> list[str]:
    """リダイレクト先のパス文字列を返す（`2>&1` のような fd 複製は除く）。"""
    targets = []
    for match in _REDIRECT_RE.finditer(command):
        target = match.group("target")
        if target.startswith("&"):
            continue  # 2>&1 等は fd の複製であってファイルではない
        targets.append(target)
    return targets


def cp_destinations(tokens: list[str]) -> list[str]:
    """`cp`／`mv`／`rsync` の転送先トークンを返す。"""
    destinations = []
    for i, token in enumerate(tokens):
        if token not in ("cp", "mv", "rsync"):
            continue
        args = []
        for arg in tokens[i + 1:]:
            if arg in _SHELL_OPERATORS:
                break
            if not arg.startswith("-"):
                args.append(arg)
        if len(args) >= 2:
            destinations.append(args[-1])
    return destinations
