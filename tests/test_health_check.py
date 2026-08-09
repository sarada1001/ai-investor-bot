"""tests/test_health_check.py — scripts/health_check.py のユニットテスト

重点は「サイレント障害を本当に検知できるか」と
「検知結果が人間に届くか（通知ポリシー）」の 2 点。

状態ファイルのパスは tests/conftest.py の autouse フィクスチャ
`isolate_state_files` が tmp へ張り替えている。本番 data/ は触らない。
"""

from __future__ import annotations

import json
import stat
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.health_check as hc


# ────────────────────────────────────────────────────────────
# ヘルパー
# ────────────────────────────────────────────────────────────

def _ctx(crontab_lines: list[str] | None = None, alpaca_symbols=None) -> hc._Context:
    return hc._Context(alpaca_symbols=alpaca_symbols, crontab_lines=crontab_lines)


@pytest.fixture
def fake_home(monkeypatch, tmp_path) -> Path:
    """cron の CWD（= $HOME）として使う空ディレクトリ。

    `tmp_path` 直下をそのまま使うと conftest が作る data/ ・ logs/ が
    見えてしまい「存在しないはずのディレクトリ」が存在してしまう。
    cron 系のテストは必ずこの隔離された空ディレクトリを $HOME とみなす。
    """
    home = tmp_path / "cron_home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def clean_env(monkeypatch):
    """.env 由来の環境変数を全て消し、テストが開発機の .env に依存しないようにする。"""
    names = {name for aliases in hc._REQUIRED_ENV_KEYS for name in aliases}
    names |= set(hc._RECOMMENDED_ENV_KEYS) | set(hc._SECRET_ENV_KEYS)
    for name in names:
        monkeypatch.delenv(name, raising=False)


def _business_days_ago(n: int) -> date:
    """n 営業日前の日付を返す（土日を飛ばす）。"""
    cursor = date.today()
    remaining = n
    while remaining > 0:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            remaining -= 1
    return cursor


def _write_portfolio(tmp_path: Path, tickers: list[str]) -> None:
    Path(hc.PORTFOLIO_PATH).write_text(json.dumps({
        "schema_version": "1.0",
        "positions": [{"ticker": t, "status": "OPEN"} for t in tickers],
    }), encoding="utf-8")


def _write_training_record(record_date: date) -> None:
    Path(hc.TRAINING_DATA_PATH).write_text(
        json.dumps({"record_id": "r1", "date": record_date.isoformat(),
                    "ticker": "AAPL"}) + "\n",
        encoding="utf-8")


# ────────────────────────────────────────────────────────────
# A-1 / A-2  cron 系
# ────────────────────────────────────────────────────────────

class TestCheckCronScripts:
    def test_missing_script_is_fail(self, fake_home):
        """今回の事故（半年間 No such file or directory）を直接再現する。"""
        result = hc.check_cron_scripts(_ctx(["0 13 * * 1-5 scripts/no_such_job.py"]))
        assert result.level == hc.FAIL
        assert "scripts/no_such_job.py" in result.detail

    def test_existing_script_is_ok(self, fake_home):
        (fake_home / "scripts").mkdir()
        script = fake_home / "scripts" / "health_check.py"
        script.write_text("")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        result = hc.check_cron_scripts(_ctx(["0 13 * * 1-5 scripts/health_check.py"]))
        assert result.level == hc.OK

    def test_project_only_path_is_warn_not_ok(self, fake_home):
        """
        ファイルはリポジトリにあるが cron の CWD からは見えないケース。

        本番 crontab の `scripts/health_check.py` は $HOME=/home/naito 基準で
        解決される。リポジトリにファイルを作っただけでは cron はまだ失敗し
        続けるため、OK にせず WARN で明確に区別する。
        """
        result = hc.check_cron_scripts(_ctx(["0 13 * * 1-5 scripts/health_check.py"]))
        assert result.level == hc.WARN
        assert "CWD" in result.detail

    def test_non_executable_direct_invocation_is_warn(self, fake_home):
        script = fake_home / "run.sh"
        script.write_text("")
        script.chmod(0o644)

        result = hc.check_cron_scripts(_ctx([f"0 1 * * * {script}"]))
        assert result.level == hc.WARN
        assert "実行ビット" in result.detail

    def test_unreadable_crontab_is_warn_not_fail(self):
        """開発機など crontab が無い環境で FAIL 誤報を出さない。"""
        assert hc.check_cron_scripts(_ctx(None)).level == hc.WARN

    def test_empty_crontab_is_warn(self, fake_home):
        """
        ジョブが 1 件も無いのを OK にしない。

        本番でこれが起きれば「ボットが一切スケジュールされていない」という
        最も静かな障害であり、必ず可視化する必要がある。
        """
        result = hc.check_cron_scripts(_ctx(['MAILTO=""', "# コメントのみ"]))
        assert result.level == hc.WARN
        assert "1 件も登録されていません" in result.detail


