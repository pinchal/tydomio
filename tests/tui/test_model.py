import curses
from datetime import UTC, datetime

import pytest

from tests.builders import config, data, devices
from tests.tui.conftest import (
    BOILER,
    ENTER,
    ESC,
    REVIEW,
    SECRETS,
    SHUTTER,
    make_cache,
)
from tydomio.model.data import Data
from tydomio.scrub import Secrets
from tydomio.state.cache import Cache
from tydomio.state.writer import NotSent, Sent
from tydomio.tui.model import DetailLine, Row, Submission, Ui

LEFT = "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}"
RIGHT = "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"


def press(ui: Ui, *keys: int | str) -> Submission | None:
    """Press each key, a str standing for its characters; give the last result."""
    result = None
    for key in keys:
        codes = [ord(c) for c in key] if isinstance(key, str) else [key]
        for code in codes:
            result = ui.handle_key(code)
    return result


def select(ui: Ui, item_id: str) -> None:
    ids = [row.id for row in ui.rows() if row.id]
    for _ in range(ids.index(item_id)):
        press(ui, curses.KEY_DOWN)


def test_rows_group_items_by_usage_in_config_order() -> None:
    ui = Ui(make_cache(), SECRETS)

    assert ui.rows() == [
        Row("", "shutter", ""),
        Row("e:1:2", "Kitchen", "100.0 %"),
        Row("e:1:3", "Bedroom", "20.0 %"),
        Row("", "boiler", ""),
        Row("e:20:1", "Boiler", "19.5 degC"),
        Row("", "alarm", ""),
        Row("e:30:1", "Alarm", ""),
        Row("", "area", ""),
        Row("a:7", "Living", "48.0 %"),
    ]


def test_selection_skips_headers_and_clamps() -> None:
    cache = make_cache()
    ui = Ui(cache, SECRETS)
    press(ui, curses.KEY_UP, curses.KEY_DOWN, curses.KEY_DOWN)
    assert ui.detail()[0] == DetailLine("Boiler · boiler")

    cache.apply_config(config({(1, 2): ("Kitchen", "shutter")}))
    press(ui, curses.KEY_DOWN)

    assert ui.detail()[0] == DetailLine("Kitchen · shutter")


def test_item_without_state_shows_name_and_kind_only() -> None:
    ui = Ui(make_cache(), SECRETS)
    select(ui, "e:30:1")

    assert ui.detail() == [DetailLine("Alarm · alarm")]


def test_detail_shows_unit_description_and_validity() -> None:
    ui = Ui(make_cache(), SECRETS)
    select(ui, "e:1:3")

    assert ui.detail() == [
        DetailLine("Bedroom · shutter"),
        DetailLine("position", "20.0 % (expired)"),
        DetailLine("0 = closed, 100 = open", dim=True),
    ]


def test_detail_shows_the_tydom_error() -> None:
    cache = make_cache()
    cache.replace_devices(devices((1, 2, [data("position", 100)]), error=3), [])
    ui = Ui(cache, SECRETS)

    assert ui.detail()[-1] == DetailLine("tydom_error", "3")


def test_invalid_state_is_shown_without_value() -> None:
    cache = make_cache()
    cache.replace_devices(devices(), [(1, 2)])
    ui = Ui(cache, SECRETS)

    assert ui.rows()[1] == Row("e:1:2", "Kitchen", "invalid_state")
    assert ui.detail() == [DetailLine("Kitchen · shutter"), DetailLine("invalid_state")]


def one_item_ui(usage: str, *fields: Data) -> Ui:
    """Build a Ui over a fresh cache with one endpoint `e:1:1` of the usage."""
    cache = Cache()
    cache.apply_config(config({(1, 1): ("Item", usage)}))
    cache.replace_devices(devices((1, 1, list(fields))), [])
    return Ui(cache, SECRETS)


def test_the_short_value_is_the_first_field_with_a_unit() -> None:
    ui = one_item_ui(
        "boiler", data("authorization", "HEATING"), data("temperature", 21.17)
    )

    assert ui.rows()[1] == Row("e:1:1", "Item", "21.17 degC")


def test_the_short_value_skips_booleans_without_a_unit() -> None:
    # a shutter has no unit field but `position`; an unknown field comes last
    ui = one_item_ui("shutter", data("battDefect", True), data("motorMode", "AUTO"))

    assert ui.rows()[1] == Row("e:1:1", "Item", "AUTO")


