import curses
import logging

import pytest

from tests.builders import config, data, devices
from tests.tui.conftest import (
    BOILER,
    REVIEW,
    SECRETS,
    Call,
    FakeWindow,
    make_cache,
)
from tydomio.state.cache import Cache
from tydomio.tui.logs import LogBuffer
from tydomio.tui.model import Ui
from tydomio.tui.render import TOO_SMALL, draw

ENTER = 10
BIDI = chr(0x202E)


def log(logs: LogBuffer, message: str) -> None:
    logs.handle(logging.LogRecord("t", logging.INFO, "f", 1, message, None, None))


def drawn(ui: Ui, logs: LogBuffer, size: tuple[int, int] = (30, 100)) -> FakeWindow:
    window = FakeWindow(*size)
    draw(window, ui, logs)
    return window


def test_draws_rows_detail_logs_and_hints() -> None:
    logs = LogBuffer(SECRETS)
    log(logs, "connected")

    text = "\n".join(drawn(Ui(make_cache(), SECRETS), logs).screen_text())

    assert "Kitchen" in text
    assert "100.0 %" in text
    assert "position" in text
    assert "INFO connected" in text
    assert "connected, synced" in text
    assert "↑↓ move · Enter command · q quit" in text


def named_cache(name: str) -> Cache:
    cache = Cache()
    cache.apply_config(config({(1, 2): (name, "shutter")}, {}))
    cache.replace_devices(devices((1, 2, [data("position", 100)])), [])
    return cache


def test_item_name_with_escape_codes_is_drawn_clean() -> None:
    ui = Ui(named_cache("\x1b[2JSalon\x07"), SECRETS)

    texts = drawn(ui, LogBuffer(SECRETS)).screen_text()

    assert any("Salon" in t for t in texts)
    assert not any("\x1b" in t or "\x07" in t for t in texts)


def test_log_line_with_control_characters_is_drawn_clean() -> None:
    logs = LogBuffer(SECRETS)
    log(logs, f"bad \x1b[31m\x07{BIDI} end")

    texts = drawn(Ui(make_cache(), SECRETS), logs).screen_text()

    assert any("bad [31m end" in t for t in texts)
    assert not any(c in t for t in texts for c in f"\x1b\x07{BIDI}")


def test_a_log_line_with_a_newline_is_drawn_on_one_line() -> None:
    logs = LogBuffer(SECRETS)
    log(logs, "first\nsecond\tthird")

    texts = drawn(Ui(make_cache(), SECRETS), logs).screen_text()

    assert any(t.endswith("first second third") for t in texts)
    assert not any("\n" in t or "\t" in t for t in texts)


def test_too_small_terminal_draws_one_line() -> None:
    window = drawn(Ui(make_cache(), SECRETS), LogBuffer(SECRETS), (11, 80))

    assert [(c.y, c.x, c.text, c.attr) for c in window.calls] == [(0, 0, TOO_SMALL, 0)]


def test_text_is_cut_to_the_pane_width() -> None:
    logs = LogBuffer(SECRETS)
    log(logs, "x" * 200)

    window = drawn(Ui(make_cache(), SECRETS), logs, (30, 60))

    assert all(len(c.text) <= 60 - c.x and c.n <= 60 - c.x for c in window.calls)
    assert any("xxxxxxxxxx" in text for text in window.screen_text())


def test_nothing_is_drawn_outside_the_window() -> None:
    logs = LogBuffer(SECRETS)
    for number in range(50):
        log(logs, f"line {number}")

    window = drawn(Ui(make_cache(), SECRETS), logs, (12, 60))

    assert window.calls
    assert all(0 <= c.y < 12 and 0 <= c.x < 60 for c in window.calls)
    assert all(c.x + min(len(c.text), c.n) <= 60 for c in window.calls)


def test_the_selected_row_is_highlighted() -> None:
    ui = Ui(make_cache(), SECRETS)
    ui.rows()
    ui.handle_key(curses.KEY_DOWN)

    window = drawn(ui, LogBuffer(SECRETS))

    reversed_rows = [c for c in window.calls if c.attr == curses.A_REVERSE][:-1]
    assert [c.text.split() for c in reversed_rows] == [["Bedroom", "20.0", "%"]]
    # the whole inner width of the left pane, 40 columns of 100
    assert (reversed_rows[0].x, len(reversed_rows[0].text)) == (1, 38)
    assert reversed_rows[0].text.startswith("   Bedroom")


