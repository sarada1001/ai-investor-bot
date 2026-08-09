"""tests/test_cron_inspect.py — scripts/cron_inspect.py のユニットテスト

crontab 文字列の解析だけを対象にした純粋なテスト。
ここでの取りこぼしが「cron が半年間失敗し続けても誰も気づかない」に直結するため、
コメント行・特殊スケジュール・リダイレクト・cd によるカレントディレクトリ変更を
それぞれ独立に検証する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scripts.cron_inspect as ci


@pytest.fixture
def fake_home(monkeypatch, tmp_path) -> Path:
    """cron の CWD（= $HOME）として使う空ディレクトリ。"""
    home = tmp_path / "cron_home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home



class TestCrontabParsing:
    def test_skips_comments_and_env_assignments(self):
        lines = [
            "# 日次スクリーニング",
            'MAILTO=""',
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "",
            "0 23 * * 1-5 /home/naito/ai-investor-bot/run_bot.sh",
        ]
        commands = ci.extract_cron_commands(lines)
        assert commands == ["/home/naito/ai-investor-bot/run_bot.sh"]

    def test_extracts_command_after_five_time_fields(self):
        lines = ["0 13 * * 1-5 scripts/health_check.py >> logs/health.log 2>&1"]
        assert ci.extract_cron_commands(lines) == [
            "scripts/health_check.py >> logs/health.log 2>&1"
        ]

    def test_handles_special_schedule(self):
        assert ci.extract_cron_commands(["@reboot /usr/bin/foo.sh"]) == ["/usr/bin/foo.sh"]

    def test_truncates_at_unescaped_percent(self):
        """cron は未エスケープの % 以降を標準入力として扱うため除外する。"""
        commands = ci.extract_cron_commands(["0 1 * * * /bin/mail.sh%body text"])
        assert commands == ["/bin/mail.sh"]

    def test_ignores_lines_with_too_few_fields(self):
        assert ci.extract_cron_commands(["0 13 * *"]) == []


class TestTokenize:
    def test_redirect_targets_are_removed_from_tokens(self):
        tokens = ci.tokenize("scripts/health_check.py >> logs/health.log 2>&1")
        assert tokens == ["scripts/health_check.py"]

    def test_cd_target_is_preserved(self):
        tokens = ci.tokenize("cd /home/naito/app && venv/bin/python3 main.py")
        assert "/home/naito/app" in tokens
        assert "venv/bin/python3" in tokens

    def test_unbalanced_quotes_fall_back_instead_of_raising(self):
        """引用符が壊れていても検知を止めない（例外で全体が死ぬのを防ぐ）。"""
        assert ci.tokenize('foo.sh "unclosed') == ["foo.sh", '"unclosed']


class TestIsPathLike:
    @pytest.mark.parametrize("token", [
        "scripts/health_check.py", "run_bot.sh", "/usr/bin/python3", "venv/bin/python3",
    ])
    def test_accepts_paths(self, token):
        assert ci.is_path_like(token)

    @pytest.mark.parametrize("token", [
        "--screen", "&&", "FOO=bar", "data/*.json", "$HOME/x.sh", "python3",
    ])
    def test_rejects_non_paths(self, token):
        assert not ci.is_path_like(token)


class TestResolveCronPath:
    def test_relative_path_resolves_against_home_not_project_root(self, fake_home):
        """cron は $HOME でジョブを起動する。プロジェクト基準で解決してはいけない。"""
        (fake_home / "scripts").mkdir()
        (fake_home / "scripts" / "x.py").write_text("")

        cron_path, project_path = ci.resolve_cron_path("scripts/x.py", cwd=None)
        assert cron_path == fake_home / "scripts" / "x.py"
        assert project_path is None

    def test_cd_target_overrides_home(self, fake_home, tmp_path):
        (tmp_path / "a.sh").write_text("")
        cron_path, _ = ci.resolve_cron_path("a.sh", cwd=tmp_path)
        assert cron_path == tmp_path / "a.sh"

    def test_absolute_missing_path_returns_none(self, tmp_path):
        cron_path, project_path = ci.resolve_cron_path(str(tmp_path / "nope.py"), cwd=None)
        assert cron_path is None and project_path is None