def test_an_item_with_only_booleans_has_no_short_value() -> None:
    ui = one_item_ui("shutter", data("battDefect", False), data("upDefect", True))

    assert ui.rows()[1] == Row("e:1:1", "Item", "")


def test_detail_lists_unit_fields_then_others_then_collapses_false_flags() -> None:
    ui = one_item_ui(
        "boiler",
        data("loadSheddingOn", False),
        data("productionDefect", False),
        data("authorization", "HEATING"),
        data("temperature", 21.17),
    )

    assert ui.detail() == [
        DetailLine("Item · boiler"),
        DetailLine("temperature", "21.17 degC"),
        DetailLine("authorization", "HEATING"),
        DetailLine("no alert (2 flags off)"),
    ]


def test_a_true_flag_is_an_alert_before_the_flags_off() -> None:
    ui = one_item_ui(
        "shutter",
        data("battDefect", False),
        data("obstacleDefect", True),
        data("upDefect", False),
    )

    assert ui.detail() == [
        DetailLine("Item · shutter"),
        DetailLine("⚠ obstacleDefect"),
        DetailLine("2 flags off"),
    ]


def test_a_false_flag_not_up_to_date_keeps_its_line() -> None:
    ui = one_item_ui(
        "shutter", data("battDefect", False), data("upDefect", False, "expired")
    )

    assert ui.detail() == [
        DetailLine("Item · shutter"),
        DetailLine("upDefect", "False (expired)"),
        DetailLine("no alert (1 flag off)"),
    ]


def test_empty_installation_has_no_rows() -> None:
    ui = Ui(Cache(), SECRETS)

    assert ui.rows() == []
    assert ui.detail() == []
    assert ui.handle_key(ENTER) is None
    assert ui.mode == "browse"


def test_enter_on_non_writable_does_nothing() -> None:
    ui = Ui(make_cache(), SECRETS)
    select(ui, "e:30:1")

    press(ui, ENTER)

    assert ui.mode == "browse"


def test_enter_when_not_fresh_does_nothing() -> None:
    cache = make_cache()
    cache.mark_disconnected()
    ui = Ui(cache, SECRETS)

    press(ui, ENTER)

    assert ui.mode == "browse"


def test_q_quits() -> None:
    ui = Ui(make_cache(), SECRETS)

    press(ui, "q")

    assert ui.quit


def shown_value(ui: Ui, name: str) -> str:
    """Give the value shown on the form line of the field, cursor included."""
    words = next(w for line in ui.form_lines() if name in (w := line.split()))
    return words[words.index(name) + 2]


def test_form_fields_come_from_the_write_model() -> None:
    ui = Ui(make_cache(), SECRETS)
    press(ui, ENTER, curses.KEY_DOWN)
    members = []
    for _ in range(8):
        press(ui, curses.KEY_RIGHT)
        members.append(shown_value(ui, "positionCmd"))

    assert shown_value(ui, "position") == "unset"
    assert members == [
        "DOWN",
        "DOWN_SLOW",
        "FAVORIT1",
        "FAVORIT2",
        "STOP",
        "UP",
        "UP_SLOW",
        "unset",
    ]


def test_left_cycles_an_enum_backwards() -> None:
    ui = Ui(make_cache(), SECRETS)

    press(ui, ENTER, curses.KEY_DOWN, curses.KEY_LEFT)

    assert shown_value(ui, "positionCmd") == "UP_SLOW"


def test_numeric_field_is_edited_with_digits_and_backspace() -> None:
    ui = Ui(make_cache(), SECRETS)

    press(ui, ENTER, "5x0", curses.KEY_LEFT, "7", 127, 8, curses.KEY_BACKSPACE, "4")

    assert shown_value(ui, "position") == "4_"


def test_unparsable_number_blocks_the_confirmation() -> None:
    ui = Ui(make_cache(), SECRETS)

    press(ui, ENTER, "5.5", *REVIEW)

    assert ui.mode == "form"
    assert ui.form_lines()[-1] == "position: not an integer"


def focused(ui: Ui) -> str:
    """Give the focused row of the form without its marker."""
    return next(line[2:] for line in ui.form_lines() if line.startswith("> "))


