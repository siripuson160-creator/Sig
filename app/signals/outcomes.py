"""Reading the result a signal source announces about its own trade.

A desk that posts signals usually reports how they went, as replies to the
original message:

    "+50Pips now, make a good profit."
    "90Pips ! Can secure as TP2 now guys."
    "SL hit, next one soon"

This module turns that wording into a verdict. It is deliberately separate from
`result_engine`, which reaches a verdict from price history, because the two are
different kinds of claim and must never be confused for one another:

* the result engine **measures** — it can be checked against the market;
* this module **repeats** — it is only as honest as the source.

Which one decides a published number is a deployment choice (`RESULT_SOURCE`).
Whichever is used, the signal records it in `result_source` so a reader is never
left guessing whether a figure was verified or merely quoted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import settings
from app.signals import patterns as P

log = logging.getLogger(__name__)


@dataclass
class ClaimedOutcome:
    """What a follow-up message says happened. Claims, not measurements."""

    tp_hit: int | None = None
    sl_hit: bool = False
    breakeven: bool = False
    closed: bool = False
    cancelled: bool = False
    #: The profit the source announced, in its own pips ("90Pips" -> 90.0).
    claimed_pips: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when the message says nothing about how the trade went."""
        return not (
            self.tp_hit or self.sl_hit or self.breakeven or self.closed or self.cancelled
        ) and self.claimed_pips is None

    @property
    def decides_the_trade(self) -> bool:
        """True when this ends the trade, rather than just reporting progress.

        "+50 pips now" is progress and must not close anything; "TP2 hit" and
        "SL hit" are verdicts.
        """
        return bool(self.tp_hit or self.sl_hit or self.cancelled or self.closed)

    def claimed_points(self) -> float | None:
        """The announced profit converted into the units the statistics use."""
        if self.claimed_pips is None:
            return None
        points = self.claimed_pips * settings.pip_size
        if settings.point_size:
            points /= settings.point_size
        return round(points, 4)


def _tp_index(text: str) -> int | None:
    match = P.TP_CLAIM_RE.search(text)
    if match is None:
        return None
    raw = match.group("index_after") or match.group("index_before")
    # "TP hit" with no number is still a target reached; treat it as the first.
    return int(raw) if raw else 1


def parse_outcome(text: str) -> ClaimedOutcome:
    """Read the outcome a follow-up message claims.

    Returns an empty outcome rather than None when nothing is claimed, so
    callers can check `is_empty` without a null check.
    """
    outcome = ClaimedOutcome()
    if not text or not text.strip():
        return outcome

    cleaned = P.normalize(text)

    # Order matters: a cancellation overrides everything, and a stop overrides
    # a target, because "TP1 hit then SL" is a stop-out on the remainder.
    outcome.cancelled = bool(P.CANCEL_CLAIM_RE.search(cleaned))
    outcome.sl_hit = bool(P.SL_CLAIM_RE.search(cleaned))
    outcome.breakeven = bool(P.BREAKEVEN_CLAIM_RE.search(cleaned))
    outcome.closed = bool(P.CLOSE_CLAIM_RE.search(cleaned))
    outcome.tp_hit = _tp_index(cleaned)

    pips = P.CLAIMED_PIPS_RE.search(cleaned)
    if pips is not None:
        value = P.to_number(pips.group("value"))
        if value is not None:
            # A minus sign is the source reporting a loss.
            outcome.claimed_pips = -value if pips.group("sign") == "-" else value

    return outcome


def describe(outcome: ClaimedOutcome) -> str:
    """A short human summary, for the evaluation note and the audit log."""
    parts: list[str] = []
    if outcome.cancelled:
        parts.append("cancelled by the source")
    if outcome.sl_hit:
        parts.append("stop loss reported")
    if outcome.tp_hit:
        parts.append(f"TP{outcome.tp_hit} reported")
    if outcome.breakeven:
        parts.append("moved to breakeven")
    if outcome.closed:
        parts.append("closed by the source")
    if outcome.claimed_pips is not None:
        parts.append(f"{outcome.claimed_pips:+g} pips announced")
    return "; ".join(parts) if parts else "no outcome claimed"