def test_draw_calls_rows_so_that_a_lost_connection_closes_the_form() -> None:
    cache = make_cache()
    ui = Ui(cache, SECRETS)
    ui.rows()
    ui.handle_key(ENTER)
    assert ui.mode == "form"

    cache.mark_disconnected()
    draw(FakeWindow(), ui, LogBuffer(SECRETS))

    assert ui.mode == "browse"


def test_the_form_is_drawn_in_the_right_pane_with_its_hints() -> None:
    ui = Ui(make_cache(), SECRETS)
    ui.rows()
    ui.handle_key(ENTER)

    text = "\n".join(drawn(ui, LogBuffer(SECRETS)).screen_text())

    assert "Kitchen (e:1:2)" in text
    assert "Esc cancel" in text


def test_the_confirmation_is_drawn_in_the_right_pane() -> None:
    ui = Ui(make_cache(), SECRETS)
    ui.rows()
    for key in (ENTER, ord("1"), ord("0"), *REVIEW):
        ui.handle_key(key)

    text = "\n".join(drawn(ui, LogBuffer(SECRETS)).screen_text())

    assert "Press y to send" in text


def test_the_status_is_drawn_clean() -> None:
    ui = Ui(make_cache(), SECRETS)
    ui.status = "\x1b[2Jdone\x07"

    texts = drawn(ui, LogBuffer(SECRETS)).screen_text()

    assert "[2Jdone" in texts
    assert not any("\x1b" in t or "\x07" in t for t in texts)


def test_the_last_cell_of_the_window_is_never_written() -> None:
    # FakeWindow raises like curses on a write reaching the bottom-right cell
    drawn(Ui(make_cache(), SECRETS), LogBuffer(SECRETS), (12, 60))
    drawn(Ui(make_cache(), SECRETS), LogBuffer(SECRETS), (1, 30))


def test_the_form_hint_stays_whole_at_the_minimum_width() -> None:
    ui = Ui(make_cache(), SECRETS)
    ui.rows()
    ui.handle_key(ENTER)

    last = drawn(ui, LogBuffer(SECRETS), (12, 60)).screen_text()[-1]

    assert last.startswith("↑↓ move · type or ←→ choose · Enter next · Esc cancel")


def test_the_form_is_drawn_with_its_send_row_and_hint() -> None:
    ui = Ui(make_cache(), SECRETS)
    ui.rows()
    ui.handle_key(ENTER)

    texts = drawn(ui, LogBuffer(SECRETS), (24, 80)).screen_text()

    assert "  [ Review and send ]" in texts
    assert texts[-1].startswith("↑↓ move · type or ←→ choose · Enter next · Esc cancel")


def test_a_long_form_stays_inside_a_small_window() -> None:
    ui = Ui(make_cache(), SECRETS)
    ui.rows()
    ui.handle_key(curses.KEY_DOWN)
    ui.handle_key(curses.KEY_DOWN)
    assert ui.selected == BOILER  # nine fields
    ui.handle_key(ENTER)

    window = drawn(ui, LogBuffer(SECRETS), (12, 60))

    assert all(0 <= c.y < 12 and 0 <= c.x < 60 for c in window.calls)
    assert all(c.x + min(len(c.text), c.n) <= 60 for c in window.calls)


def boiler_form() -> Ui:
    ui = Ui(make_cache(), SECRETS)
    ui.rows()
    while ui.selected != BOILER:
        ui.handle_key(curses.KEY_DOWN)
    ui.handle_key(ENTER)  # nine fields, then the send row
    return ui


FORM_SIZES = [(12, 60), (24, 80), (35, 120)]


def right_pane(window: FakeWindow) -> list[Call]:
    """Give the writes inside the form box, the full width; frames left out."""
    return [c for c in window.calls if c.x == 2 and 0 < c.y < window.size[0] - 2]


@pytest.mark.parametrize("size", FORM_SIZES, ids=str)
def test_the_target_and_the_focused_row_are_drawn_at_every_focus(
    size: tuple[int, int],
) -> None:
    ui = boiler_form()

    for _ in range(10):
        pane = right_pane(drawn(ui, LogBuffer(SECRETS), size))
        assert (pane[0].y, pane[0].text) == (1, "Boiler (e:20:1)")
        assert sum(c.text.startswith("> ") for c in pane) == 1
        ui.handle_key(curses.KEY_DOWN)