def test_enter_on_a_field_moves_to_the_next_row() -> None:
    ui = Ui(make_cache(), SECRETS)

    press(ui, ENTER, ENTER)
    second = focused(ui)
    press(ui, ENTER)

    assert second.startswith("positionCmd ")
    assert focused(ui) == "[ Review and send ]"
    assert ui.mode == "form"


def test_enter_on_the_send_row_without_a_value_stays_in_the_form() -> None:
    ui = Ui(make_cache(), SECRETS)

    result = press(ui, ENTER, ENTER, ENTER, ENTER)

    assert result is None
    assert ui.mode == "form"
    assert ui.form_lines()[-1] == "nothing to send: set a value first"


def test_enter_on_the_send_row_with_a_value_goes_to_the_confirmation() -> None:
    ui = Ui(make_cache(), SECRETS)

    result = press(ui, ENTER, "50", ENTER, ENTER, ENTER)

    assert result is None
    assert ui.mode == "confirm"


def test_up_and_down_reach_the_send_row_and_clamp() -> None:
    ui = Ui(make_cache(), SECRETS)
    press(ui, ENTER, *(curses.KEY_DOWN,) * 3)
    last = focused(ui)

    press(ui, *(curses.KEY_UP,) * 3)

    assert last == "[ Review and send ]"
    assert focused(ui).startswith("position ")


def test_editing_keys_on_the_send_row_do_nothing() -> None:
    ui = Ui(make_cache(), SECRETS)
    press(ui, ENTER, curses.KEY_DOWN, curses.KEY_DOWN)
    before = ui.form_lines()

    press(ui, "5", curses.KEY_RIGHT, 127)

    assert ui.form_lines() == before


def test_the_form_lines_show_the_target_fields_and_send_row() -> None:
    ui = Ui(make_cache(), SECRETS)

    press(ui, ENTER, "50")

    assert ui.form_lines() == [
        "Kitchen (e:1:2)",
        "",
        "> position     [ 50_       ]  0..100",
        f"  positionCmd  {LEFT} unset {RIGHT}",
        "",
        "  [ Review and send ]",
    ]


def test_only_the_focused_number_field_shows_the_cursor() -> None:
    ui = Ui(make_cache(), SECRETS)
    press(ui, ENTER)
    empty = shown_value(ui, "position")

    press(ui, "50", curses.KEY_DOWN)

    assert empty == "_"
    assert shown_value(ui, "position") == "50"


def test_an_unset_number_field_not_focused_says_unset() -> None:
    ui = Ui(make_cache(), SECRETS)

    press(ui, ENTER, curses.KEY_DOWN)

    assert shown_value(ui, "position") == "unset"


def test_the_values_of_the_fields_start_in_one_column_wherever_the_focus() -> None:
    ui = Ui(make_cache(), SECRETS)
    select(ui, BOILER)
    press(ui, ENTER, "20")
    starts = set()

    for _ in range(9):
        rows = ui.form_lines()[2:-2]
        starts |= {next(i for i, c in enumerate(r) if c in f"[{LEFT}") for r in rows}
        press(ui, curses.KEY_DOWN)

    assert len(rows) == 9
    assert len(starts) == 1


def test_the_hint_is_on_the_focused_row_only() -> None:
    ui = Ui(make_cache(), SECRETS)
    press(ui, ENTER)
    first = ui.form_lines()

    press(ui, curses.KEY_DOWN)

    assert first[2].endswith("  0..100")
    assert not any("←→" in line for line in first)
    assert ui.form_lines()[3].endswith("  ←→ to choose")
    assert not any("0..100" in line for line in ui.form_lines())


def test_esc_cancels_the_form() -> None:
    ui = Ui(make_cache(), SECRETS)

    press(ui, ENTER, "5", ESC)

    assert ui.mode == "browse"


def test_y_confirms_an_ordinary_target_and_returns_the_submission() -> None:
    ui = Ui(make_cache(), SECRETS)

    result = press(ui, ENTER, "50", *REVIEW, "y")

    assert result == Submission(SHUTTER, {"position": 50}, "tui")
    assert ui.mode == "browse"


@pytest.mark.parametrize("keys", ["y", "yes", "n"])
def test_a_key_outside_the_confirmation_sends_nothing(keys: str) -> None:
    ui = Ui(make_cache(), SECRETS)

    assert press(ui, keys) is None
    assert press(ui, ENTER, keys) is None
    assert ui.mode == "form"


