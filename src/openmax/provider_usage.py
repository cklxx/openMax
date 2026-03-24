"""Probe local coding-agent installations for subscription usage data."""

from __future__ import annotations

import glob as glob_mod
import json
import sqlite3
import time
import urllib.request  # noqa: F401 (used in function bodies)
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Data classes ───────────────────────────────────────────────────


@dataclass
class ModelUsage:
    """Token usage breakdown for a single model."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class DailyActivity:
    """Activity stats for a single day."""

    date: str
    messages: int = 0
    sessions: int = 0
    tool_calls: int = 0
    tokens_by_model: dict[str, int] = field(default_factory=dict)


@dataclass
class WindowUsage:
    """Usage in a sliding time window (e.g. last 5 hours)."""

    window_hours: int = 5
    messages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    models: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class QuotaWindow:
    """Rate-limit window from provider API."""

    name: str  # e.g. "5-hour session", "7-day weekly"
    used_pct: float  # 0-100
    resets_at: str | None = None  # ISO timestamp
    reset_seconds: int | None = None


@dataclass
class QuotaInfo:
    """Subscription quota from provider API."""

    windows: list[QuotaWindow] = field(default_factory=list)
    plan: str | None = None
    rate_tier: str | None = None
    extra_usage_enabled: bool = False
    extra_usage_used: float = 0.0
    extra_usage_limit: float = 0.0
    error: str | None = None


@dataclass
class ProviderStatus:
    """Usage status for one coding-agent provider."""

    provider: str
    installed: bool = False
    version: str | None = None
    plan: str | None = None
    model: str | None = None
    total_sessions: int = 0
    total_messages: int = 0
    total_tokens: int = 0
    model_usage: list[ModelUsage] = field(default_factory=list)
    daily_activity: list[DailyActivity] = field(default_factory=list)
    window_usage: WindowUsage | None = None
    quota: QuotaInfo | None = None
    first_used: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ── Public API ─────────────────────────────────────────────────────


def probe_all() -> list[ProviderStatus]:
    """Probe all known coding-agent providers and return their status."""
    probes = [
        _probe_claude_code,
        _probe_codex,
    ]
    results: list[ProviderStatus] = []
    for probe in probes:
        try:
            results.append(probe())
        except Exception as exc:
            name = probe.__name__.replace("_probe_", "")
            results.append(ProviderStatus(provider=name, error=str(exc)))
    return results


# ── Claude Code ────────────────────────────────────────────────────

_CLAUDE_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_CLAUDE_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"

_PLAN_LABELS: dict[str, str] = {
    "pro": "Pro",
    "max_5x": "Max 5x",
    "max": "Max 20x",
    "max_20x": "Max 20x",
    "team": "Team",
    "enterprise": "Enterprise",
}

_RATE_TIER_LABELS: dict[str, str] = {
    "default_claude_pro": "Pro",
    "default_claude_max_5x": "Max 5x",
    "default_claude_max_20x": "Max 20x",
}


def _probe_claude_code() -> ProviderStatus:
    """Probe Claude Code: local stats + OAuth usage API."""
    import shutil

    status = ProviderStatus(provider="claude-code")

    claude_bin = shutil.which("claude")
    if not claude_bin:
        return status
    status.installed = True

    # ── Local stats ────────────────────────────────────
    stats_path = Path.home() / ".claude" / "stats-cache.json"
    if stats_path.exists():
        try:
            data = json.loads(stats_path.read_text(encoding="utf-8"))
            _populate_claude_local_stats(status, data)
        except (json.JSONDecodeError, OSError):
            pass

    # ── Version ────────────────────────────────────────
    try:
        import subprocess

        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            status.version = result.stdout.strip().split("\n")[0]
    except Exception:
        pass

    # ── 5-hour local JSONL scan ────────────────────────
    status.window_usage = _claude_window_usage(5)

    # ── OAuth usage API (quota) ────────────────────────
    status.quota = _claude_fetch_quota()
    if status.quota and status.quota.plan:
        status.plan = status.quota.plan

    return status


def _populate_claude_local_stats(status: ProviderStatus, data: dict) -> None:
    status.total_sessions = data.get("totalSessions", 0)
    status.total_messages = data.get("totalMessages", 0)
    status.first_used = data.get("firstSessionDate")

    for model_name, usage in data.get("modelUsage", {}).items():
        mu = ModelUsage(
            model=model_name,
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            cache_read_tokens=usage.get("cacheReadInputTokens", 0),
            cache_creation_tokens=usage.get("cacheCreationInputTokens", 0),
        )
        status.model_usage.append(mu)
        status.total_tokens += mu.input_tokens + mu.output_tokens

    for day in data.get("dailyActivity", []):
        status.daily_activity.append(
            DailyActivity(
                date=day.get("date", ""),
                messages=day.get("messageCount", 0),
                sessions=day.get("sessionCount", 0),
                tool_calls=day.get("toolCallCount", 0),
            )
        )

    daily_tokens: dict[str, dict[str, int]] = {}
    for day in data.get("dailyModelTokens", []):
        daily_tokens[day.get("date", "")] = day.get("tokensByModel", {})
    for activity in status.daily_activity:
        if activity.date in daily_tokens:
            activity.tokens_by_model = daily_tokens[activity.date]

    if status.model_usage:
        status.model = max(status.model_usage, key=lambda m: m.total_tokens).model

    status.raw = {
        "stats_path": str(Path.home() / ".claude" / "stats-cache.json"),
        "last_computed": data.get("lastComputedDate"),
    }


def _claude_fetch_quota() -> QuotaInfo:
    """Fetch quota from Anthropic OAuth usage API."""
    quota = QuotaInfo()

    # Get OAuth token from keychain
    oauth = _claude_read_oauth()
    if oauth is None:
        quota.error = "no credentials"
        return quota

    access_token = oauth.get("accessToken", "")
    sub_type = oauth.get("subscriptionType", "")
    rate_tier = oauth.get("rateLimitTier", "")

    quota.plan = _PLAN_LABELS.get(sub_type, sub_type) if sub_type else None
    quota.rate_tier = _RATE_TIER_LABELS.get(rate_tier, rate_tier) if rate_tier else None

    # Check expiry — refresh if needed
    expires_at = oauth.get("expiresAt", 0)
    now_ms = int(time.time() * 1000)
    expired = bool(expires_at and now_ms > expires_at - 5 * 60 * 1000)
    if expired:
        refreshed = _claude_refresh_token(oauth.get("refreshToken", ""))
        if refreshed:
            access_token = (
                refreshed.get("accessToken") or refreshed.get("access_token") or access_token
            )
            _claude_save_refreshed_token(oauth, refreshed)
        else:
            quota.error = "credentials expired — launch claude to re-authenticate"
            return quota

    # Call usage API
    try:
        req = urllib.request.Request(
            _CLAUDE_USAGE_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "claude-code/2.1.76",
            },
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
    except Exception as exc:
        quota.error = str(exc)
        return quota

    # Parse windows
    for key, label in [
        ("five_hour", "5-hour session"),
        ("seven_day", "7-day weekly"),
        ("seven_day_sonnet", "7-day Sonnet"),
        ("seven_day_opus", "7-day Opus"),
    ]:
        window_data = data.get(key)
        if window_data and window_data.get("utilization") is not None:
            quota.windows.append(
                QuotaWindow(
                    name=label,
                    used_pct=window_data["utilization"],
                    resets_at=window_data.get("resets_at"),
                )
            )

    # Extra usage / overages
    extra = data.get("extra_usage")
    if extra:
        quota.extra_usage_enabled = extra.get("is_enabled", False)
        quota.extra_usage_used = extra.get("used_credits", 0.0) or 0.0
        quota.extra_usage_limit = extra.get("monthly_limit", 0) or 0

    return quota


def _claude_read_oauth() -> dict | None:
    """Read Claude OAuth credentials from keychain or credentials file."""
    # Try keychain first
    try:
        import subprocess

        r = subprocess.run(
            ["security", "find-generic-password", "-s", _CLAUDE_KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            cred = json.loads(r.stdout.strip())
            return cred.get("claudeAiOauth")
    except Exception:
        pass

    # Fallback to credentials file
    cred_path = Path.home() / ".claude" / ".credentials.json"
    if cred_path.exists():
        try:
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            return data.get("claudeAiOauth")
        except (json.JSONDecodeError, OSError):
            pass

    return None


def _claude_refresh_token(refresh_token: str) -> dict | None:
    """Refresh Claude OAuth access token."""
    if not refresh_token:
        return None
    try:
        body = json.dumps(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _CLAUDE_OAUTH_CLIENT_ID,
            }
        ).encode()
        req = urllib.request.Request(
            _CLAUDE_TOKEN_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "claude-code/2.1.81",
            },
        )
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except Exception:
        return None


def _claude_save_refreshed_token(original: dict, refreshed: dict) -> None:
    """Persist refreshed OAuth tokens back to keychain or credentials file."""
    new_access = refreshed.get("accessToken") or refreshed.get("access_token")
    if not new_access:
        return

    new_refresh = (
        refreshed.get("refreshToken")
        or refreshed.get("refresh_token")
        or original.get("refreshToken")
    )
    new_expires = (
        refreshed.get("expiresAt")
        or refreshed.get("expires_at")
        or _compute_expiry(refreshed.get("expires_in"))
    )

    updated = {**original, "accessToken": new_access}
    if new_refresh:
        updated["refreshToken"] = new_refresh
    if new_expires:
        updated["expiresAt"] = new_expires

    _claude_write_oauth(updated)


def _compute_expiry(expires_in: int | None) -> int | None:
    """Convert expires_in (seconds) to expiresAt (epoch ms)."""
    if not expires_in:
        return None
    return int(time.time() * 1000) + expires_in * 1000


def _claude_write_oauth(oauth: dict) -> None:
    """Write updated OAuth credentials to keychain or credentials file."""
    import subprocess

    wrapper = {"claudeAiOauth": oauth}
    payload = json.dumps(wrapper)

    # Try keychain first
    try:
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-s",
                _CLAUDE_KEYCHAIN_SERVICE,
                "-a",
                "default",
                "-w",
                payload,
                "-U",
            ],
            capture_output=True,
            timeout=5,
        )
        return
    except Exception:
        pass

    # Fallback: write credentials file
    cred_path = Path.home() / ".claude" / ".credentials.json"
    try:
        cred_path.write_text(payload, encoding="utf-8")
    except OSError:
        pass


def _claude_window_usage(hours: int) -> WindowUsage:
    """Scan Claude Code project JSONL files for usage in last N hours."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    cutoff_epoch = time.time() - hours * 3600

    window = WindowUsage(window_hours=hours)

    pattern = str(Path.home() / ".claude" / "projects" / "*" / "*.jsonl")
    recent_files = [f for f in glob_mod.glob(pattern) if Path(f).stat().st_mtime > cutoff_epoch]

    for filepath in recent_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") != "assistant":
                        continue
                    msg = entry.get("message")
                    if not isinstance(msg, dict) or "usage" not in msg:
                        continue
                    ts = entry.get("timestamp")
                    if ts:
                        try:
                            msg_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if msg_time < cutoff:
                                continue
                        except ValueError:
                            continue
                    usage = msg["usage"]
                    window.input_tokens += usage.get("input_tokens", 0)
                    window.output_tokens += usage.get("output_tokens", 0)
                    window.cache_read_tokens += usage.get("cache_read_input_tokens", 0)
                    window.cache_creation_tokens += usage.get("cache_creation_input_tokens", 0)
                    window.messages += 1
                    model = msg.get("model", "unknown")
                    window.models[model] = window.models.get(model, 0) + 1
        except OSError:
            continue

    return window


