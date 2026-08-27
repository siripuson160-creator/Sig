"""Trading-signal parser.

The parser is a small registry of independent strategies. ``parse_signal``
tries each registered strategy in priority order and keeps the most confident
result. To support a new message layout, write a class with a ``parse`` method
and decorate it with ``@register(priority=N)`` — no other file needs to change.

The parser is deliberately forgiving: an incomplete message such as
``"Sell now"`` still yields a ``ParsedSignal`` with ``is_complete=False``
(section 14) so that a later edit can fill in the levels (section 16).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

from app.config import settings
from app.signals import patterns as P

log = logging.getLogger(__name__)


@dataclass
class ParsedSignal:
    direction: str | None = None
    symbol: str | None = None
    entry: float | None = None
    sl: float | None = None
    tps: list[float] = field(default_factory=list)
    parser_name: str = ""
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def tp1(self) -> float | None:
        return self.tps[0] if len(self.tps) > 0 else None

    @property
    def tp2(self) -> float | None:
        return self.tps[1] if len(self.tps) > 1 else None

    @property
    def tp3(self) -> float | None:
        return self.tps[2] if len(self.tps) > 2 else None

    @property
    def is_complete(self) -> bool:
        """Enough information for the result engine to judge the trade."""
        return bool(self.direction and self.entry is not None and self.sl is not None and self.tps)

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "symbol": self.symbol,
            "entry": self.entry,
            "sl": self.sl,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "parser_name": self.parser_name,
            "confidence": round(self.confidence, 3),
            "is_complete": self.is_complete,
            "notes": list(self.notes),
        }


class SignalStrategy(Protocol):
    name: str

    def parse(self, text: str) -> ParsedSignal | None: ...


_REGISTRY: list[tuple[int, SignalStrategy]] = []


def register(priority: int = 100) -> Callable[[type], type]:
    """Register a strategy class. Lower priority number runs first."""

    def decorator(cls: type) -> type:
        _REGISTRY.append((priority, cls()))
        _REGISTRY.sort(key=lambda item: item[0])
        return cls

    return decorator


def registered_strategies() -> list[str]:
    return [strategy.name for _, strategy in _REGISTRY]


# --------------------------------------------------------------------- helpers
def _inside(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


def _extract_stop_loss(text: str) -> tuple[float | None, bool, list[tuple[int, int]]]:
    """Return ``(value, is_distance, spans)``.

    ``is_distance`` is True for "SL: 50 pips", where the number is a distance
    from entry rather than a price.
    """
    spans: list[tuple[int, int]] = []
    value: float | None = None
    in_pips = False
    for match in P.STOP_LOSS_RE.finditer(text):
        spans.append((match.start(), match.end()))
        if value is None:
            value = P.to_number(match.group("value"))
            in_pips = match.group("unit") is not None
    return value, in_pips, spans


def _extract_take_profits(text: str) -> tuple[list[float], bool, list[tuple[int, int]]]:
    """Return take-profit levels ordered by their index (TP1, TP2, TP3...).

    Handles ``TP1 3350`` / ``TP 3350`` / ``TP 3350 3360 3370`` and tolerates the
    same level being restated in a later line. The middle element of the result
    is True when the numbers are distances ("TP: 50/100Pips") rather than
    prices, which the caller converts once the entry is known.
    """
    indexed: dict[int, float] = {}
    unindexed: list[float] = []
    spans: list[tuple[int, int]] = []
    in_pips = False

    for match in P.TAKE_PROFIT_RE.finditer(text):
        spans.append((match.start(), match.end()))
        values = [P.to_number(v) for v in P.NUMBER_RE.findall(match.group("values"))]
        values = [v for v in values if v is not None]
        if not values:
            continue
        if match.group("unit") is not None:
            in_pips = True
        index = int(match.group("index")) if match.group("index") else None
        if index is not None:
            indexed.setdefault(index, values[0])
            # "TP1 3350 3360" — the extra numbers continue the ladder.
            for offset, extra in enumerate(values[1:], start=1):
                indexed.setdefault(index + offset, extra)
        else:
            unindexed.extend(values)

    ordered = [indexed[i] for i in sorted(indexed)]
    for value in unindexed:
        if value not in ordered:
            ordered.append(value)
    return ordered, in_pips, spans


def _from_distance(entry: float, distance: float, direction: str, *, is_target: bool) -> float:
    """Turn a pip distance into a price.

    A target is on the profitable side of entry, a stop on the losing side, so
    the direction decides the sign.
    """
    offset = abs(distance) * settings.pip_size
    profitable_up = direction == "BUY"
    if profitable_up == is_target:
        return entry + offset
    return entry - offset


def _extract_entry(text: str, direction_match, blocked: list[tuple[int, int]]) -> tuple[float | None, list[str]]:
    """Find the entry price, ignoring numbers that belong to SL/TP labels."""
    notes: list[str] = []

    # 1. An explicit "ENTRY 3340" / "@ 3340" wins.
    for match in P.ENTRY_RE.finditer(text):
        if _inside(match.start("value"), blocked):
            continue
        value = P.to_number(match.group("value"))
        if value is not None:
            return value, notes

    # 2. Otherwise the first number attached to the direction word.
    if direction_match is not None:
        tail = text[direction_match.start() :]
        de = P.DIRECTION_ENTRY_RE.search(tail)
        if de is not None:
            absolute = direction_match.start() + de.start("value")
            if not _inside(absolute, blocked):
                value = P.to_number(de.group("value"))
                # An entry zone ("3340-3342") is reduced to its midpoint.
                zone = P.RANGE_RE.match(tail[de.start("value") :])
                if zone is not None:
                    low, high = P.to_number(zone.group("low")), P.to_number(zone.group("high"))
                    if low is not None and high is not None and abs(high - low) < max(low, high) * 0.02:
                        notes.append(f"entry zone {low}-{high}; midpoint used")
                        return (low + high) / 2, notes
                if value is not None:
                    return value, notes

    # 3. Last resort: the first free-standing number in the message.
    for match in P.NUMBER_RE.finditer(text):
        if _inside(match.start(), blocked):
            continue
        value = P.to_number(match.group(0))
        if value is not None:
            notes.append("entry inferred from first unlabelled number")
            return value, notes

    return None, notes


#: A target or stop further than this from entry is not a price for this trade.
#: Deliberately generous — it is there to catch a misread (a "50" that was a
#: pip distance, gold "taking profit" at 100), not to judge a wide stop.
IMPLAUSIBLE_DISTANCE = 0.25


def _drop_implausible_levels(parsed: ParsedSignal) -> list[str]:
    """Discard levels that cannot be prices for this trade.

    Publishing a number worked out from a misread level is worse than
    publishing nothing: it puts a fabricated result on the dashboard. Dropping
    the level leaves the signal incomplete, so it stays PENDING and is visible
    as something to correct by hand.
    """
    notes: list[str] = []
    if parsed.entry is None or parsed.entry <= 0:
        return notes

    def too_far(level: float) -> bool:
        return abs(level - parsed.entry) / parsed.entry > IMPLAUSIBLE_DISTANCE

    if parsed.sl is not None and too_far(parsed.sl):
        notes.append(f"SL {parsed.sl:g} is implausible against entry {parsed.entry:g}; ignored")
        parsed.sl = None

    kept = []
    for index, tp in enumerate(parsed.tps, start=1):
        if too_far(tp):
            notes.append(f"TP{index} {tp:g} is implausible against entry {parsed.entry:g}; ignored")
        else:
            kept.append(tp)
    parsed.tps = kept
    return notes


def _coherence_notes(parsed: ParsedSignal) -> list[str]:
    """Flag levels that contradict the direction instead of silently fixing them."""
    notes: list[str] = []
    if not parsed.direction or parsed.entry is None:
        return notes
    entry = parsed.entry
    if parsed.direction == "BUY":
        if parsed.sl is not None and parsed.sl >= entry:
            notes.append("SL is not below entry for a BUY")
        for i, tp in enumerate(parsed.tps, start=1):
            if tp <= entry:
                notes.append(f"TP{i} is not above entry for a BUY")
    else:
        if parsed.sl is not None and parsed.sl <= entry:
            notes.append("SL is not above entry for a SELL")
        for i, tp in enumerate(parsed.tps, start=1):
            if tp >= entry:
                notes.append(f"TP{i} is not below entry for a SELL")
    return notes


def _score(parsed: ParsedSignal) -> float:
    score = 0.0
    if parsed.direction:
        score += 0.30
    if parsed.entry is not None:
        score += 0.25
    if parsed.sl is not None:
        score += 0.20
    if parsed.tps:
        score += 0.25
    if parsed.notes:
        score -= 0.10 * len(parsed.notes)
    return max(0.0, min(1.0, score))


# ------------------------------------------------------------------ strategies
@register(priority=10)
class LabelledLevelsStrategy:
    """The common case: a direction plus labelled SL / TP levels.

    Works for multi-line and single-line messages alike because the levels are
    located by their labels, not by their position.
    """

    name = "labelled_levels"

    def parse(self, text: str) -> ParsedSignal | None:
        upper = text.upper()
        direction_match = P.DIRECTION_RE.search(upper)
        sl, sl_in_pips, sl_spans = _extract_stop_loss(upper)
        tps, tps_in_pips, tp_spans = _extract_take_profits(upper)

        if direction_match is None and sl is None and not tps:
            return None

        parsed = ParsedSignal(parser_name=self.name)
        if direction_match is not None:
            parsed.direction = P.direction_of(direction_match.group("dir"))
            # A second, opposite direction word means the message is unclear.
            others = {
                P.direction_of(m.group("dir"))
                for m in P.DIRECTION_RE.finditer(upper)
                if P.direction_of(m.group("dir")) is not None
            }
            if len(others) > 1:
                parsed.notes.append("message contains both BUY and SELL wording")

        symbol_match = P.SYMBOL_RE.search(text)
        parsed.symbol = P.canonical_symbol(symbol_match.group(1)) if symbol_match else None

        parsed.sl = sl
        parsed.tps = tps
        parsed.entry, entry_notes = _extract_entry(upper, direction_match, sl_spans + tp_spans)
        parsed.notes.extend(entry_notes)

        # Distances only become prices once the entry and direction are known.
        if parsed.entry is not None and parsed.direction:
            if tps_in_pips and tps:
                parsed.tps = [
                    _from_distance(parsed.entry, tp, parsed.direction, is_target=True) for tp in tps
                ]
                pretty = "/".join(f"{tp:g}" for tp in tps)
                parsed.notes.append(
                    f"TP quoted in pips ({pretty}); converted at {settings.pip_size:g} per pip"
                )
            if sl_in_pips and sl is not None:
                parsed.sl = _from_distance(parsed.entry, sl, parsed.direction, is_target=False)
                parsed.notes.append(
                    f"SL quoted in pips ({sl:g}); converted at {settings.pip_size:g} per pip"
                )
        elif tps_in_pips or sl_in_pips:
            # Nothing to measure the distance from, so publishing a level would
            # be inventing one.
            parsed.notes.append("levels quoted in pips but no entry to measure from")
            if tps_in_pips:
                parsed.tps = []
            if sl_in_pips:
                parsed.sl = None

        parsed.notes.extend(_drop_implausible_levels(parsed))
        parsed.notes.extend(_coherence_notes(parsed))
        parsed.confidence = _score(parsed)
        return parsed


@register(priority=50)
class DirectionOnlyStrategy:
    """``"Sell now"`` — a heads-up with no levels yet (section 14)."""

    name = "direction_only"

    def parse(self, text: str) -> ParsedSignal | None:
        match = P.DIRECTION_RE.search(text.upper())
        if match is None:
            return None
        parsed = ParsedSignal(parser_name=self.name)
        parsed.direction = P.direction_of(match.group("dir"))
        symbol_match = P.SYMBOL_RE.search(text)
        parsed.symbol = P.canonical_symbol(symbol_match.group(1)) if symbol_match else None
        parsed.notes.append("no entry/SL/TP in message yet")
        parsed.confidence = _score(parsed)
        return parsed


# --------------------------------------------------------------------- entry pt
def is_management_message(text: str) -> bool:
    """True for "close half", "TP1 hit", "cancel" and friends.

    Such a message is still forwarded to LINE, it just must not become a signal.
    A message that carries a full setup is never treated as management, because
    groups often write "BUY GOLD 3340 ... close at TP1".
    """
    if not text:
        return False
    if P.MANAGEMENT_RE.search(text) is None:
        return False
    probe = parse_signal(text, allow_management=True)
    return probe is None or not probe.is_complete


def parse_signal(text: str, *, allow_management: bool = False) -> ParsedSignal | None:
    """Parse ``text`` into a :class:`ParsedSignal`, or ``None`` if it is not one."""
    if not text or not text.strip():
        return None

    normalized = P.normalize(text)
    if not allow_management and is_management_message(normalized):
        return None

    best: ParsedSignal | None = None
    for _, strategy in _REGISTRY:
        try:
            candidate = strategy.parse(normalized)
        except Exception:  # pragma: no cover - a broken pattern must not stop delivery
            log.exception("signal strategy %s failed", getattr(strategy, "name", strategy))
            continue
        if candidate is None:
            continue
        if best is None or candidate.confidence > best.confidence:
            best = candidate
        if best.confidence >= 1.0:
            break

    if best is None or best.direction is None:
        # Levels without a direction are not actionable.
        return None

    if best.symbol is None:
        best.symbol = settings.price_symbol
        best.notes.append(f"symbol not stated; assumed {settings.price_symbol}")
    return best


def describe_parsers() -> list[dict]:
    """Used by the dashboard methodology page."""
    return [
        {"name": strategy.name, "priority": priority, "doc": (strategy.__doc__ or "").strip()}
        for priority, strategy in _REGISTRY
    ]


__all__ = [
    "ParsedSignal",
    "parse_signal",
    "is_management_message",
    "register",
    "registered_strategies",
    "describe_parsers",
]