def test_any_other_key_cancels_an_ordinary_target() -> None:
    ui = Ui(make_cache(), SECRETS)

    result = press(ui, ENTER, "50", *REVIEW, "Y")

    assert result is None
    assert ui.mode == "browse"


def test_heating_needs_the_word_yes() -> None:
    ui = Ui(make_cache(), SECRETS)
    select(ui, BOILER)

    result = press(ui, ENTER, curses.KEY_DOWN, "20.5", *REVIEW, "yes", ENTER)

    assert result == Submission(BOILER, {"heatSetpoint": 20.5}, "tui")


@pytest.mark.parametrize(
    "keys",
    [("y", ENTER), ("Y", ENTER), ("ye", ENTER), (ENTER,), (curses.KEY_ENTER,), (ESC,)],
)
def test_anything_else_cancels(keys: tuple[int | str, ...]) -> None:
    ui = Ui(make_cache(), SECRETS)
    select(ui, BOILER)
    press(ui, ENTER, curses.KEY_DOWN, "20.5", *REVIEW)

    result = press(ui, *keys)

    assert result is None
    assert ui.mode == "browse"


def test_typing_into_the_heating_confirmation_returns_nothing() -> None:
    ui = Ui(make_cache(), SECRETS)
    select(ui, BOILER)
    press(ui, ENTER, curses.KEY_DOWN, "20.5", *REVIEW)

    result = press(ui, "yes")

    assert result is None
    assert ui.confirm_lines()[-1] == "> yes"


def test_confirm_lines_warn_for_heating_only() -> None:
    shutter = Ui(make_cache(), SECRETS)
    press(shutter, ENTER, "50", *REVIEW)
    area = Ui(make_cache(), SECRETS)
    select(area, "a:7")
    press(area, ENTER, curses.KEY_RIGHT, *REVIEW)

    assert shutter.confirm_lines() == [
        "Send to Kitchen (e:1:2)",
        "position: 100.0 -> 50",
        "Press y to send, any other key cancels.",
    ]
    assert area.confirm_lines() == [
        "Send to Living (a:7)",
        "comfortMode: unset -> HEATING",
        "Some modes and low setpoints can leave the home unheated.",
        "Type yes then Enter to send, anything else cancels.",
        "> ",
    ]


def test_connection_loss_closes_the_form() -> None:
    cache = make_cache()
    ui = Ui(cache, SECRETS)
    press(ui, ENTER)

    cache.mark_disconnected()
    ui.rows()

    assert ui.mode == "browse"


def test_connection_loss_closes_the_confirmation_on_a_key() -> None:
    cache = make_cache()
    ui = Ui(cache, SECRETS)
    press(ui, ENTER, "50", *REVIEW)

    cache.mark_disconnected()
    result = press(ui, "y")

    assert result is None
    assert ui.mode == "browse"


@pytest.mark.parametrize(
    ("result", "status"),
    [
        (Sent("acknowledged", {}, datetime(2026, 1, 1, tzinfo=UTC)), "acknowledged"),
        (
            Sent("unknown", {}, datetime(2026, 1, 1, tzinfo=UTC)),
            "unknown, check the state before retrying",
        ),
        (NotSent("not_synced"), "not sent: not_synced"),
        (
            NotSent("invalid_write", detail="at least one field"),
            "not sent: invalid_write (at least one field)",
        ),
        (
            NotSent("rate_limited", retry_after_s=0.5),
            "not sent: rate_limited, retry after 0.5s",
        ),
    ],
)
def test_show_result_wording(result: Sent | NotSent, status: str) -> None:
    ui = Ui(make_cache(), SECRETS)

    ui.show_result(result)

    assert ui.status == status


def test_connection_line_gives_connection_and_freshness() -> None:
    cache = make_cache()
    ui = Ui(cache, SECRETS)
    synced = ui.connection_line()
    cache.mark_disconnected()

    assert synced == "connected, synced 2026-11-02T09:14:05Z"
    assert ui.connection_line() == "disconnected, not synced"