def test_the_error_line_is_drawn_in_a_short_pane() -> None:
    ui = boiler_form()
    for _ in range(9):
        ui.handle_key(curses.KEY_DOWN)
    ui.handle_key(ENTER)

    texts = [c.text for c in right_pane(drawn(ui, LogBuffer(SECRETS), (12, 60)))]

    assert texts[0] == "Boiler (e:20:1)"
    assert texts[-2:] == [
        "> [ Review and send ]",
        "nothing to send: set a value first",
    ]


@pytest.mark.parametrize("size", FORM_SIZES, ids=str)
def test_a_form_row_is_drawn_whole_never_cut_or_wrapped(size: tuple[int, int]) -> None:
    ui = boiler_form()

    for _ in range(10):
        window = drawn(ui, LogBuffer(SECRETS), size)
        texts = [c.shown for c in right_pane(window)]
        assert set(texts) <= set(ui.form_lines())
        assert ui.form_lines()[ui.form_focus] in texts
        assert all(c.x + min(len(c.text), c.n) <= size[1] for c in window.calls)
        ui.handle_key(curses.KEY_DOWN)


def test_the_form_takes_the_full_width_titled_with_the_target() -> None:
    ui = boiler_form()

    window = drawn(ui, LogBuffer(SECRETS), (24, 80))

    tops = [c.text for c in window.calls if c.y == 0]
    assert len(tops) == 1
    assert tops[0].startswith("╭ Command · Boiler ─")
    assert tops[0].endswith("╮")
    assert len(tops[0]) == 80
    assert not any("Home" in c.text or "Kitchen" in c.text for c in window.calls)


def test_the_ordinary_confirmation_hint_names_the_key() -> None:
    ui = Ui(make_cache(), SECRETS)
    ui.rows()
    for key in (ENTER, ord("1"), *REVIEW):
        ui.handle_key(key)

    last = drawn(ui, LogBuffer(SECRETS), (12, 60)).screen_text()[-1]

    assert last.startswith("y send · any other key cancels")


def heating_confirmation() -> Ui:
    ui = Ui(make_cache(), SECRETS)
    ui.rows()
    for _ in range(4):  # the area `a:7` of the heating kinds is the fifth row
        ui.handle_key(curses.KEY_DOWN)
    ui.handle_key(ENTER)
    for key in (curses.KEY_RIGHT, curses.KEY_DOWN, *map(ord, "20.5"), *REVIEW):
        ui.handle_key(key)
    for key in map(ord, "yes"):
        ui.handle_key(key)
    return ui


def test_the_heating_hint_asks_for_yes_and_escape_cancels() -> None:
    ui = heating_confirmation()
    assert ui.mode == "confirm"

    last = drawn(ui, LogBuffer(SECRETS), (12, 60)).screen_text()[-1]

    assert last.startswith("type yes then Enter to send · Esc cancels")


def test_the_heating_warning_and_typed_word_stay_visible_in_a_short_pane() -> None:
    ui = heating_confirmation()
    logs = LogBuffer(SECRETS)
    log(logs, "a log line")

    texts = drawn(ui, logs, (12, 60)).screen_text()

    assert "Some modes and low setpoints can leave the home" in " ".join(texts)
    assert "unheated." in " ".join(texts)
    assert "> yes" in texts
    assert "a log line" not in " ".join(texts)


def test_the_confirmation_keeps_its_target_warning_prompt_and_typed_word() -> None:
    ui = Ui(make_cache(), SECRETS)
    ui.rows()
    for _ in range(4):
        ui.handle_key(curses.KEY_DOWN)
    ui.handle_key(ENTER)
    # an enum field takes the first member, a number takes 1: five values in all
    for _ in range(5):
        for key in (curses.KEY_RIGHT, ord("1"), curses.KEY_DOWN):
            ui.handle_key(key)
    for key in REVIEW:
        ui.handle_key(key)
    for key in map(ord, "yes"):
        ui.handle_key(key)

    text = " ".join(drawn(ui, LogBuffer(SECRETS), (12, 60)).screen_text())

    assert "Send to Living (a:7)" in text
    assert "can leave the home" in text
    assert "unheated." in text
    assert "Type yes then Enter" in text
    assert "> yes" in text


def call_with(window: FakeWindow, text: str) -> Call:
    return next(c for c in window.calls if text in c.text)


def test_an_item_row_has_its_label_left_and_value_at_the_pane_edge() -> None:
    window = drawn(Ui(make_cache(), SECRETS), LogBuffer(SECRETS))

    row = call_with(window, "  Kitchen")

    # the left pane is 40 of 100 columns: inside, a margin at 1 and 38
    assert row.x == 1
    assert row.text.startswith("   Kitchen  ")
    assert row.text.endswith("100.0 % ")
    assert row.x + len(row.text) == 39


