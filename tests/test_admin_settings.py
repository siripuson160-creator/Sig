"""Editing connection settings from the admin page.

This is the one write path into the configuration, so most of what is checked
here is what it refuses: keys off the allow-list, secrets leaking back to the
browser, and secret values reaching the audit log.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import setup_state
from app.api import routes_settings
from app.config import settings
from app.setup_wizard import read_env

ENV = """TELEGRAM_API_ID=12345
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
TELEGRAM_SOURCE_CHAT_ID=-1001234567890
LINE_CHANNEL_ACCESS_TOKEN=a-very-long-line-token-value
LINE_GROUP_ID=Cabc123
LINE_ENABLED=true
DRY_RUN=true
ADMIN_PASSWORD=test-admin-key
"""


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(ENV)
    monkeypatch.setattr(setup_state, "ENV_PATH", str(path))
    # /settings/editable would otherwise schedule os._exit(0).
    async def no_restart():
        return None

    monkeypatch.setattr(routes_settings, "_restart_soon", no_restart)
    return path


@pytest.fixture
def client(env_file):
    from app.api.main import create_app

    with TestClient(create_app(init_database=True)) as test_client:
        yield test_client


def auth(client):
    token = client.post("/api/admin/login", json={"password": settings.admin_secret}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def save(client, values, **extra):
    return client.post(
        "/api/admin/settings/editable",
        json={"values": values, "restart": False, **extra},
        headers=auth(client),
    )


# ------------------------------------------------------------------- access
def test_reading_the_settings_needs_an_admin(client):
    assert client.get("/api/admin/settings/editable").status_code == 401


def test_writing_the_settings_needs_an_admin(client):
    response = client.post("/api/admin/settings/editable", json={"values": {"LINE_GROUP_ID": "Cnew"}})
    assert response.status_code == 401


# ------------------------------------------------------------------ secrets
def test_a_stored_secret_is_never_returned(client, env_file):
    """The page shows that a token is set, and a hint — never the token."""
    items = {i["key"]: i for i in client.get("/api/admin/settings/editable", headers=auth(client)).json()["items"]}

    token = items["LINE_CHANNEL_ACCESS_TOKEN"]
    assert token["secret"] is True
    assert token["is_set"] is True
    assert "a-very-long-line-token-value" not in token["value"]
    assert token["value"] == "a-ve…alue"

    # A non-secret is shown in full: it is what makes the page usable.
    assert items["LINE_GROUP_ID"]["value"] == "Cabc123"


def test_a_blank_secret_keeps_the_stored_value(client, env_file):
    """So the form can be saved without re-typing every token."""
    save(client, {"LINE_CHANNEL_ACCESS_TOKEN": "", "LINE_GROUP_ID": "Cnew999"})
    stored = read_env(str(env_file))
    assert stored["LINE_CHANNEL_ACCESS_TOKEN"] == "a-very-long-line-token-value"
    assert stored["LINE_GROUP_ID"] == "Cnew999"


def test_a_secret_can_still_be_replaced(client, env_file):
    save(client, {"LINE_CHANNEL_ACCESS_TOKEN": "a-brand-new-token"})
    assert read_env(str(env_file))["LINE_CHANNEL_ACCESS_TOKEN"] == "a-brand-new-token"


async def test_the_audit_log_records_the_key_not_the_secret(client, env_file):
    save(client, {"LINE_CHANNEL_ACCESS_TOKEN": "another-secret-token"})
    entries = client.get("/api/admin/audit?limit=20", headers=auth(client)).json()["items"]
    settings_entries = [e for e in entries if e["entity_type"] == "settings"]
    assert settings_entries, "the change must be audited"
    assert "LINE_CHANNEL_ACCESS_TOKEN" in settings_entries[0]["summary"]
    assert "another-secret-token" not in str(settings_entries)


# ------------------------------------------------------------- allow-list
def test_a_key_off_the_allow_list_is_refused(client, env_file):
    """Nothing outside EDITABLE can be written, whatever is posted."""
    response = save(client, {"DATABASE_URL": "postgresql://evil", "ADMIN_PASSWORD": "hijacked"})
    body = response.json()
    assert body["changed"] == []
    assert set(body["ignored"]) == {"DATABASE_URL", "ADMIN_PASSWORD"}

    stored = read_env(str(env_file))
    assert "DATABASE_URL" not in stored
    assert stored["ADMIN_PASSWORD"] == "test-admin-key"


def test_no_statistic_can_be_typed_in(client):
    """Section 43: the engines stay the only source of a published number."""
    keys = {item.key for item in routes_settings.EDITABLE}
    forbidden = {"WIN_RATE", "TOTAL_PROFIT", "PROFIT_POINTS", "WINS", "LOSSES", "TOTAL_SIGNALS"}
    assert not keys & forbidden
    assert not any("PROFIT" in key or "WIN" in key for key in keys)


# ------------------------------------------------------------- validation
def test_a_bool_is_normalised(client, env_file):
    save(client, {"DRY_RUN": "no", "LINE_ENABLED": "yes"})
    stored = read_env(str(env_file))
    assert stored["DRY_RUN"] == "false"
    assert stored["LINE_ENABLED"] == "true"


def test_a_number_that_is_not_a_number_is_ignored(client, env_file):
    save(client, {"TELEGRAM_API_ID": "not-a-number"})
    assert read_env(str(env_file))["TELEGRAM_API_ID"] == "12345"


def test_a_choice_outside_the_list_is_ignored(client, env_file):
    save(client, {"RESULT_SOURCE": "whatever-i-like"})
    assert "RESULT_SOURCE" not in read_env(str(env_file))
    save(client, {"RESULT_SOURCE": "message"})
    assert read_env(str(env_file))["RESULT_SOURCE"] == "message"


# ---------------------------------------------------------------- writing
def test_saving_reports_only_what_actually_moved(client, env_file):
    body = save(client, {"LINE_GROUP_ID": "Cabc123", "PRICE_SYMBOL": "XAUUSD"}).json()
    assert body["changed"] == ["PRICE_SYMBOL"]  # the group id was already that


def test_the_env_stays_private(client, env_file):
    import os

    save(client, {"LINE_GROUP_ID": "Cprivate"})
    assert oct(os.stat(env_file).st_mode & 0o777) == "0o600"


def test_settings_the_operator_needs_are_all_editable():
    """The point of the page: fix Telegram and LINE without an SSH session."""
    keys = {item.key for item in routes_settings.EDITABLE}
    assert {
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_SOURCE_CHAT_ID",
        "LINE_CHANNEL_ACCESS_TOKEN",
        "LINE_GROUP_ID",
        "DRY_RUN",
    } <= keys


# ------------------------------------------------- the page shows them all
def _settings_groups() -> dict[str, list[str]]:
    """The GROUPS array out of admin.js, without a JavaScript engine.

    Crude, but it is the only way to check from here that the page renders the
    keys the server offers — and that is worth checking, because it once did
    not: DELIVERY_TARGET was added to EDITABLE and stayed invisible, so the
    operator was told a setting had not been deployed when it had.
    """
    import re
    from pathlib import Path

    source = Path("app/web/static/js/admin.js").read_text()
    block = re.search(r"const GROUPS = \[(.*?)\n  \];", source, re.S)
    assert block, "GROUPS array not found in admin.js"
    groups: dict[str, list[str]] = {}
    for title, keys in re.findall(r"\['([^']+)',\s*\[(.*?)\]\]", block.group(1), re.S):
        groups[title] = re.findall(r"'([A-Z_]+)'", keys)
    return groups


def test_every_editable_setting_is_placed_on_the_page():
    """A key the server will accept must be one the operator can find."""
    grouped = {key for keys in _settings_groups().values() for key in keys}
    missing = sorted({item.key for item in routes_settings.EDITABLE} - grouped)
    assert not missing, f"editable but not on the settings page: {', '.join(missing)}"


def test_the_page_does_not_offer_settings_the_server_refuses():
    """The reverse: a field that cannot be saved would be a trap."""
    editable = {item.key for item in routes_settings.EDITABLE}
    extra = sorted({key for keys in _settings_groups().values() for key in keys} - editable)
    assert not extra, f"on the settings page but not editable: {', '.join(extra)}"


def test_choosing_the_destination_is_editable(client, env_file):
    """Section: messages must be re-pointable without an SSH session."""
    save(client, {"DELIVERY_TARGET": "telegram", "TELEGRAM_TARGET_CHAT_ID": "@goldsignals"})
    stored = read_env(str(env_file))
    assert stored["DELIVERY_TARGET"] == "telegram"
    assert stored["TELEGRAM_TARGET_CHAT_ID"] == "@goldsignals"


def test_an_unknown_destination_is_refused(client, env_file):
    save(client, {"DELIVERY_TARGET": "carrier-pigeon"})
    assert "DELIVERY_TARGET" not in read_env(str(env_file))