def test_fields_the_form_cannot_represent_are_not_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schemas = {"position": {"type": "integer"}, "label": {"type": "string"}}
    monkeypatch.setattr("tydomio.tui.model.write_fields", lambda _: schemas)
    ui = Ui(make_cache(), SECRETS)

    press(ui, ENTER)

    assert [line.split()[1] for line in ui.form_lines()[2:-2]] == ["position"]


def test_no_form_opens_without_a_representable_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tydomio.tui.model.write_fields", lambda _: {"label": {"type": "string"}}
    )
    ui = Ui(make_cache(), SECRETS)

    press(ui, ENTER)

    assert ui.mode == "browse"


def refused(ui: Ui, status: str) -> None:
    """Press Enter and check the refusal: the reason shown, no form, no submission."""
    assert press(ui, ENTER) is None
    assert ui.status == status
    assert ui.mode == "browse"


def test_enter_says_when_not_connected_and_synced() -> None:
    cache = make_cache()
    cache.mark_disconnected()

    refused(Ui(cache, SECRETS), "cannot command: not connected and synced")


def test_enter_says_when_the_config_is_missing() -> None:
    cache = make_cache()
    cache.config = None

    refused(Ui(cache, SECRETS), "cannot command: nothing selected")


def test_enter_says_when_nothing_is_selected() -> None:
    cache = Cache()
    cache.apply_config(config({}))
    cache.mark_connected()
    cache.mark_synced(datetime(2026, 11, 2, 9, 14, 5, tzinfo=UTC))

    refused(Ui(cache, SECRETS), "cannot command: nothing selected")


def test_enter_says_when_the_item_has_no_writable_field() -> None:
    ui = Ui(make_cache(), SECRETS)
    select(ui, "e:30:1")

    refused(ui, "cannot command: this item has no writable field")


def test_enter_says_when_no_field_is_editable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tydomio.tui.model.write_fields", lambda _: {"label": {"type": "string"}}
    )

    refused(Ui(make_cache(), SECRETS), "cannot command: no editable field")


def test_opening_a_form_clears_the_previous_status() -> None:
    ui = Ui(make_cache(), SECRETS)
    ui.status = "not sent: stale"

    press(ui, ENTER)

    assert (ui.mode, ui.status) == ("form", "")


def test_an_enum_field_ignores_digits() -> None:
    ui = Ui(make_cache(), SECRETS)

    press(ui, ENTER, curses.KEY_DOWN, "5")

    assert shown_value(ui, "positionCmd") == "unset"


def thermostat_ui(secrets: Secrets) -> Ui:
    """Build a Ui over a fresh cache with one re2020ControlBoiler endpoint."""
    cache = Cache()
    cache.apply_config(config({(30, 1): ("Thermostat", "re2020ControlBoiler")}))
    cache.replace_devices(devices((30, 1, [data("selectedProg", 1)])), [])
    cache.mark_connected()
    cache.mark_synced(datetime(2026, 11, 2, 9, 14, 5, tzinfo=UTC))
    return Ui(cache, secrets)


def test_a_re2020_control_boiler_endpoint_needs_the_word_yes() -> None:
    ui = thermostat_ui(SECRETS)
    press(ui, ENTER, "2", *REVIEW)

    assert press(ui, "y") is None
    assert press(ui, "es", ENTER) == Submission("e:30:1", {"selectedProg": 2}, "tui")


def test_a_heating_usage_scrubbed_from_the_display_still_needs_yes() -> None:
    # a password inside the usage name redacts the kind shown; built from parts
    inside_usage = "".join(("20", "20"))
    ui = thermostat_ui(Secrets(password=inside_usage, ip=SECRETS.ip, mac=SECRETS.mac))
    press(ui, ENTER, "2", *REVIEW)

    assert press(ui, "y") is None
    assert press(ui, "es", ENTER) == Submission("e:30:1", {"selectedProg": 2}, "tui")


def test_selected_gives_the_id_of_the_selected_item() -> None:
    ui = Ui(make_cache(), SECRETS)
    ui.rows()

    press(ui, curses.KEY_DOWN)

    assert ui.selected == "e:1:3"


def test_heating_confirmation_is_true_only_when_confirming_a_heating_target() -> None:
    ui = Ui(make_cache(), SECRETS)
    select(ui, BOILER)
    assert not ui.heating_confirmation

    press(ui, ENTER, curses.KEY_DOWN, "20.5", *REVIEW)

    assert ui.heating_confirmation