class TestCheckCronOutputDirs:
    def test_missing_backup_destination_is_fail(self, fake_home):
        """backups/ が無く毎晩 cp が失敗していた件を再現する。"""
        result = hc.check_cron_output_dirs(
            _ctx(["0 2 * * * cp data/portfolio.json backups/portfolio.json"]))
        assert result.level == hc.FAIL
        assert "backups" in result.detail

    def test_missing_redirect_directory_is_fail(self, fake_home):
        result = hc.check_cron_output_dirs(
            _ctx(["0 1 * * * /bin/true >> logs/health.log 2>&1"]))
        assert result.level == hc.FAIL
        assert "logs/health.log" in result.detail

    def test_existing_directories_are_ok(self, fake_home):
        (fake_home / "logs").mkdir()
        result = hc.check_cron_output_dirs(
            _ctx(["0 1 * * * /bin/true >> logs/health.log 2>&1"]))
        assert result.level == hc.OK

    def test_fd_duplication_is_not_treated_as_file(self, fake_home):
        """`2>&1` をファイル出力先と誤認しないこと。"""
        result = hc.check_cron_output_dirs(_ctx(["0 1 * * * /bin/true 2>&1"]))
        assert result.level == hc.OK


# ────────────────────────────────────────────────────────────
# A-3  .env
# ────────────────────────────────────────────────────────────

def _set_all_env(monkeypatch) -> None:
    """全チェックが OK になる最小構成を組む（clean_env の後に呼ぶ）。"""
    for aliases in hc._REQUIRED_ENV_KEYS:
        monkeypatch.setenv(aliases[0], "v" * 20)
    monkeypatch.setenv("GOOGLE_API_KEY", "g" * 20)
    for name in hc._RECOMMENDED_ENV_KEYS:
        monkeypatch.setenv(name, "false")


