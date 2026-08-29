"""Ten points to the pip, the way Thai desks count gold.

A source posts "+70Pips" and its members read +700. Publishing +7 or +70
instead would not look broken — it would look like a modest win — which is
exactly why the convention is checked rather than assumed.
"""

from __future__ import annotations

import pytest

from app import units
from app.config import settings
from app.setup_wizard import read_env


@pytest.fixture
def thai_units(monkeypatch):
    monkeypatch.setattr(settings, "point_size", units.DEFAULT_POINT_SIZE)
    monkeypatch.setattr(settings, "pip_size", units.DEFAULT_PIP_SIZE)
    return settings


# ------------------------------------------------------------- the convention
def test_the_defaults_are_the_thai_convention(thai_units):
    assert units.points_per_pip() == pytest.approx(10.0)
    assert units.convention_holds()
    assert units.convention_warning() is None


def test_seventy_pips_reads_as_seven_hundred(thai_units):
    from app.signals.outcomes import ClaimedOutcome

    assert ClaimedOutcome(claimed_pips=70).claimed_points() == pytest.approx(700)


def test_a_loss_keeps_its_sign(thai_units):
    from app.signals.outcomes import ClaimedOutcome

    assert ClaimedOutcome(claimed_pips=-30).claimed_points() == pytest.approx(-300)


# ------------------------------------------------------------------ the check
def test_the_old_unit_is_reported_in_the_numbers_it_would_publish(monkeypatch):
    """Concrete enough to check against the group, not a ratio puzzle."""
    monkeypatch.setattr(settings, "point_size", 1.0)
    monkeypatch.setattr(settings, "pip_size", 0.1)

    warning = units.convention_warning()
    assert warning is not None
    assert "+7," in warning or "+7 " in warning, warning
    assert "+700" in warning


def test_a_pip_size_of_one_is_also_caught(monkeypatch):
    """The other half of the pair can drift on its own."""
    monkeypatch.setattr(settings, "point_size", units.DEFAULT_POINT_SIZE)
    monkeypatch.setattr(settings, "pip_size", 1.0)
    assert not units.convention_holds()
    assert "+7000" in units.convention_warning()


def test_a_zero_point_size_does_not_divide_by_zero(monkeypatch):
    monkeypatch.setattr(settings, "point_size", 0.0)
    monkeypatch.setattr(settings, "pip_size", units.DEFAULT_PIP_SIZE)
    assert units.points_per_pip() == pytest.approx(10.0)


# -------------------------------------------------------------- the migration
def _env(tmp_path, body: str):
    path = tmp_path / ".env"
    path.write_text(body)
    return str(path)


def test_a_stale_install_is_repaired(tmp_path, monkeypatch):
    """POINT_SIZE=1.0 with no PIP_SIZE is what installs carried before this."""
    monkeypatch.setattr(settings, "point_size", 1.0)
    monkeypatch.setattr(settings, "pip_size", 0.1)
    path = _env(tmp_path, "TELEGRAM_API_ID=1\nPOINT_SIZE=1.0\n")

    assert units.migrate_env(path) == ["POINT_SIZE", "PIP_SIZE"]
    stored = read_env(path)
    assert stored["POINT_SIZE"] == "0.01"
    assert stored["PIP_SIZE"] == "0.1"


def test_the_repair_applies_to_this_run_not_the_next(tmp_path, monkeypatch):
    """Rewriting the file alone would leave the run publishing +70."""
    monkeypatch.setattr(settings, "point_size", 1.0)
    monkeypatch.setattr(settings, "pip_size", 0.1)
    units.migrate_env(_env(tmp_path, "POINT_SIZE=1.0\n"))

    assert settings.point_size == pytest.approx(0.01)
    assert units.convention_holds()


def test_a_deliberate_pip_size_opts_out_for_good(tmp_path, monkeypatch):
    """Setting PIP_SIZE at all means the operator has chosen; leave it alone."""
    monkeypatch.setattr(settings, "point_size", 1.0)
    monkeypatch.setattr(settings, "pip_size", 1.0)
    path = _env(tmp_path, "POINT_SIZE=1.0\nPIP_SIZE=1.0\n")

    assert units.migrate_env(path) == []
    assert read_env(path)["POINT_SIZE"] == "1.0"


def test_a_point_size_that_is_neither_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "point_size", 0.1)
    path = _env(tmp_path, "POINT_SIZE=0.1\n")
    assert units.migrate_env(path) == []
    assert read_env(path)["POINT_SIZE"] == "0.1"


def test_an_unset_point_size_needs_no_repair(tmp_path):
    """Nothing stored means the defaults apply, and those are correct."""
    assert units.migrate_env(_env(tmp_path, "TELEGRAM_API_ID=1\n")) == []


def test_the_repair_runs_once_not_every_start(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "point_size", 1.0)
    monkeypatch.setattr(settings, "pip_size", 0.1)
    path = _env(tmp_path, "POINT_SIZE=1.0\n")

    assert units.migrate_env(path) == ["POINT_SIZE", "PIP_SIZE"]
    assert units.migrate_env(path) == []


def test_a_missing_env_file_is_not_an_error(tmp_path):
    assert units.migrate_env(str(tmp_path / "nope.env")) == []


# ---------------------------------------------------- through the real thread
# The rest of the suite pins POINT_SIZE=1.0 (tests/conftest.py) because the
# engine tests are about arithmetic, not about a deployment's unit. That leaves
# nothing asserting what the shipped defaults actually publish, which is the
# only number members ever see — so it is asserted here, on the real thread.
GARY_SIGNAL = "Gold Buy Now @ 4594 - 4588\n\nSl: 4584\n\nTP: 50/100Pips"
GARY_RESULT = "+70Pips making profit again.\n\nToday 4/4 winning setup. Good job everyone"


async def test_the_announced_seventy_pips_is_booked_as_seven_hundred(
    session, thai_units, monkeypatch
):
    """What the group posted, read the way the group reads it."""
    from app.db.models import SignalResult
    from app.processor.message_processor import ingest_message

    monkeypatch.setattr(settings, "result_source", "message")
    created = await ingest_message(session, chat_id=-1001, message_id=70100, content=GARY_SIGNAL)
    await ingest_message(
        session, chat_id=-1001, message_id=70101, content=GARY_RESULT, reply_to_message_id=70100
    )

    assert created.signal.result == SignalResult.WIN
    assert created.signal.profit_points == pytest.approx(700)


async def test_the_seven_hundred_is_what_the_statistics_publish(session, thai_units, monkeypatch):
    """A win members can check against their own terminal."""
    from app.engine import stats_engine
    from app.processor.message_processor import ingest_message

    monkeypatch.setattr(settings, "result_source", "message")
    await ingest_message(session, chat_id=-1001, message_id=70200, content=GARY_SIGNAL)
    await ingest_message(
        session, chat_id=-1001, message_id=70201, content=GARY_RESULT, reply_to_message_id=70200
    )
    await session.commit()

    overview = await stats_engine.build_overview(session, "all")
    assert overview["total_pl_points"] == pytest.approx(700)


def test_the_repair_reports_the_number_it_was_actually_publishing(tmp_path, monkeypatch, caplog):
    """A log line written from memory would name the wrong wrong-number."""
    monkeypatch.setattr(settings, "point_size", 1.0)
    monkeypatch.setattr(settings, "pip_size", 0.1)
    with caplog.at_level("WARNING"):
        units.migrate_env(_env(tmp_path, "POINT_SIZE=1.0\n"))
    assert "+7 rather than +700" in caplog.text
