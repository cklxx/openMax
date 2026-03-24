"""Tests for provider_usage module — probing local coding-agent installations."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openmax.provider_usage import (
    ModelUsage,
    ProviderStatus,
    QuotaInfo,
    QuotaWindow,
    WindowUsage,
    _claude_fetch_quota,
    _claude_window_usage,
    _codex_fetch_quota,
    _epoch_to_iso,
    _populate_claude_local_stats,
    _probe_claude_code,
    _probe_codex,
    probe_all,
)

# ── Dataclass basics ──────────────────────────────────────────────


class TestModelUsage:
    def test_total_tokens(self):
        mu = ModelUsage(model="claude-3", input_tokens=100, output_tokens=50)
        assert mu.total_tokens == 150

    def test_defaults(self):
        mu = ModelUsage(model="x")
        assert mu.input_tokens == 0
        assert mu.output_tokens == 0
        assert mu.cache_read_tokens == 0
        assert mu.cache_creation_tokens == 0
        assert mu.total_tokens == 0


class TestWindowUsage:
    def test_total_tokens(self):
        wu = WindowUsage(input_tokens=300, output_tokens=200)
        assert wu.total_tokens == 500

    def test_defaults(self):
        wu = WindowUsage()
        assert wu.window_hours == 5
        assert wu.messages == 0
        assert wu.total_tokens == 0
        assert wu.models == {}


class TestQuotaWindow:
    def test_fields(self):
        qw = QuotaWindow(
            name="5-hour session", used_pct=42.5, resets_at="2026-01-01T00:00:00+00:00"
        )
        assert qw.name == "5-hour session"
        assert qw.used_pct == 42.5
        assert qw.reset_seconds is None


class TestQuotaInfo:
    def test_defaults(self):
        qi = QuotaInfo()
        assert qi.windows == []
        assert qi.plan is None
        assert qi.extra_usage_enabled is False
        assert qi.error is None

    def test_with_windows(self):
        qi = QuotaInfo(
            windows=[QuotaWindow(name="5h", used_pct=10.0)],
            plan="Pro",
        )
        assert len(qi.windows) == 1
        assert qi.plan == "Pro"


class TestProviderStatus:
    def test_defaults(self):
        ps = ProviderStatus(provider="test")
        assert ps.installed is False
        assert ps.total_sessions == 0
        assert ps.model_usage == []
        assert ps.daily_activity == []
        assert ps.raw == {}


# ── _epoch_to_iso ─────────────────────────────────────────────────


class TestEpochToIso:
    def test_valid_epoch(self):
        result = _epoch_to_iso(0)
        assert result == "1970-01-01T00:00:00+00:00"

    def test_float_epoch(self):
        result = _epoch_to_iso(1700000000.0)
        assert "2023" in result

    def test_overflow_returns_str(self):
        result = _epoch_to_iso(99999999999999)
        assert result == "99999999999999"


# ── _populate_claude_local_stats ──────────────────────────────────


class TestPopulateClaudeLocalStats:
    def test_basic_population(self):
        status = ProviderStatus(provider="claude-code")
        data = {
            "totalSessions": 42,
            "totalMessages": 300,
            "firstSessionDate": "2025-01-15",
            "lastComputedDate": "2026-03-19",
            "modelUsage": {
                "claude-3-opus": {
                    "inputTokens": 1000,
                    "outputTokens": 500,
                    "cacheReadInputTokens": 200,
                    "cacheCreationInputTokens": 100,
                },
                "claude-3-sonnet": {
                    "inputTokens": 2000,
                    "outputTokens": 1000,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                },
            },
            "dailyActivity": [
                {"date": "2026-03-18", "messageCount": 10, "sessionCount": 2, "toolCallCount": 5},
                {"date": "2026-03-19", "messageCount": 20, "sessionCount": 3, "toolCallCount": 15},
            ],
            "dailyModelTokens": [
                {"date": "2026-03-18", "tokensByModel": {"opus": 500}},
            ],
        }
        _populate_claude_local_stats(status, data)

        assert status.total_sessions == 42
        assert status.total_messages == 300
        assert status.first_used == "2025-01-15"
        assert len(status.model_usage) == 2
        assert status.total_tokens == 1000 + 500 + 2000 + 1000
        assert status.model == "claude-3-sonnet"  # highest total
        assert len(status.daily_activity) == 2
        assert status.daily_activity[0].messages == 10
        assert status.daily_activity[0].tokens_by_model == {"opus": 500}
        assert status.daily_activity[1].tokens_by_model == {}
        assert status.raw["last_computed"] == "2026-03-19"

    def test_empty_data(self):
        status = ProviderStatus(provider="claude-code")
        _populate_claude_local_stats(status, {})
        assert status.total_sessions == 0
        assert status.total_messages == 0
        assert status.model_usage == []
        assert status.daily_activity == []
        assert status.model is None

    def test_model_picks_highest_total(self):
        status = ProviderStatus(provider="claude-code")
        data = {
            "modelUsage": {
                "small": {"inputTokens": 10, "outputTokens": 5},
                "big": {"inputTokens": 9999, "outputTokens": 1},
            }
        }
        _populate_claude_local_stats(status, data)
        assert status.model == "big"


# ── _claude_window_usage ──────────────────────────────────────────


class TestClaudeWindowUsage:
    def test_scans_recent_jsonl(self, tmp_path, monkeypatch):
        project_dir = tmp_path / ".claude" / "projects" / "myproj"
        project_dir.mkdir(parents=True)

        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(hours=1)).isoformat()
        old_ts = (now - timedelta(hours=10)).isoformat()

        entries = [
            # recent assistant message with usage
            {
                "type": "assistant",
                "timestamp": recent_ts,
                "message": {
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_read_input_tokens": 10,
                        "cache_creation_input_tokens": 5,
                    },
                    "model": "claude-3-opus",
                },
            },
            # old message — should be excluded
            {
                "type": "assistant",
                "timestamp": old_ts,
                "message": {
                    "usage": {"input_tokens": 9999, "output_tokens": 9999},
                    "model": "old-model",
                },
            },
            # non-assistant — should be excluded
            {"type": "user", "timestamp": recent_ts, "message": {"content": "hello"}},
            # assistant without usage — should be excluded
            {
                "type": "assistant",
                "timestamp": recent_ts,
                "message": {"content": "hi"},
            },
            # another recent assistant message
            {
                "type": "assistant",
                "timestamp": recent_ts,
                "message": {
                    "usage": {"input_tokens": 200, "output_tokens": 100},
                    "model": "claude-3-opus",
                },
            },
        ]

        jsonl_file = project_dir / "session.jsonl"
        jsonl_file.write_text(
            "\n".join(json.dumps(e) for e in entries),
            encoding="utf-8",
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        window = _claude_window_usage(5)
        assert window.window_hours == 5
        assert window.messages == 2
        assert window.input_tokens == 300
        assert window.output_tokens == 150
        assert window.cache_read_tokens == 10
        assert window.cache_creation_tokens == 5
        assert window.models == {"claude-3-opus": 2}

    def test_empty_directory(self, tmp_path, monkeypatch):
        (tmp_path / ".claude" / "projects").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        window = _claude_window_usage(5)
        assert window.messages == 0
        assert window.total_tokens == 0

    def test_invalid_json_lines_skipped(self, tmp_path, monkeypatch):
        project_dir = tmp_path / ".claude" / "projects" / "p"
        project_dir.mkdir(parents=True)

        now_ts = datetime.now(timezone.utc).isoformat()
        lines = [
            "not valid json",
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": now_ts,
                    "message": {
                        "usage": {"input_tokens": 50, "output_tokens": 25},
                        "model": "m",
                    },
                }
            ),
        ]
        (project_dir / "s.jsonl").write_text("\n".join(lines), encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        window = _claude_window_usage(5)
        assert window.messages == 1
        assert window.input_tokens == 50

    def test_invalid_timestamp_skipped(self, tmp_path, monkeypatch):
        project_dir = tmp_path / ".claude" / "projects" / "p"
        project_dir.mkdir(parents=True)

        entry = {
            "type": "assistant",
            "timestamp": "not-a-date",
            "message": {
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "model": "m",
            },
        }
        (project_dir / "s.jsonl").write_text(json.dumps(entry), encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        window = _claude_window_usage(5)
        assert window.messages == 0  # skipped due to bad timestamp

    def test_message_not_dict_skipped(self, tmp_path, monkeypatch):
        project_dir = tmp_path / ".claude" / "projects" / "p"
        project_dir.mkdir(parents=True)

        entry = {"type": "assistant", "message": "just a string"}
        (project_dir / "s.jsonl").write_text(json.dumps(entry), encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        window = _claude_window_usage(5)
        assert window.messages == 0


# ── _claude_fetch_quota ───────────────────────────────────────────


class TestClaudeFetchQuota:
    def test_no_credentials_returns_error(self, monkeypatch):
        monkeypatch.setattr("openmax.provider_usage._claude_read_oauth", lambda: None)
        quota = _claude_fetch_quota()
        assert quota.error == "no credentials"

    def test_parses_usage_response(self, monkeypatch):
        oauth = {
            "accessToken": "tok-123",
            "subscriptionType": "max_5x",
            "rateLimitTier": "default_claude_max_5x",
            "expiresAt": int(time.time() * 1000) + 3600_000,  # future
        }
        monkeypatch.setattr("openmax.provider_usage._claude_read_oauth", lambda: oauth)

        api_response = json.dumps(
            {
                "five_hour": {"utilization": 42.0, "resets_at": "2026-03-19T12:00:00Z"},
                "seven_day": {"utilization": 15.0},
                "seven_day_sonnet": None,
                "extra_usage": {
                    "is_enabled": True,
                    "used_credits": 2.50,
                    "monthly_limit": 100,
                },
            }
        ).encode()

        class FakeResp:
            def read(self):
                return api_response

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=10: FakeResp(),
        )

        quota = _claude_fetch_quota()
        assert quota.error is None
        assert quota.plan == "Max 5x"
        assert quota.rate_tier == "Max 5x"
        assert len(quota.windows) == 2
        assert quota.windows[0].name == "5-hour session"
        assert quota.windows[0].used_pct == 42.0
        assert quota.windows[1].name == "7-day weekly"
        assert quota.extra_usage_enabled is True
        assert quota.extra_usage_used == 2.50
        assert quota.extra_usage_limit == 100

    def test_expired_token_triggers_refresh(self, monkeypatch):
        oauth = {
            "accessToken": "old-tok",
            "refreshToken": "ref-tok",
            "subscriptionType": "pro",
            "rateLimitTier": "",
            "expiresAt": 1000,  # long expired
        }
        monkeypatch.setattr("openmax.provider_usage._claude_read_oauth", lambda: oauth)

        refresh_called = []

        def fake_refresh(token):
            refresh_called.append(token)
            return {"accessToken": "new-tok"}

        monkeypatch.setattr("openmax.provider_usage._claude_refresh_token", fake_refresh)
        monkeypatch.setattr("openmax.provider_usage._claude_save_refreshed_token", lambda *a: None)

        captured_headers = {}

        class FakeResp:
            def read(self):
                return b"{}"

        def fake_urlopen(req, timeout=10):
            captured_headers["auth"] = req.get_header("Authorization")
            return FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        quota = _claude_fetch_quota()
        assert refresh_called == ["ref-tok"]
        assert captured_headers["auth"] == "Bearer new-tok"
        assert quota.plan == "Pro"

    def test_expired_token_refresh_fails_returns_error(self, monkeypatch):
        oauth = {
            "accessToken": "old-tok",
            "refreshToken": "ref-tok",
            "subscriptionType": "pro",
            "rateLimitTier": "",
            "expiresAt": 1000,  # long expired
        }
        monkeypatch.setattr("openmax.provider_usage._claude_read_oauth", lambda: oauth)
        monkeypatch.setattr("openmax.provider_usage._claude_refresh_token", lambda token: None)

        quota = _claude_fetch_quota()
        assert "expired" in quota.error
        assert "re-authenticate" in quota.error

    def test_api_error_returns_error_quota(self, monkeypatch):
        oauth = {
            "accessToken": "tok",
            "subscriptionType": "",
            "rateLimitTier": "",
            "expiresAt": int(time.time() * 1000) + 3600_000,
        }
        monkeypatch.setattr("openmax.provider_usage._claude_read_oauth", lambda: oauth)
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=10: (_ for _ in ()).throw(ConnectionError("offline")),
        )

        quota = _claude_fetch_quota()
        assert "offline" in quota.error


# ── _claude_read_oauth ────────────────────────────────────────────


class TestClaudeReadOauth:
    def test_reads_from_keychain(self, monkeypatch):
        from openmax.provider_usage import _claude_read_oauth

        cred_json = json.dumps({"claudeAiOauth": {"accessToken": "abc"}})

        class FakeResult:
            returncode = 0
            stdout = cred_json

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeResult())

        result = _claude_read_oauth()
        assert result == {"accessToken": "abc"}

    def test_falls_back_to_credentials_file(self, tmp_path, monkeypatch):
        from openmax.provider_usage import _claude_read_oauth

        # Make keychain fail
        class FailResult:
            returncode = 1
            stdout = ""

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FailResult())
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        cred_dir = tmp_path / ".claude"
        cred_dir.mkdir()
        (cred_dir / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "file-tok"}}),
            encoding="utf-8",
        )

        result = _claude_read_oauth()
        assert result == {"accessToken": "file-tok"}

    def test_returns_none_when_nothing_available(self, tmp_path, monkeypatch):
        from openmax.provider_usage import _claude_read_oauth

        class FailResult:
            returncode = 1
            stdout = ""

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FailResult())
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = _claude_read_oauth()
        assert result is None


# ── _claude_refresh_token ─────────────────────────────────────────


class TestClaudeRefreshToken:
    def test_empty_token_returns_none(self):
        from openmax.provider_usage import _claude_refresh_token

        assert _claude_refresh_token("") is None

    def test_success(self, monkeypatch):
        from openmax.provider_usage import _claude_refresh_token

        class FakeResp:
            def read(self):
                return json.dumps({"accessToken": "refreshed"}).encode()

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=10: FakeResp())

        result = _claude_refresh_token("my-refresh-token")
        assert result == {"accessToken": "refreshed"}

    def test_failure_returns_none(self, monkeypatch):
        from openmax.provider_usage import _claude_refresh_token

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=10: (_ for _ in ()).throw(ConnectionError("fail")),
        )

        result = _claude_refresh_token("tok")
        assert result is None


# ── _claude_save_refreshed_token / _claude_write_oauth ────────────


class TestClaudeSaveRefreshedToken:
    def test_saves_camel_case_keys(self, monkeypatch):
        from openmax.provider_usage import _claude_save_refreshed_token

        written = {}
        monkeypatch.setattr(
            "openmax.provider_usage._claude_write_oauth",
            lambda oauth: written.update(oauth),
        )

        original = {"accessToken": "old", "refreshToken": "old-ref", "expiresAt": 1000}
        refreshed = {"accessToken": "new", "refreshToken": "new-ref", "expiresAt": 9999}
        _claude_save_refreshed_token(original, refreshed)

        assert written["accessToken"] == "new"
        assert written["refreshToken"] == "new-ref"
        assert written["expiresAt"] == 9999

    def test_handles_snake_case_keys(self, monkeypatch):
        from openmax.provider_usage import _claude_save_refreshed_token

        written = {}
        monkeypatch.setattr(
            "openmax.provider_usage._claude_write_oauth",
            lambda oauth: written.update(oauth),
        )

        original = {"accessToken": "old", "refreshToken": "old-ref"}
        refreshed = {"access_token": "new-snake", "expires_in": 3600}
        _claude_save_refreshed_token(original, refreshed)

        assert written["accessToken"] == "new-snake"
        assert written["refreshToken"] == "old-ref"  # kept from original
        assert written["expiresAt"] > int(time.time() * 1000)

    def test_no_access_token_skips(self, monkeypatch):
        from openmax.provider_usage import _claude_save_refreshed_token

        called = []
        monkeypatch.setattr(
            "openmax.provider_usage._claude_write_oauth",
            lambda oauth: called.append(True),
        )

        _claude_save_refreshed_token({"accessToken": "old"}, {"something": "else"})
        assert called == []


class TestComputeExpiry:
    def test_none_returns_none(self):
        from openmax.provider_usage import _compute_expiry

        assert _compute_expiry(None) is None

    def test_converts_seconds_to_ms(self):
        from openmax.provider_usage import _compute_expiry

        result = _compute_expiry(3600)
        expected_min = int(time.time() * 1000) + 3600 * 1000 - 1000
        assert result > expected_min


# ── _probe_claude_code ────────────────────────────────────────────


class TestProbeClaude:
    def test_not_installed(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        status = _probe_claude_code()
        assert status.provider == "claude-code"
        assert status.installed is False

    def test_installed_with_local_stats(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create stats-cache.json
        stats_dir = tmp_path / ".claude"
        stats_dir.mkdir()
        (stats_dir / "stats-cache.json").write_text(
            json.dumps(
                {
                    "totalSessions": 5,
                    "totalMessages": 50,
                    "modelUsage": {"opus": {"inputTokens": 100, "outputTokens": 50}},
                }
            ),
            encoding="utf-8",
        )

        # Mock version check
        class FakeVersionResult:
            returncode = 0
            stdout = "claude 2.1.76\n"

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeVersionResult())

        # Mock quota fetch
        monkeypatch.setattr(
            "openmax.provider_usage._claude_fetch_quota",
            lambda: QuotaInfo(plan="Pro"),
        )

        status = _probe_claude_code()
        assert status.installed is True
        assert status.total_sessions == 5
        assert status.version == "claude 2.1.76"
        assert status.plan == "Pro"
        assert status.window_usage is not None


# ── _probe_codex ──────────────────────────────────────────────────


class TestProbeCodex:
    def test_not_installed(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        status = _probe_codex()
        assert status.provider == "codex"
        assert status.installed is False

    def test_installed_with_sqlite_and_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/codex")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()

        # Config
        (codex_dir / "config.toml").write_text(
            'model = "o3-mini"\napproval_mode = "auto-edit"\n',
            encoding="utf-8",
        )

        # Version
        (codex_dir / "version.json").write_text(
            json.dumps({"latest_version": "0.1.2"}),
            encoding="utf-8",
        )

        # SQLite
        db_path = codex_dir / "state_5.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE threads (id TEXT, tokens_used INTEGER, created_at INTEGER)")
        now_epoch = int(time.time())
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?)",
            ("t1", 1000, now_epoch - 86400),
        )
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?)",
            ("t2", 2000, now_epoch),
        )
        conn.commit()
        conn.close()

        # History
        (codex_dir / "history.jsonl").write_text("line1\nline2\nline3\n", encoding="utf-8")

        # Mock quota
        monkeypatch.setattr(
            "openmax.provider_usage._codex_fetch_quota",
            lambda d: QuotaInfo(plan="Plus"),
        )

        status = _probe_codex()
        assert status.installed is True
        assert status.model == "o3-mini"
        assert status.version == "0.1.2"
        assert status.total_sessions == 2
        assert status.total_tokens == 3000
        assert status.first_used is not None
        assert len(status.daily_activity) >= 1
        assert status.plan == "Plus"
        assert status.raw["codex_dir"] == str(codex_dir)

    def test_config_model_skips_array(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/codex")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text(
            'model = ["o3-mini", "gpt-4"]\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "openmax.provider_usage._codex_fetch_quota",
            lambda d: QuotaInfo(),
        )

        status = _probe_codex()
        assert status.model is None  # array value skipped

    def test_history_counts_messages(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/codex")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "history.jsonl").write_text("a\nb\nc\n", encoding="utf-8")
        monkeypatch.setattr(
            "openmax.provider_usage._codex_fetch_quota",
            lambda d: QuotaInfo(),
        )

        status = _probe_codex()
        assert status.total_messages == 3


# ── _codex_fetch_quota ────────────────────────────────────────────


class TestCodexFetchQuota:
    def test_no_auth_file(self, tmp_path):
        quota = _codex_fetch_quota(tmp_path)
        assert quota.error == "no auth.json"

    def test_invalid_auth_json(self, tmp_path):
        (tmp_path / "auth.json").write_text("not json", encoding="utf-8")
        quota = _codex_fetch_quota(tmp_path)
        assert quota.error == "cannot read auth.json"

    def test_no_access_token(self, tmp_path):
        (tmp_path / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": ""}}),
            encoding="utf-8",
        )
        quota = _codex_fetch_quota(tmp_path)
        assert quota.error == "no access token"

    def test_tokens_as_json_string(self, tmp_path, monkeypatch):
        tokens_str = json.dumps({"access_token": "tok-abc", "account_id": "acc-1"})
        (tmp_path / "auth.json").write_text(
            json.dumps({"tokens": tokens_str}),
            encoding="utf-8",
        )

        api_response = json.dumps(
            {
                "plan_type": "plus",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 30.0,
                        "reset_after_seconds": 3600,
                        "reset_at": int(time.time()) + 3600,
                    },
                    "secondary_window": {
                        "used_percent": 10.0,
                    },
                },
                "credits": {
                    "balance": 50.0,
                    "has_credits": True,
                },
            }
        ).encode()

        class FakeResp:
            def read(self):
                return api_response

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=10: FakeResp())

        quota = _codex_fetch_quota(tmp_path)
        assert quota.plan == "plus"
        assert len(quota.windows) == 2
        assert quota.windows[0].name == "5-hour session"
        assert quota.windows[0].used_pct == 30.0
        assert quota.windows[0].reset_seconds == 3600
        assert quota.windows[1].name == "7-day weekly"
        assert quota.extra_usage_enabled is True
        assert quota.extra_usage_limit == 50.0

    def test_invalid_tokens_string(self, tmp_path):
        (tmp_path / "auth.json").write_text(
            json.dumps({"tokens": "not-json"}),
            encoding="utf-8",
        )
        quota = _codex_fetch_quota(tmp_path)
        assert quota.error == "cannot parse tokens"

    def test_api_error(self, tmp_path, monkeypatch):
        (tmp_path / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "tok"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=10: (_ for _ in ()).throw(ConnectionError("nope")),
        )

        quota = _codex_fetch_quota(tmp_path)
        assert "nope" in quota.error

    def test_rate_limit_reached(self, tmp_path, monkeypatch):
        (tmp_path / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "tok"}}),
            encoding="utf-8",
        )

        class FakeResp:
            def read(self):
                return json.dumps(
                    {
                        "rate_limit": {"limit_reached": True},
                    }
                ).encode()

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=10: FakeResp())

        quota = _codex_fetch_quota(tmp_path)
        assert quota.error == "rate limit reached"

    def test_credits_invalid_balance(self, tmp_path, monkeypatch):
        (tmp_path / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "tok"}}),
            encoding="utf-8",
        )

        class FakeResp:
            def read(self):
                return json.dumps(
                    {
                        "credits": {"balance": "not-a-number", "has_credits": True},
                        "rate_limit": {},
                    }
                ).encode()

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=10: FakeResp())

        quota = _codex_fetch_quota(tmp_path)
        assert quota.extra_usage_limit == 0.0
        assert quota.extra_usage_enabled is True

    def test_account_id_header_set(self, tmp_path, monkeypatch):
        (tmp_path / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "tok", "account_id": "acc-42"}}),
            encoding="utf-8",
        )

        captured = {}

        class FakeResp:
            def read(self):
                return b'{"rate_limit": {}}'

        def fake_urlopen(req, timeout=10):
            captured["account_id"] = req.get_header("Chatgpt-account-id")
            return FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        _codex_fetch_quota(tmp_path)
        assert captured["account_id"] == "acc-42"


# ── probe_all ─────────────────────────────────────────────────────


class TestProbeAll:
    def test_returns_both_providers(self, monkeypatch):
        monkeypatch.setattr(
            "openmax.provider_usage._probe_claude_code",
            lambda: ProviderStatus(provider="claude-code", installed=True),
        )
        monkeypatch.setattr(
            "openmax.provider_usage._probe_codex",
            lambda: ProviderStatus(provider="codex", installed=True),
        )

        results = probe_all()
        assert len(results) == 2
        assert results[0].provider == "claude-code"
        assert results[1].provider == "codex"

    def test_catches_probe_exception(self, monkeypatch):
        def exploding_probe():
            raise RuntimeError("boom")

        monkeypatch.setattr("openmax.provider_usage._probe_claude_code", exploding_probe)
        monkeypatch.setattr(
            "openmax.provider_usage._probe_codex",
            lambda: ProviderStatus(provider="codex"),
        )

        results = probe_all()
        assert len(results) == 2
        assert results[0].error == "boom"
        assert results[1].provider == "codex"