class TestCheckEnvKeys:
    def test_missing_required_key_is_fail(self, clean_env, monkeypatch):
        _set_all_env(monkeypatch)
        for name in ("APCA_API_KEY_ID", "ALPACA_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        result = hc.check_env_keys(_ctx())
        assert result.level == hc.FAIL
        assert "APCA_API_KEY_ID" in result.detail

    def test_fully_configured_env_is_ok(self, clean_env, monkeypatch):
        _set_all_env(monkeypatch)
        assert hc.check_env_keys(_ctx()).level == hc.OK

    def test_alias_satisfies_required_key(self, clean_env, monkeypatch):
        _set_all_env(monkeypatch)
        monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
        monkeypatch.setenv("ALPACA_API_KEY", "k" * 20)
        assert hc.check_env_keys(_ctx()).level == hc.OK

    def test_secret_values_never_appear_in_detail(self, clean_env, monkeypatch):
        """.env の値がログ・通知へ漏れないこと（最重要の安全要件）。"""
        secret = "super-secret-value-1234567890"
        _set_all_env(monkeypatch)
        monkeypatch.setenv("APCA_API_KEY_ID", secret)
        monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
        result = hc.check_env_keys(_ctx())
        assert result.level == hc.FAIL       # SECRET 側が欠けているので FAIL
        assert secret not in result.detail

    def test_missing_line_credentials_is_fail(self, clean_env, monkeypatch):
        """通知経路そのものが死ぬため FAIL 扱いであること。"""
        _set_all_env(monkeypatch)
        monkeypatch.delenv("LINE_ACCESS_TOKEN", raising=False)
        result = hc.check_env_keys(_ctx())
        assert result.level == hc.FAIL
        assert "LINE_ACCESS_TOKEN" in result.detail

    def test_force_gemini_without_key_is_fail(self, clean_env, monkeypatch):
        _set_all_env(monkeypatch)
        monkeypatch.setenv("FORCE_GEMINI", "true")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        result = hc.check_env_keys(_ctx())
        assert result.level == hc.FAIL
        assert "GOOGLE_API_KEY" in result.detail

    def test_missing_gemini_key_without_force_is_warn(self, clean_env, monkeypatch):
        """Ollama 主系なら即死ではないが、フォールバックが効かないので WARN。"""
        _set_all_env(monkeypatch)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert hc.check_env_keys(_ctx()).level == hc.WARN

    def test_disable_gemini_makes_google_key_unnecessary(self, clean_env, monkeypatch):
        _set_all_env(monkeypatch)
        monkeypatch.setenv("DISABLE_GEMINI", "true")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert hc.check_env_keys(_ctx()).level == hc.OK

    def test_missing_recommended_key_is_warn(self, clean_env, monkeypatch):
        """暗黙のデフォルトで動いている状態を可視化する（モデル名無言死の教訓）。"""
        _set_all_env(monkeypatch)
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        result = hc.check_env_keys(_ctx())
        assert result.level == hc.WARN
        assert "GEMINI_MODEL" in result.detail


# ────────────────────────────────────────────────────────────
# B-5  Alpaca 疎通
# ────────────────────────────────────────────────────────────

class _FakePosition:
    def __init__(self, symbol: str, qty: int = 1):
        self.symbol, self.qty = symbol, qty


class _FakeTradingClient:
    def __init__(self, positions=None, raises: Exception | None = None):
        self._positions, self._raises = positions or [], raises

    def get_all_positions(self):
        if self._raises:
            raise self._raises
        return self._positions


class _FakeAlpacaClient:
    def __init__(self, account=None, positions=None, positions_raise=None):
        self._account = account if account is not None else {"equity": 100_000.0}
        self._tc = _FakeTradingClient(positions, positions_raise)

    def get_account(self):
        return self._account

    def get_positions(self):
        return []


def _patch_alpaca(monkeypatch, client: _FakeAlpacaClient) -> None:
    import tools.alpaca_client as alpaca_mod
    monkeypatch.setattr(alpaca_mod, "AlpacaClient", lambda: client)
    monkeypatch.setattr(alpaca_mod, "_PAPER", True)


class TestCheckAlpacaConnectivity:
    def test_auth_failure_is_fail(self, monkeypatch):
        _patch_alpaca(monkeypatch, _FakeAlpacaClient(account={"error": "unauthorized"}))
        result = hc.check_alpaca_connectivity(_ctx())
        assert result.level == hc.FAIL
        assert "認証失敗" in result.detail

    def test_zero_positions_is_ok_and_labelled_explicitly(self, monkeypatch):
        """「保有 0 件」は正常。認証失敗と混同しないこと。"""
        _patch_alpaca(monkeypatch, _FakeAlpacaClient(positions=[]))
        result = hc.check_alpaca_connectivity(_ctx())
        assert result.level == hc.OK
        assert "0 件" in result.detail

    def test_position_fetch_error_is_fail_not_zero_positions(self, monkeypatch):
        """
        get_positions() の例外握り潰しに引きずられないこと。

        ここを OK（0 件）と誤報すると C-6 の突合まで無意味になる。
        """
        _patch_alpaca(monkeypatch, _FakeAlpacaClient(
            positions_raise=RuntimeError("connection reset")))
        ctx = _ctx()
        result = hc.check_alpaca_connectivity(ctx)
        assert result.level == hc.FAIL
        assert ctx.alpaca_symbols is None

    def test_symbols_are_shared_with_context(self, monkeypatch):
        _patch_alpaca(monkeypatch, _FakeAlpacaClient(
            positions=[_FakePosition("AAPL"), _FakePosition("hsy")]))
        ctx = _ctx()
        hc.check_alpaca_connectivity(ctx)
        assert ctx.alpaca_symbols == {"AAPL", "HSY"}

    def test_does_not_touch_order_methods(self, monkeypatch):
        """発注系メソッドを絶対に呼ばないこと。"""
        client = _FakeAlpacaClient(positions=[])

        def _forbidden(*_a, **_kw):
            raise AssertionError("発注系メソッドが呼ばれました")

        client.place_buy = _forbidden
        client.place_sell = _forbidden
        _patch_alpaca(monkeypatch, client)
        assert hc.check_alpaca_connectivity(_ctx()).level == hc.OK


# ────────────────────────────────────────────────────────────
# B-4  LLM 疎通
# ────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, content="OK"):
        self._content = content
        self.calls = 0

    def invoke(self, _prompt):
        self.calls += 1
        return _FakeResponse(self._content)


class TestCheckLLMConnectivity:
    def test_actually_invokes_the_model(self, monkeypatch):
        """設定確認ではなく実際に応答を取ること（廃止モデル名の無言死対策）。"""
        llm = _FakeLLM("OK")
        monkeypatch.setattr("skills.llm_factory.get_llm", lambda **kw: (llm, "gemini"))
        result = hc.check_llm_connectivity(_ctx())
        assert llm.calls == 1
        assert result.level == hc.OK

    def test_empty_response_is_fail(self, monkeypatch):
        monkeypatch.setattr("skills.llm_factory.get_llm",
                            lambda **kw: (_FakeLLM("   "), "ollama"))
        assert hc.check_llm_connectivity(_ctx()).level == hc.FAIL

    def test_exception_is_surfaced_as_fail(self, monkeypatch):
        def _boom(**_kw):
            raise RuntimeError("404 model not found")
        monkeypatch.setattr("skills.llm_factory.get_llm", _boom)
        result = hc._safe("llm_connectivity", "LLM 疎通", hc.check_llm_connectivity, _ctx())
        assert result.level == hc.FAIL
        assert "RuntimeError" in result.detail


# ────────────────────────────────────────────────────────────
# C. データ整合性
# ────────────────────────────────────────────────────────────

class TestPortfolioVsAlpaca:
    def test_matching_sets_are_ok(self, isolate_state_files):
        _write_portfolio(isolate_state_files, ["AAPL", "HSY"])
        assert hc.check_portfolio_vs_alpaca(_ctx(alpaca_symbols={"AAPL", "HSY"})).level == hc.OK

    def test_mismatch_is_warn_and_lists_both_sides(self, isolate_state_files):
        _write_portfolio(isolate_state_files, ["AAPL"])
        result = hc.check_portfolio_vs_alpaca(_ctx(alpaca_symbols={"RTX"}))
        assert result.level == hc.WARN
        assert "AAPL" in result.detail and "RTX" in result.detail

    def test_skipped_when_alpaca_unavailable(self, isolate_state_files):
        _write_portfolio(isolate_state_files, ["AAPL"])
        result = hc.check_portfolio_vs_alpaca(_ctx(alpaca_symbols=None))
        assert result.level == hc.WARN
        assert "未検証" in result.detail

    def test_does_not_modify_portfolio(self, isolate_state_files):
        _write_portfolio(isolate_state_files, ["AAPL"])
        before = Path(hc.PORTFOLIO_PATH).read_bytes()
        hc.check_portfolio_vs_alpaca(_ctx(alpaca_symbols={"RTX"}))
        assert Path(hc.PORTFOLIO_PATH).read_bytes() == before


class TestTrainingDataFreshness:
    def test_recent_record_is_ok(self, isolate_state_files):
        _write_training_record(date.today())
        assert hc.check_training_data_freshness(_ctx()).level == hc.OK

    def test_stale_record_is_warn(self, isolate_state_files):
        _write_training_record(_business_days_ago(hc.TRAINING_DATA_MAX_AGE_BUSINESS_DAYS + 1))
        result = hc.check_training_data_freshness(_ctx())
        assert result.level == hc.WARN
        assert "営業日" in result.detail

    def test_missing_file_is_fail(self, isolate_state_files):
        assert hc.check_training_data_freshness(_ctx()).level == hc.FAIL

    def test_reads_last_record_not_first(self, isolate_state_files):
        old = json.dumps({"date": "2020-01-01"})
        new = json.dumps({"date": date.today().isoformat()})
        Path(hc.TRAINING_DATA_PATH).write_text(f"{old}\n{new}\n", encoding="utf-8")
        assert hc.check_training_data_freshness(_ctx()).level == hc.OK


class TestPositionsIndexStaleness:
    def test_stale_entries_are_warn_with_counts(self, isolate_state_files):
        """5 月から滞留している index エントリを検知する。"""
        _write_portfolio(isolate_state_files, [])
        Path(hc.POSITIONS_INDEX_PATH).write_text(json.dumps({
            "HSY":  [{"record_id": "a"}],
            "AAPL": [{"record_id": "b"}, {"record_id": "c"}],
            "RTX":  [{"record_id": "d"}],
        }), encoding="utf-8")

        result = hc.check_positions_index_staleness(_ctx())
        assert result.level == hc.WARN
        assert "4 件" in result.detail
        assert "AAPL×2" in result.detail

    def test_entries_matching_open_positions_are_ok(self, isolate_state_files):
        _write_portfolio(isolate_state_files, ["HSY"])
        Path(hc.POSITIONS_INDEX_PATH).write_text(
            json.dumps({"HSY": [{"record_id": "a"}]}), encoding="utf-8")
        assert hc.check_positions_index_staleness(_ctx()).level == hc.OK

    def test_missing_index_is_ok(self, isolate_state_files):
        assert hc.check_positions_index_staleness(_ctx()).level == hc.OK

    def test_does_not_modify_index(self, isolate_state_files):
        _write_portfolio(isolate_state_files, [])
        Path(hc.POSITIONS_INDEX_PATH).write_text(
            json.dumps({"HSY": [{"record_id": "a"}]}), encoding="utf-8")
        before = Path(hc.POSITIONS_INDEX_PATH).read_bytes()
        hc.check_positions_index_staleness(_ctx())
        assert Path(hc.POSITIONS_INDEX_PATH).read_bytes() == before


# ────────────────────────────────────────────────────────────
# D-9  自己申告
# ────────────────────────────────────────────────────────────

class TestSelfReport:
    def test_first_run_is_ok(self, isolate_state_files):
        assert hc.check_self_report(_ctx(), previous={}).level == hc.OK

    def test_recent_run_is_ok(self, isolate_state_files):
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        assert hc.check_self_report(_ctx(), {"last_run_at": stamp}).level == hc.OK

    def test_long_gap_is_warn(self, isolate_state_files):
        """health_check 自体が止まっていたことを次回実行時に検知する。"""
        stale = _business_days_ago(hc.HEALTH_RUN_MAX_GAP_BUSINESS_DAYS + 2)
        stamp = datetime.combine(stale, datetime.min.time()).astimezone().isoformat()
        result = hc.check_self_report(_ctx(), {"last_run_at": stamp})
        assert result.level == hc.WARN
        assert "実行されていなかった" in result.detail

    def test_corrupt_last_run_file_does_not_raise(self, isolate_state_files):
        Path(hc.LAST_RUN_PATH).write_text("{ broken json", encoding="utf-8")
        assert hc.read_last_run() == {}

    def test_write_last_run_records_warn_keys(self, isolate_state_files):
        report = hc.HealthReport([
            hc.CheckResult("a", "A", hc.WARN, "d"),
            hc.CheckResult("b", "B", hc.FAIL, "d"),
        ])
        hc.write_last_run(report)
        saved = json.loads(Path(hc.LAST_RUN_PATH).read_text(encoding="utf-8"))
        assert saved["warn_keys"] == ["a"]
        assert saved["fail_keys"] == ["b"]
        assert saved["overall"] == hc.FAIL


# ────────────────────────────────────────────────────────────
# 通知ポリシー
# ────────────────────────────────────────────────────────────

def _report(*results: hc.CheckResult) -> hc.HealthReport:
    return hc.HealthReport(list(results))


class TestNotificationPolicy:
    def test_fail_always_notifies(self):
        report = _report(hc.CheckResult("k", "ラベル", hc.FAIL, "詳細"))
        with patch("engine.notify.send_line_message") as mock_send:
            notified, _ = hc.notify_if_needed(report, previous={})
        assert notified
        mock_send.assert_called_once()

    def test_all_ok_does_not_notify(self):
        report = _report(hc.CheckResult("k", "ラベル", hc.OK, "詳細"))
        with patch("engine.notify.send_line_message") as mock_send:
            notified, _ = hc.notify_if_needed(report, previous={})
        assert not notified
        mock_send.assert_not_called()

    def test_new_warn_notifies_once(self):
        report = _report(hc.CheckResult("new_warn", "新警告", hc.WARN, "詳細"))
        with patch("engine.notify.send_line_message") as mock_send:
            notified, new = hc.notify_if_needed(report, previous={"warn_keys": []})
        assert notified and [r.key for r in new] == ["new_warn"]
        mock_send.assert_called_once()

    def test_known_warn_does_not_renotify(self):
        """既知の WARN が毎平日飛んで通知が読まれなくなるのを防ぐ。"""
        report = _report(hc.CheckResult("known", "既知警告", hc.WARN, "詳細"))
        with patch("engine.notify.send_line_message") as mock_send:
            notified, _ = hc.notify_if_needed(report, previous={"warn_keys": ["known"]})
        assert not notified
        mock_send.assert_not_called()

    def test_notify_warn_flag_forces_resend(self):
        report = _report(hc.CheckResult("known", "既知警告", hc.WARN, "詳細"))
        with patch("engine.notify.send_line_message") as mock_send:
            notified, _ = hc.notify_if_needed(report, {"warn_keys": ["known"]},
                                              notify_warn=True)
        assert notified
        mock_send.assert_called_once()

    def test_no_notify_flag_suppresses_send(self):
        report = _report(hc.CheckResult("k", "ラベル", hc.FAIL, "詳細"))
        with patch("engine.notify.send_line_message") as mock_send:
            notified, _ = hc.notify_if_needed(report, previous={}, no_notify=True)
        assert not notified
        mock_send.assert_not_called()

    def test_known_warn_still_sent_alongside_a_fail(self):
        """FAIL 通知には現状の全体像が必要なので FAIL は常に本文へ載る。"""
        report = _report(
            hc.CheckResult("f", "失敗項目", hc.FAIL, "詳細"),
            hc.CheckResult("known", "既知警告", hc.WARN, "詳細"),
        )
        with patch("engine.notify.send_line_message") as mock_send:
            hc.notify_if_needed(report, previous={"warn_keys": ["known"]})
        assert "失敗項目" in mock_send.call_args.args[0]


class TestNotificationBody:
    def test_secret_values_are_redacted(self, monkeypatch):
        secret = "line-token-abcdefghijklmnop"
        monkeypatch.setenv("LINE_ACCESS_TOKEN", secret)
        report = _report(hc.CheckResult("k", "ラベル", hc.FAIL, f"エラー: {secret} は無効"))
        body = hc.build_notification(report, new_warnings=[])
        assert secret not in body
        assert "***" in body

    def test_body_contains_label_and_detail(self):
        report = _report(hc.CheckResult("k", "cron 登録スクリプトの実在", hc.FAIL,
                                        "1 件のパスが存在しません"))
        body = hc.build_notification(report, new_warnings=[])
        assert "cron 登録スクリプトの実在" in body
        assert "1 件のパスが存在しません" in body

    def test_body_mentions_no_auto_repair(self):
        report = _report(hc.CheckResult("k", "ラベル", hc.FAIL, "詳細"))
        assert "自動修復は行いません" in hc.build_notification(report, [])


class TestRedact:
    def test_short_values_are_not_replaced(self, monkeypatch):
        """短い値まで置換すると無関係な文字列を壊すため対象外にする。"""
        monkeypatch.setenv("LINE_USER_ID", "abc")
        assert hc._redact("abcdef") == "abcdef"

    def test_long_text_is_truncated(self):
        assert len(hc._redact("x" * 1000)) <= hc._DETAIL_MAX_LEN + 1

    def test_newlines_are_collapsed(self):
        assert hc._redact("a\nb\n c") == "a b c"


# ────────────────────────────────────────────────────────────
# ログ出力・終了コード・全体実行
# ────────────────────────────────────────────────────────────

class TestAppendLog:
    def test_each_result_gets_a_greppable_line(self, isolate_state_files):
        report = _report(
            hc.CheckResult("cron_scripts", "cron 登録スクリプトの実在", hc.FAIL, "無い"),
            hc.CheckResult("env_keys", ".env 必須キー", hc.OK, "問題なし"),
        )
        hc.append_log(report, notified=True)
        lines = Path(hc.HEALTH_LOG_PATH).read_text(encoding="utf-8").splitlines()

        assert len(lines) == 3  # 2 項目 + SUMMARY
        assert sum(" | FAIL | " in ln for ln in lines) == 2  # 項目行 + SUMMARY の overall
        assert any("cron_scripts" in ln and "無い" in ln for ln in lines)
        assert any("notified=yes" in ln for ln in lines)

    def test_appends_instead_of_overwriting(self, isolate_state_files):
        report = _report(hc.CheckResult("k", "ラベル", hc.OK, "d"))
        hc.append_log(report, notified=False)
        hc.append_log(report, notified=False)
        assert len(Path(hc.HEALTH_LOG_PATH).read_text(encoding="utf-8").splitlines()) == 4

    def test_secrets_are_redacted_in_log(self, isolate_state_files, monkeypatch):
        secret = "apca-secret-abcdefghijklmnop"
        monkeypatch.setenv("APCA_API_SECRET_KEY", secret)
        hc.append_log(_report(hc.CheckResult("k", "L", hc.FAIL, f"err {secret}")), False)
        assert secret not in Path(hc.HEALTH_LOG_PATH).read_text(encoding="utf-8")


class TestMainExitCode:
    def _run(self, monkeypatch, results: list[hc.CheckResult], argv: list[str]):
        monkeypatch.setattr(hc, "run_health_check",
                            lambda **kw: (hc.HealthReport(results), {}))
        return hc.main(argv)

    def test_all_ok_exits_zero(self, monkeypatch, isolate_state_files):
        assert self._run(monkeypatch, [hc.CheckResult("k", "L", hc.OK, "d")],
                         ["--no-notify", "--quiet"]) == 0

    def test_warn_only_exits_zero(self, monkeypatch, isolate_state_files):
        assert self._run(monkeypatch, [hc.CheckResult("k", "L", hc.WARN, "d")],
                         ["--no-notify", "--quiet"]) == 0

    def test_fail_exits_one(self, monkeypatch, isolate_state_files):
        assert self._run(monkeypatch, [hc.CheckResult("k", "L", hc.FAIL, "d")],
                         ["--no-notify", "--quiet"]) == 1

    def test_inject_fail_produces_failure(self, monkeypatch, isolate_state_files):
        """LINE 配達経路の検証用フラグが実際に FAIL を作ること。"""
        monkeypatch.setattr(hc, "read_crontab", lambda: (None, "テスト"))
        report, _ = hc.run_health_check(skip_network=True, inject_fail=True)
        assert any(r.key == "inject_fail" and r.level == hc.FAIL for r in report.results)


class TestRunHealthCheckIsolation:
    def test_independent_checks_survive_one_exception(self, monkeypatch, isolate_state_files):
        """1 項目が例外を投げても他のチェックが継続すること（設計原則 4）。"""
        def _boom(_ctx):
            raise RuntimeError("意図的な例外")
        monkeypatch.setattr(hc, "check_env_keys", _boom)
        monkeypatch.setattr(hc, "read_crontab", lambda: (None, "テスト"))

        report, _ = hc.run_health_check(skip_network=True)
        keys = {r.key for r in report.results}
        assert "env_keys" in keys and "positions_index_staleness" in keys
        env_result = next(r for r in report.results if r.key == "env_keys")
        assert env_result.level == hc.FAIL and "RuntimeError" in env_result.detail

    def test_skip_network_omits_connectivity_checks(self, monkeypatch, isolate_state_files):
        monkeypatch.setattr(hc, "read_crontab", lambda: (None, "テスト"))
        report, _ = hc.run_health_check(skip_network=True)
        keys = {r.key for r in report.results}
        assert "llm_connectivity" not in keys and "alpaca_connectivity" not in keys

    def test_state_paths_point_into_tmp(self, isolate_state_files):
        """本番 data/ ・ logs/ を触らないことを構造的に保証する。"""
        for path in (hc.HEALTH_LOG_PATH, hc.LAST_RUN_PATH, hc.PORTFOLIO_PATH,
                     hc.POSITIONS_INDEX_PATH, hc.TRAINING_DATA_PATH):
            assert Path(path).is_relative_to(isolate_state_files)

    def test_full_run_writes_only_last_run_file(self, monkeypatch, isolate_state_files):
        """data/ 配下への書き込みが health_last_run.json だけであること。"""
        monkeypatch.setattr(hc, "read_crontab", lambda: (None, "テスト"))
        data_dir = isolate_state_files / "data"
        before = {p for p in data_dir.rglob("*") if p.is_file()}

        report, _ = hc.run_health_check(skip_network=True)
        hc.write_last_run(report)

        created = {p for p in data_dir.rglob("*") if p.is_file()} - before
        assert created == {Path(hc.LAST_RUN_PATH)}