def test_a_header_is_bold_and_upper_cased() -> None:
    window = drawn(Ui(make_cache(), SECRETS), LogBuffer(SECRETS))

    assert call_with(window, "SHUTTER").attr == curses.A_BOLD


def test_the_panes_are_framed_with_their_titles() -> None:
    window = drawn(Ui(make_cache(), SECRETS), LogBuffer(SECRETS))

    tops = [c.text for c in window.calls if c.y == 0]
    bottoms = [c.text for c in window.calls if c.text.startswith("╰")]
    assert tops[0].startswith("╭ Home ─")
    assert tops[1].startswith("╭ Kitchen · shutter ─")
    assert all(top.endswith("╮") for top in tops)
    assert all(bottom.endswith("╯") for bottom in bottoms)
    assert [len(top) for top in tops] == [len(b) for b in bottoms] == [40, 60]


def test_the_form_pane_is_titled_command() -> None:
    ui = Ui(make_cache(), SECRETS)
    ui.handle_key(ENTER)

    tops = [c.text for c in drawn(ui, LogBuffer(SECRETS)).calls if c.y == 0]

    assert tops[0].startswith("╭ Command · Kitchen ─")


def test_a_detail_value_is_at_the_pane_edge_and_its_description_dimmed() -> None:
    window = drawn(Ui(make_cache(), SECRETS), LogBuffer(SECRETS))

    value = call_with(window, "position")
    description = call_with(window, "0 = closed")

    # the right pane is 60 of 100 columns: inside, a margin at 41 and 98
    assert (value.x, value.x + len(value.text)) == (42, 98)
    assert value.text.endswith("100.0 %")
    assert description.attr == curses.A_DIM


def test_the_log_pane_shows_the_last_four_lines_dimmed() -> None:
    logs = LogBuffer(SECRETS)
    for number in range(6):
        log(logs, f"line {number}")

    window = drawn(Ui(make_cache(), SECRETS), logs)

    shown = [c for c in window.calls if "INFO line" in c.text]
    assert [c.text[-6:] for c in shown] == [f"line {n}" for n in range(2, 6)]
    assert {c.attr for c in shown} == {curses.A_DIM}


def test_whitespace_inside_a_log_message_is_collapsed() -> None:
    logs = LogBuffer(SECRETS)
    log(logs, "a  b")

    texts = drawn(Ui(make_cache(), SECRETS), logs).screen_text()

    assert any(t.endswith("INFO a b") for t in texts)


def test_whitespace_inside_a_tydom_name_is_collapsed() -> None:
    texts = drawn(Ui(named_cache("Salon  droite"), SECRETS), LogBuffer(SECRETS))

    assert "Salon droite" in call_with(texts, "Salon").text


def test_the_last_line_has_the_hint_left_and_the_connection_right() -> None:
    ui = Ui(make_cache(), SECRETS)

    last = drawn(ui, LogBuffer(SECRETS), (30, 120)).calls[-1]

    assert last.text.startswith("↑↓ move · Enter command · q quit  ")
    assert last.text.endswith(ui.connection_line())
    assert (last.x, len(last.text), last.attr) == (0, 119, curses.A_REVERSE)


def long_cache() -> Cache:
    cache = Cache()
    endpoints = {(1, n): (f"Shutter {n}", "shutter") for n in range(40)}
    cache.apply_config(config(endpoints))
    cache.replace_devices(
        devices(*((1, n, [data("position", n)]) for n in range(40))), []
    )
    return cache


@pytest.mark.parametrize("size", [(12, 60), (24, 80), (35, 120)])
def test_a_long_list_and_many_logs_stay_inside_the_window(
    size: tuple[int, int],
) -> None:
    height, width = size
    logs = LogBuffer(SECRETS)
    for number in range(50):
        log(logs, f"line {number} " * 20)
    ui = Ui(long_cache(), SECRETS)
    for _ in range(39):
        ui.handle_key(curses.KEY_DOWN)

    # FakeWindow raises like curses on a write reaching the bottom-right cell
    window = drawn(ui, logs, size)

    assert all(0 <= c.y < height and 0 <= c.x < width for c in window.calls)
    assert all(c.x + min(len(c.text), c.n) <= width for c in window.calls)
    assert "Shutter 39" in call_with(window, "Shutter 39").text