# ── Codex CLI ──────────────────────────────────────────────────────

_CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def _probe_codex() -> ProviderStatus:
    """Probe Codex CLI: local SQLite + ChatGPT usage API."""
    import shutil

    status = ProviderStatus(provider="codex")

    codex_bin = shutil.which("codex")
    if not codex_bin:
        return status
    status.installed = True

    codex_dir = Path.home() / ".codex"

    # ── Config ─────────────────────────────────────────
    config_path = codex_dir / "config.toml"
    if config_path.exists():
        try:
            config_text = config_path.read_text(encoding="utf-8")
            for line in config_text.splitlines():
                stripped = line.strip()
                if stripped.startswith("model") and "=" in stripped:
                    val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                    if not val.startswith("["):
                        status.model = val
                        break
        except OSError:
            pass

    # ── Version ────────────────────────────────────────
    version_path = codex_dir / "version.json"
    if version_path.exists():
        try:
            vdata = json.loads(version_path.read_text(encoding="utf-8"))
            status.version = vdata.get("latest_version")
        except (json.JSONDecodeError, OSError):
            pass

    # ── Local SQLite stats ─────────────────────────────
    state_db = codex_dir / "state_5.sqlite"
    if state_db.exists():
        try:
            conn = sqlite3.connect(str(state_db))
            row = conn.execute(
                "SELECT count(*), sum(tokens_used), min(created_at), max(created_at) FROM threads"
            ).fetchone()
            if row:
                status.total_sessions = row[0] or 0
                status.total_tokens = row[1] or 0
                if row[2]:
                    status.first_used = _epoch_to_iso(row[2])

            daily_rows = conn.execute(
                "SELECT date(created_at, 'unixepoch') as d, "
                "count(*) as sessions, sum(tokens_used) as tokens "
                "FROM threads GROUP BY d ORDER BY d DESC LIMIT 14"
            ).fetchall()
            for dr in daily_rows:
                status.daily_activity.append(
                    DailyActivity(
                        date=dr[0] or "",
                        sessions=dr[1] or 0,
                        tokens_by_model={status.model or "unknown": dr[2] or 0},
                    )
                )
            status.daily_activity.reverse()
            conn.close()
        except (sqlite3.Error, OSError) as exc:
            status.error = f"SQLite error: {exc}"

    history_path = codex_dir / "history.jsonl"
    if history_path.exists() and status.total_messages == 0:
        try:
            with history_path.open(encoding="utf-8") as f:
                status.total_messages = sum(1 for _ in f)
        except OSError:
            pass

    # ── OAuth usage API (quota) ────────────────────────
    status.quota = _codex_fetch_quota(codex_dir)
    if status.quota and status.quota.plan:
        status.plan = status.quota.plan

    status.raw = {"codex_dir": str(codex_dir)}
    return status


