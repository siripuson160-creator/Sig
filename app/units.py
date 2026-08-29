"""How a pip figure the source posts becomes a number members recognise.

Thai desks quote gold in pips and read them at **ten points to the pip**: a
source that posts "+70Pips" is understood by its members as **+700**, and that
is the number the dashboard has to show. Anything else and the statistics
disagree with the room they came from.

Two settings encode that between them — ``PIP_SIZE`` (what one pip is worth in
price, $0.10) over ``POINT_SIZE`` (the unit statistics are reported in, the
MT4/MT5 point of $0.01) — and it is their *ratio* that carries the convention.
Keeping it as a pair is what makes it fragile: either value can drift on its
own and the published numbers quietly become wrong by a factor of ten, with
nothing to notice it. So the convention lives here, in one place, and is:

* the default for a new install (:data:`DEFAULT_POINT_SIZE`, :data:`DEFAULT_PIP_SIZE`),
* repaired once on an install still carrying the pre-convention pair
  (:func:`migrate_env`), and
* checked at every start, so a pair that does not hold it is said out loud
  rather than silently mis-reporting (:func:`convention_warning`).

The point of the check is that this system publishes trading results. A number
that is wrong by ten is worse than no number, because it still looks credible.
"""

from __future__ import annotations

import logging

from app.config import settings

log = logging.getLogger(__name__)

#: Points to the pip, as Thai desks count gold. "+70Pips" reads as +700.
PIP_IN_POINTS = 10.0

#: The MT4/MT5 point for a gold quote carrying two decimals.
DEFAULT_POINT_SIZE = 0.01
#: What most desks call a pip on XAUUSD.
DEFAULT_PIP_SIZE = 0.1

#: The value installs written before the convention was settled carry. Nothing
#: else writes POINT_SIZE=1.0 with no PIP_SIZE beside it, so the pair is a
#: reliable fingerprint of a stale install rather than a deliberate choice.
_STALE_POINT_SIZE = 1.0


def points_per_pip() -> float:
    """What one pip is worth in the units the statistics are published in."""
    point = settings.point_size or DEFAULT_POINT_SIZE
    return settings.pip_size / point


def convention_holds() -> bool:
    # A float ratio, so compared with a tolerance rather than for equality.
    return abs(points_per_pip() - PIP_IN_POINTS) < 0.01


def convention_warning() -> str | None:
    """A sentence naming what the configured pair actually does, or None.

    Deliberately concrete: "+70Pips reads as +7" is something an operator can
    check against the group in front of them, where "PIP_SIZE/POINT_SIZE is
    not 10" is a puzzle.
    """
    if convention_holds():
        return None
    reads_as = round(70 * points_per_pip(), 2)
    pretty = f"{reads_as:g}"
    return (
        f'POINT_SIZE={settings.point_size:g} with PIP_SIZE={settings.pip_size:g} '
        f'makes a posted "+70Pips" read as +{pretty}, not +700. '
        f"Thai desks count ten points to the pip; set POINT_SIZE={DEFAULT_POINT_SIZE:g} "
        f"and PIP_SIZE={DEFAULT_PIP_SIZE:g} to publish the number members expect."
    )


def _is_stale(stored: dict[str, str]) -> bool:
    """True for the pre-convention pair, and only that pair."""
    if stored.get("PIP_SIZE", "").strip():
        return False        # a deliberate pip size; leave the operator's choice
    raw = stored.get("POINT_SIZE", "").strip()
    if not raw:
        return False        # unset means the defaults apply, which are correct
    try:
        return float(raw) == _STALE_POINT_SIZE
    except ValueError:
        return False


def migrate_env(path: str) -> list[str]:
    """Repair a stale install, once, and return the keys that moved.

    Narrow on purpose. It fires only on POINT_SIZE=1.0 with PIP_SIZE absent —
    exactly what installs written before the convention carry — so an operator
    who has deliberately chosen a unit keeps it. Setting PIP_SIZE to anything
    at all opts out for good.

    Both the file and the settings in force are updated, so the repair applies
    to this run rather than waiting for the next restart.
    """
    from app.setup_wizard import read_env, update_env_value

    try:
        stored = read_env(path)
    except OSError:
        return []
    if not _is_stale(stored):
        return []

    # Read what it was publishing before changing it, so the log says the
    # number that was actually wrong rather than one written from memory.
    was = round(70 * points_per_pip(), 2)

    update_env_value(path, "POINT_SIZE", f"{DEFAULT_POINT_SIZE:g}")
    update_env_value(path, "PIP_SIZE", f"{DEFAULT_PIP_SIZE:g}")
    settings.point_size = DEFAULT_POINT_SIZE
    settings.pip_size = DEFAULT_PIP_SIZE
    log.warning(
        'POINT_SIZE was %g, which published a posted "+70Pips" as +%g rather than '
        "+700; set to %g with PIP_SIZE %g so statistics come out in the points Thai "
        "desks count in",
        _STALE_POINT_SIZE,
        was,
        DEFAULT_POINT_SIZE,
        DEFAULT_PIP_SIZE,
    )
    return ["POINT_SIZE", "PIP_SIZE"]


def check_on_start(env_path: str) -> None:
    """Repair what can be repaired, and say what cannot."""
    migrate_env(env_path)
    warning = convention_warning()
    if warning:
        log.warning("%s", warning)
    else:
        log.info(
            "publishing in points of %g; one pip is %g points",
            settings.point_size,
            points_per_pip(),
        )
