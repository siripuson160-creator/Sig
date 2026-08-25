"""Parser tests, driven by the examples in the project brief (section 13)."""

from __future__ import annotations

import pytest

from app.signals.parser import is_management_message, parse_signal


def test_multiline_buy_signal():
    parsed = parse_signal("BUY GOLD 3340\nSL 3330\nTP1 3350\nTP2 3360")
    assert parsed is not None
    assert (parsed.direction, parsed.symbol, parsed.entry) == ("BUY", "XAUUSD", 3340)
    assert (parsed.sl, parsed.tp1, parsed.tp2) == (3330, 3350, 3360)
    assert parsed.is_complete
    assert parsed.confidence == 1.0


def test_multiline_sell_signal():
    parsed = parse_signal("SELL GOLD 3340\nSL 3350\nTP1 3330\nTP2 3320")
    assert parsed is not None
    assert (parsed.direction, parsed.entry, parsed.sl) == ("SELL", 3340, 3350)
    assert parsed.tps == [3330, 3320]


def test_single_line_signal():
    parsed = parse_signal("SELL GOLD 3340 SL 3350 TP1 3330 TP2 3320")
    assert parsed is not None
    assert (parsed.direction, parsed.entry, parsed.sl, parsed.tp1, parsed.tp2) == ("SELL", 3340, 3350, 3330, 3320)
    assert parsed.is_complete


def test_incomplete_signal_is_still_parsed():
    """"Sell now" has no levels yet but must not be discarded (section 14)."""
    parsed = parse_signal("Sell now")
    assert parsed is not None
    assert parsed.direction == "SELL"
    assert parsed.entry is None
    assert not parsed.is_complete


def test_non_signal_text_is_not_a_signal():
    assert parse_signal("Good morning") is None
    assert parse_signal("") is None
    assert parse_signal("see you tomorrow") is None


@pytest.mark.parametrize(
    "text",
    [
        "BUY XAUUSD @3340 S/L 3330 T/P1 3350 T/P2 3360",
        "buy gold entry 3340 stop loss 3330 take profit 1 3350 take profit 2 3360",
        "🔥 BUY GOLD 3340\n🛑 SL: 3330\n✅ TP1: 3350\n✅ TP2: 3360",
        "BUY GOLD 3,340.00 SL 3,330.00 TP 3350 3360",
    ],
)
def test_layout_variants_all_parse(text):
    parsed = parse_signal(text)
    assert parsed is not None and parsed.is_complete
    assert parsed.direction == "BUY"
    assert parsed.entry == 3340
    assert parsed.sl == 3330
    assert parsed.tps[:2] == [3350, 3360]


def test_entry_zone_uses_midpoint():
    parsed = parse_signal("BUY GOLD 3340-3342 SL 3330 TP1 3350")
    assert parsed is not None
    assert parsed.entry == 3341
    assert any("zone" in note for note in parsed.notes)


def test_three_take_profits():
    parsed = parse_signal("SELL GOLD 3340 SL 3350 TP1 3330 TP2 3320 TP3 3310")
    assert parsed is not None
    assert parsed.tps == [3330, 3320, 3310]
    assert parsed.tp3 == 3310


def test_unlabelled_tp_ladder():
    parsed = parse_signal("SELL GOLD 3340 SL 3350 TP 3330 3320 3310")
    assert parsed is not None
    assert parsed.tps == [3330, 3320, 3310]


def test_incoherent_levels_are_flagged_not_fixed():
    parsed = parse_signal("BUY GOLD 3340 SL 3350 TP1 3330")
    assert parsed is not None
    assert parsed.sl == 3350  # kept as posted
    assert parsed.confidence < 1.0
    assert any("SL is not below entry" in note for note in parsed.notes)


@pytest.mark.parametrize(
    "text", ["close half now", "TP1 hit +10 points", "move sl to be", "cancel this trade", "ปิดออเดอร์"]
)
def test_management_messages_do_not_create_signals(text):
    assert is_management_message(text)
    assert parse_signal(text) is None


def test_full_setup_is_not_treated_as_management():
    text = "BUY GOLD 3340 SL 3330 TP1 3350 - close at TP1"
    assert not is_management_message(text)
    assert parse_signal(text) is not None


def test_symbol_defaults_when_missing():
    parsed = parse_signal("BUY 3340 SL 3330 TP1 3350")
    assert parsed is not None
    assert parsed.symbol == "XAUUSD"
    assert any("assumed" in note for note in parsed.notes)