def _codex_fetch_quota(codex_dir: Path) -> QuotaInfo:
    """Fetch quota from ChatGPT /wham/usage API."""
    quota = QuotaInfo()

    auth_path = codex_dir / "auth.json"
    if not auth_path.exists():
        quota.error = "no auth.json"
        return quota

    try:
        adata = json.loads(auth_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        quota.error = "cannot read auth.json"
        return quota

    tokens = adata.get("tokens", {})
    if isinstance(tokens, str):
        try:
            tokens = json.loads(tokens)
        except json.JSONDecodeError:
            quota.error = "cannot parse tokens"
            return quota

    access_token = tokens.get("access_token", "")
    account_id = tokens.get("account_id", "")

    if not access_token:
        quota.error = "no access token"
        return quota

    headers: dict[str, str] = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "OpenUsage",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id

    try:
        req = urllib.request.Request(_CODEX_USAGE_URL, headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
    except Exception as exc:
        quota.error = str(exc)
        return quota

    quota.plan = data.get("plan_type")

    # Primary rate limit
    rl = data.get("rate_limit", {})
    primary = rl.get("primary_window", {})
    secondary = rl.get("secondary_window", {})

    if primary.get("used_percent") is not None:
        quota.windows.append(
            QuotaWindow(
                name="5-hour session",
                used_pct=primary["used_percent"],
                reset_seconds=primary.get("reset_after_seconds"),
                resets_at=_epoch_to_iso(primary["reset_at"]) if primary.get("reset_at") else None,
            )
        )
    if secondary.get("used_percent") is not None:
        quota.windows.append(
            QuotaWindow(
                name="7-day weekly",
                used_pct=secondary["used_percent"],
                reset_seconds=secondary.get("reset_after_seconds"),
                resets_at=_epoch_to_iso(secondary["reset_at"])
                if secondary.get("reset_at")
                else None,
            )
        )

    # Credits
    credits = data.get("credits", {})
    if credits:
        try:
            balance = float(credits.get("balance", 0))
        except (ValueError, TypeError):
            balance = 0.0
        quota.extra_usage_used = 0.0
        quota.extra_usage_limit = balance
        quota.extra_usage_enabled = credits.get("has_credits", False)

    # Limit reached flag
    if rl.get("limit_reached"):
        quota.error = "rate limit reached"

    return quota


# ── Helpers ────────────────────────────────────────────────────────


def _epoch_to_iso(epoch: int | float) -> str:
    try:
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return dt.isoformat()
    except (ValueError, OSError, OverflowError):
        return str(epoch)
