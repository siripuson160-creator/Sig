"""Browser setup wizard: the guards, the sign-in flow, and what it writes.

The Telethon client is replaced with a stand-in, so the whole wizard can be
driven end to end without a real Telegram account. What is *not* stubbed is the
part that matters for section 3: the assertions below check that the login code
and the 2FA password never reach the log, the .env or any stored state.
"""

from __future__ import annotations

import logging
import os

import pytest
from fastapi.testclient import TestClient

from app import setup_state
from app.api import routes_setup


# --------------------------------------------------------------- stand-ins
class FakeMe:
    first_name = "Owner"
    id = 4242


class FakeDialog:
    def __init__(self, id_, name, is_group=True, is_channel=False):
        self.id = id_
        self.name = name
        self.is_group = is_group
        self.is_channel = is_channel


class FakeSentCode:
    phone_code_hash = "hash-from-telegram"


class SessionPasswordNeededError(Exception):
    """Same name as Telethon's, which is what the handler matches on."""


class PhoneCodeInvalidError(Exception):
    pass


class FakeClient:
    """Records what it was asked, so the tests can assert on the sequence."""

    def __init__(self, *, needs_password=False, bad_code=False):
        self.needs_password = needs_password
        self.bad_code = bad_code
        self.authorized = False
        self.connected = False
        self.sign_in_calls: list[dict] = []
        self.code_requests: list[str] = []

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        return self.authorized

    async def send_code_request(self, phone):
        self.code_requests.append(phone)
        return FakeSentCode()

    async def sign_in(self, phone=None, code=None, phone_code_hash=None, password=None):
        self.sign_in_calls.append({"phone": phone, "code": code, "password": password})
        if password is not None:
            self.authorized = True
            return FakeMe()
        if self.bad_code:
            raise PhoneCodeInvalidError("nope")
        if self.needs_password:
            raise SessionPasswordNeededError()
        self.authorized = True
        return FakeMe()

    async def get_me(self):
        return FakeMe()

    async def iter_dialogs(self, limit=None):
        for dialog in [
            FakeDialog(-1001, "Gold Signals VIP"),
            FakeDialog(-1002, "Announcements", is_group=False, is_channel=True),
            FakeDialog(555, "A Person", is_group=False, is_channel=False),
        ]:
            yield dialog


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An unconfigured install rooted in a temp directory."""
    env_path = tmp_path / ".env"
    token_path = tmp_path / "data" / "setup-token"
    monkeypatch.setattr(setup_state, "ENV_PATH", str(env_path))
    monkeypatch.setattr(setup_state, "TOKEN_PATH", str(token_path))
    # conftest exports these for the rest of the suite; here they would make
    # the install look configured and close the wizard before it opened.
    for key in setup_state.REQUIRED_KEYS:
        monkeypatch.delenv(key, raising=False)
    token = setup_state.ensure_token()
    return {"env": str(env_path), "token": token, "dir": tmp_path}


@pytest.fixture
def client(env, monkeypatch):
    from app.api.main import create_app

    fake = FakeClient()

    async def build(api_id, api_hash):
        return fake

    async def no_restart():
        return None

    monkeypatch.setattr(routes_setup, "_build_client", build)
    monkeypatch.setattr(routes_setup, "signin", routes_setup.TelegramSignIn())
    # /finish would otherwise schedule os._exit(0), which would take the test
    # run with it if the loop ever got far enough to run the task.
    monkeypatch.setattr(routes_setup, "_restart_soon", no_restart)
    with TestClient(create_app(init_database=False)) as test_client:
        test_client.fake = fake
        test_client.token = env["token"]
        test_client.env_path = env["env"]
        yield test_client


def auth(client):
    return {"X-Setup-Token": client.token}


CREDS = {"api_id": 12345, "api_hash": "abcdef0123456789", "phone": "+66811111111"}


# ------------------------------------------------------------------- guards
def test_setup_requires_the_token(client):
    assert client.post("/api/setup/verify-token").status_code == 401
    assert client.post("/api/setup/verify-token", headers={"X-Setup-Token": "guess"}).status_code == 401
    assert client.post("/api/setup/verify-token", headers=auth(client)).status_code == 200


def test_every_setup_route_is_guarded(client):
    """A missing token must never let a stranger configure the box."""
    unguarded = []
    for path, payload in [
        ("/api/setup/telegram/send-code", CREDS),
        ("/api/setup/telegram/sign-in", {"code": "11111"}),
        ("/api/setup/telegram/password", {"password": "x"}),
        ("/api/setup/line/test", {"access_token": "x" * 20, "destination": "Cabc"}),
        ("/api/setup/finish", {"chat_id": "-1001"}),
    ]:
        if client.post(path, json=payload).status_code != 401:
            unguarded.append(path)
    assert client.get("/api/setup/telegram/groups").status_code == 401
    assert unguarded == []


def test_status_is_readable_without_a_token(client):
    """The page has to render before the operator has pasted anything."""
    body = client.get("/api/setup/status").json()
    assert body["configured"] is False
    assert "TELEGRAM_API_ID" in body["needs"]


def test_a_configured_install_closes_the_wizard(client, monkeypatch):
    monkeypatch.setattr(setup_state, "is_configured", lambda path=None: True)
    response = client.post("/api/setup/verify-token", headers=auth(client))
    assert response.status_code == 409
    assert "already configured" in response.json()["detail"]


# ------------------------------------------------------------------ sign-in
def test_the_happy_path_signs_in_and_lists_groups(client):
    sent = client.post("/api/setup/telegram/send-code", json=CREDS, headers=auth(client))
    assert sent.status_code == 200
    assert sent.json() == {"already_signed_in": False, "sent": True}

    signed = client.post("/api/setup/telegram/sign-in", json={"code": "54321"}, headers=auth(client))
    assert signed.status_code == 200
    assert signed.json()["signed_in"] is True
    assert signed.json()["account"] == "Owner"

    groups = client.get("/api/setup/telegram/groups", headers=auth(client)).json()["groups"]
    # Only groups and channels: a private chat is never the signal source.
    assert [g["name"] for g in groups] == ["Gold Signals VIP", "Announcements"]
    assert groups[0]["id"] == "-1001"


def test_two_factor_accounts_are_asked_for_the_password(client):
    client.fake.needs_password = True
    client.post("/api/setup/telegram/send-code", json=CREDS, headers=auth(client))

    step = client.post("/api/setup/telegram/sign-in", json={"code": "54321"}, headers=auth(client))
    assert step.json() == {"needs_password": True}

    done = client.post("/api/setup/telegram/password", json={"password": "hunter2"}, headers=auth(client))
    assert done.status_code == 200
    assert done.json()["signed_in"] is True


def test_a_wrong_code_is_reported_without_echoing_it(client):
    client.fake.bad_code = True
    client.post("/api/setup/telegram/send-code", json=CREDS, headers=auth(client))
    response = client.post("/api/setup/telegram/sign-in", json={"code": "00000"}, headers=auth(client))
    assert response.status_code == 400
    assert "00000" not in response.text


def test_signing_in_before_asking_for_a_code_is_refused(client):
    response = client.post("/api/setup/telegram/sign-in", json={"code": "54321"}, headers=auth(client))
    assert response.status_code == 409


def test_an_already_authorised_session_skips_the_code(client):
    """Re-running setup after a successful login should not re-authenticate."""
    client.fake.authorized = True
    response = client.post("/api/setup/telegram/send-code", json=CREDS, headers=auth(client))
    assert response.json() == {"already_signed_in": True, "account": "Owner"}
    assert client.fake.code_requests == []


def test_groups_need_a_completed_sign_in(client):
    assert client.get("/api/setup/telegram/groups", headers=auth(client)).status_code == 409


# ------------------------------------- section 3: the OTP goes nowhere else
def test_the_code_and_password_never_reach_the_log(client, caplog):
    caplog.set_level(logging.DEBUG)
    client.fake.needs_password = True
    client.post("/api/setup/telegram/send-code", json=CREDS, headers=auth(client))
    client.post("/api/setup/telegram/sign-in", json={"code": "13579"}, headers=auth(client))
    client.post("/api/setup/telegram/password", json={"password": "s3cret-2fa"}, headers=auth(client))

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "13579" not in logged
    assert "s3cret-2fa" not in logged
    # The phone number is an account identifier, so it is not logged either.
    assert CREDS["phone"] not in logged


def test_the_code_and_password_are_not_kept_in_memory(client):
    client.fake.needs_password = True
    client.post("/api/setup/telegram/send-code", json=CREDS, headers=auth(client))
    client.post("/api/setup/telegram/sign-in", json={"code": "13579"}, headers=auth(client))
    client.post("/api/setup/telegram/password", json={"password": "s3cret-2fa"}, headers=auth(client))

    held = vars(routes_setup.signin)
    assert "13579" not in str(held)
    assert "s3cret-2fa" not in str(held)
    # The phone is cleared once it is no longer needed.
    assert routes_setup.signin.phone == ""


def test_the_written_env_holds_no_otp_or_password(client):
    client.fake.needs_password = True
    client.post("/api/setup/telegram/send-code", json=CREDS, headers=auth(client))
    client.post("/api/setup/telegram/sign-in", json={"code": "13579"}, headers=auth(client))
    client.post("/api/setup/telegram/password", json={"password": "s3cret-2fa"}, headers=auth(client))
    client.post("/api/setup/finish", json={"chat_id": "-1001"}, headers=auth(client))

    written = open(client.env_path, encoding="utf-8").read()
    assert "13579" not in written
    assert "s3cret-2fa" not in written
    assert CREDS["phone"] not in written


# ------------------------------------------------------------------- finish
@pytest.fixture
def signed_in(client):
    """A client that has completed the Telegram sign-in."""
    client.post("/api/setup/telegram/send-code", json=CREDS, headers=auth(client))
    client.post("/api/setup/telegram/sign-in", json={"code": "54321"}, headers=auth(client))
    return client


def test_finishing_writes_a_usable_env(signed_in):
    response = signed_in.post(
        "/api/setup/finish",
        json={
            "chat_id": "-1001",
            "line_access_token": "line-token-value",
            "line_destination": "Cabc123",
            "price_provider": "twelvedata",
            "price_api_key": "td-key",
            "timezone": "Asia/Bangkok",
            "admin_password": "chosen-password",
        },
        headers=auth(signed_in),
    )
    assert response.status_code == 200
    assert response.json()["admin_password"] == "chosen-password"

    values = setup_state.read_env(signed_in.env_path)
    assert values["TELEGRAM_API_ID"] == "12345"
    assert values["TELEGRAM_API_HASH"] == "abcdef0123456789"
    assert values["TELEGRAM_SOURCE_CHAT_ID"] == "-1001"
    assert values["LINE_CHANNEL_ACCESS_TOKEN"] == "line-token-value"
    assert values["LINE_GROUP_ID"] == "Cabc123"
    assert values["PRICE_DATA_PROVIDER"] == "twelvedata"
    assert values["ADMIN_PASSWORD"] == "chosen-password"
    # And the install now reads as configured.
    assert setup_state.is_configured(signed_in.env_path)


def test_the_env_is_not_world_readable(signed_in):
    """It holds the LINE token and the admin password."""
    signed_in.post("/api/setup/finish", json={"chat_id": "-1001"}, headers=auth(signed_in))
    assert oct(os.stat(signed_in.env_path).st_mode & 0o777) == "0o600"


def test_an_admin_password_is_generated_when_none_is_given(signed_in):
    response = signed_in.post("/api/setup/finish", json={"chat_id": "-1001"}, headers=auth(signed_in))
    generated = response.json()["admin_password"]
    assert len(generated) >= 12
    assert setup_state.read_env(signed_in.env_path)["ADMIN_PASSWORD"] == generated


def test_skipping_line_starts_in_test_mode(signed_in):
    """No LINE credentials must mean 'store but do not push', never a crash."""
    signed_in.post(
        "/api/setup/finish",
        json={"chat_id": "-1001", "line_enabled": False, "dry_run": True},
        headers=auth(signed_in),
    )
    values = setup_state.read_env(signed_in.env_path)
    assert values["LINE_ENABLED"] == "false"
    assert values["DRY_RUN"] == "true"


def test_the_setup_link_stops_working_once_finished(signed_in):
    signed_in.post("/api/setup/finish", json={"chat_id": "-1001"}, headers=auth(signed_in))
    assert setup_state.read_token() is None


def test_finishing_without_signing_in_is_refused(client):
    response = client.post("/api/setup/finish", json={"chat_id": "-1001"}, headers=auth(client))
    assert response.status_code == 409


def test_a_junk_symbol_falls_back_instead_of_reaching_the_env(signed_in):
    signed_in.post(
        "/api/setup/finish",
        json={"chat_id": "-1001", "price_symbol": "'; DROP TABLE signals;--"},
        headers=auth(signed_in),
    )
    assert setup_state.read_env(signed_in.env_path)["PRICE_SYMBOL"] == "XAUUSD"


def test_an_unknown_scoring_rule_falls_back_to_the_conservative_one(signed_in):
    signed_in.post(
        "/api/setup/finish",
        json={"chat_id": "-1001", "ambiguity_rule": "ALWAYS_WIN"},
        headers=auth(signed_in),
    )
    assert setup_state.read_env(signed_in.env_path)["AMBIGUITY_RULE"] == "SL_FIRST"


def test_an_existing_env_is_backed_up_before_being_replaced(signed_in, env):
    with open(signed_in.env_path, "w", encoding="utf-8") as handle:
        handle.write("PREVIOUS=1\n")
    signed_in.post("/api/setup/finish", json={"chat_id": "-1001"}, headers=auth(signed_in))

    backups = [p for p in os.listdir(env["dir"]) if p.startswith(".env.bak-")]
    assert len(backups) == 1
    assert "PREVIOUS=1" in open(os.path.join(env["dir"], backups[0]), encoding="utf-8").read()


# -------------------------------------------------------------------- pages
def test_an_unconfigured_install_sends_every_page_to_setup(client):
    for path in ("/", "/dashboard", "/admin"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (302, 307)
        assert response.headers["location"] == "/setup"


def test_a_configured_install_sends_setup_back_to_the_dashboard(client, monkeypatch):
    monkeypatch.setattr(setup_state, "is_configured", lambda path=None: True)
    response = client.get("/setup", follow_redirects=False)
    assert response.headers["location"] == "/dashboard"


# ------------------------------------------------------------ setup_state
def test_is_configured_needs_every_required_key(tmp_path):
    path = tmp_path / ".env"
    path.write_text("TELEGRAM_API_ID=1\nTELEGRAM_API_HASH=x\n")
    assert setup_state.missing_keys(str(path), include_environ=False) == ["TELEGRAM_SOURCE_CHAT_ID"]

    path.write_text("TELEGRAM_API_ID=1\nTELEGRAM_API_HASH=x\nTELEGRAM_SOURCE_CHAT_ID=-100\n")
    assert setup_state.missing_keys(str(path), include_environ=False) == []


def test_an_empty_value_counts_as_missing(tmp_path):
    """A half-written .env must reopen the wizard, not start a broken service."""
    path = tmp_path / ".env"
    path.write_text("TELEGRAM_API_ID=1\nTELEGRAM_API_HASH=\nTELEGRAM_SOURCE_CHAT_ID=-100\n")
    assert setup_state.missing_keys(str(path), include_environ=False) == ["TELEGRAM_API_HASH"]


def test_environment_variables_count_as_configured(tmp_path, monkeypatch):
    """A container or a systemd unit with Environment= lines has no .env."""
    path = tmp_path / "absent.env"
    for key in setup_state.REQUIRED_KEYS:
        monkeypatch.setenv(key, "set-in-the-environment")
    assert setup_state.is_configured(str(path))
    assert setup_state.missing_keys(str(path), include_environ=False) == list(setup_state.REQUIRED_KEYS)


def test_the_token_file_is_private(env):
    assert oct(os.stat(setup_state.TOKEN_PATH).st_mode & 0o777) == "0o600"


def test_ensure_token_is_stable_across_calls(env):
    assert setup_state.ensure_token() == env["token"]


def test_a_half_filled_env_does_not_crash_the_settings(tmp_path, monkeypatch):
    """The wizard writes every key, blanks included, and so does a partial edit.

    A blank numeric used to raise at import time, which crash-looped the
    service before it could serve the page that would fix it. A blank now means
    "unset", so the declared default applies.
    """
    from app.config import Settings

    blank = ("TELEGRAM_API_ID", "API_PORT", "DRY_RUN", "POINT_SIZE", "LINE_CHANNEL_ACCESS_TOKEN")
    for key in blank:
        # Otherwise conftest's exports outrank the file under test.
        monkeypatch.delenv(key, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{key}=" for key in blank) + "\n")

    settings = Settings(_env_file=str(env_file))
    assert settings.telegram_api_id == 0
    assert settings.api_port == 8000
    assert settings.dry_run is False
    assert settings.point_size == 1.0
    # Text settings keep the empty answer rather than reverting to a default.
    assert settings.line_channel_access_token == ""


def test_setup_mode_runs_only_the_web_tier(monkeypatch):
    """An unconfigured install must serve /setup, not crash-loop the unit."""
    from app.main import _setup_mode_components

    monkeypatch.setattr(setup_state, "is_configured", lambda path=None: False)
    monkeypatch.setattr(setup_state, "ensure_token", lambda: "t")
    assert _setup_mode_components({"listener", "line", "results", "api"}) == {"api"}


def test_a_configured_install_runs_everything(monkeypatch):
    from app.main import _setup_mode_components

    monkeypatch.setattr(setup_state, "is_configured", lambda path=None: True)
    everything = {"listener", "line", "results", "api"}
    assert _setup_mode_components(everything) == everything
