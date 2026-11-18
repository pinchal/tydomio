# Threats: the terminal UI

`tydomio-tui` shows the state of the home in a terminal and lets a person send a command
to one endpoint or area. This page lists what a mistake or a hostile text could do
through it, and the check and the test that stop each case. The write path itself
(target, write model, acknowledgement) is the one of [writes](writes.md); the TUI adds
the screen, the keys and the terminal.

## Callers

- A person typing at the terminal, with stray keys, typos and a window that gets resized.
- The network: every Tydom name, value and log line is untrusted text drawn on the
  terminal. It can carry escape sequences, control or bidirectional characters, or the
  password, IP or MAC address.

## What a command can break

See [writes](writes.md): heating (frost protection through the Typass ATL gateway),
pairing and configuration fields, the `conso` `reset`, shutters. The TUI has no
`--writes` option: every usage `WRITES` declares can be commanded, so the guards below
are the confirmation, the freshness check, the write models and the rate.

## Threats

### A key pressed by mistake

Only the confirmation screen gives a `Submission`: in browse and form modes
`Ui.handle_key` returns nothing, whatever the key. The loop sends only a `Submission`
(`tui/main.py:_loop`). An ordinary target is confirmed by `y` alone, anything else
cancels.

Tests: `tests/tui/test_model.py::test_a_key_outside_the_confirmation_sends_nothing`,
`test_any_other_key_cancels_an_ordinary_target`;
`tests/tui/test_main.py::test_scenario_cancelled_confirmation_sends_nothing`.

### A heating command confirmed by accident

A heating target (`boiler`, `re2020_control_boiler`, heating area) shows the warning
that some modes and low setpoints can leave the home unheated, and is sent only after
the word `yes` and Enter (`tui/model.py:Ui._confirm_key`). `Y`, `ye`, Enter alone, Esc
or any key that is not printable cancels. Whether the target is heating is decided from
the raw config in `Ui._open` (`write_model(config, ..., HEATING)`), not from the usage
name on the display, which a secret in the name could redact.

Tests: `tests/tui/test_model.py::test_heating_needs_the_word_yes`,
`test_anything_else_cancels`, `test_typing_into_the_heating_confirmation_returns_nothing`,
`test_a_re2020_control_boiler_endpoint_needs_the_word_yes`,
`test_a_heating_usage_scrubbed_from_the_display_still_needs_yes`,
`test_heating_confirmation_is_true_only_when_confirming_a_heating_target`;
`tests/tui/test_render.py::test_the_heating_hint_asks_for_yes_and_escape_cancels`,
`test_the_heating_warning_and_typed_word_stay_visible_in_a_short_pane`,
`test_the_confirmation_keeps_its_target_warning_prompt_and_typed_word` (five values
at the minimum size).

### A value out of bounds or not in the enum

The form offers only the fields the write model can represent, cycles enums through
their members, and parses numbers (`Ui._to_confirm`). The value then goes to
`Writer.write`, which validates it with the write model by Tydom names before any send
(`state/writer.py:_validated`): out of bounds, unknown enum value, unknown field, NaN
are refused as `invalid_write`, with the field named and the value not echoed.

Tests: `tests/state/test_writer.py::test_an_invalid_write_is_not_sent_and_echoes_nothing`,
`test_the_write_model_names_the_invalid_field`;
`tests/tui/test_model.py::test_unparsable_number_blocks_the_confirmation`,
`test_an_enum_field_ignores_digits`, `test_fields_the_form_cannot_represent_are_not_offered`.

### A command while the cache is not fresh

`Ui._open` does not open the form unless the cache is fresh, and a form or confirmation
open when the connection is lost is closed (`Ui._read`). At send, `Writer._synced`
refuses with `not_synced` as a backstop.

Tests: `tests/tui/test_model.py::test_enter_when_not_fresh_does_nothing`,
`test_connection_loss_closes_the_form`,
`test_connection_loss_closes_the_confirmation_on_a_key`;
`tests/state/test_writer.py::test_a_write_while_not_synced_is_not_sent`.

### Two quick commands

The `Writer` is shared by every write of the process and allows one send per
`1 / WRITE_RATE` s, `WRITE_RATE` being the constant 1.0 in `tui/main.py`, with no option.
A second command within the interval is refused as `rate_limited` and shown with its
retry delay.

Tests: `tests/tui/test_main.py::test_a_second_command_within_the_interval_is_held_by_the_write_rate`
(through the `WRITE_RATE` constant, lowered so that the interval is long); `tests/state/test_writer.py::test_a_write_within_the_interval_is_rate_limited`,
`test_two_parallel_writes_send_one`.

### A send that is cancelled or fails

`Writer._send_once` sends once and never resends. A communication error or a
cancellation during the send is the outcome `unknown`; the screen says to check the
state before retrying (`Ui.show_result`), and a cancellation propagates.

Tests: `tests/state/test_writer.py::test_a_communication_error_is_unknown_sent_once_and_counted`,
`test_cancelled_during_the_send_is_sent_once_counted_and_logged`;
`tests/tui/test_model.py::test_show_result_wording`.

### A name with escape sequences or control characters

Tydom strings reach the screen through `sanitize` (`state/view.py`: control and format
characters stripped, bounded length) and every drawn line is cut to its pane
(`tui/render.py:_put`). Log lines get `strip_controls` (`tui/logs.py:LogBuffer.emit`).

Tests: `tests/tui/test_render.py::test_item_name_with_escape_codes_is_drawn_clean`,
`test_log_line_with_control_characters_is_drawn_clean`,
`test_a_log_line_with_a_newline_is_drawn_on_one_line`, `test_the_status_is_drawn_clean`;
`tests/tui/test_logs.py::test_strips_control_characters`;
`tests/state/test_view.py::test_sanitize`.

### A name or log carrying a secret

`sanitize` redacts the password, IP and MAC in what the model draws; the log handler
has the `SecretFilter` and scrubs again after stripping controls, so a secret split by a
control character is caught. Messages at INFO carry no content anyway.

Tests: `tests/state/test_view.py::test_sanitize`,
`test_every_tydom_string_leaves_sanitized`;
`tests/tui/test_logs.py::test_scrubs_secrets_hidden_behind_control_characters`;
`tests/tui/test_main.py::test_logs_pane_shows_info_without_secrets`.

### A resize or a very small terminal

`draw` writes one line when the terminal is under the minimum size, never writes outside
the window or the last cell, and cuts text to the pane. A `KEY_RESIZE` only redraws.

Tests: `tests/tui/test_render.py::test_too_small_terminal_draws_one_line`,
`test_nothing_is_drawn_outside_the_window`, `test_the_last_cell_of_the_window_is_never_written`,
`test_text_is_cut_to_the_pane_width`;
`tests/tui/test_main.py::test_a_resize_redraws_without_reaching_the_ui`.

### The loop's pseudo-keys

`getch` gives `-1` when no key is pressed and `KEY_RESIZE` on a resize. `_loop` draws
on `-1` and passes neither to `Ui.handle_key`: a `-1` would cancel a heating
confirmation in the middle of typing `yes`.

Tests: `tests/tui/test_main.py::test_no_key_is_never_passed_to_the_ui_as_minus_one`,
`test_a_resize_redraws_without_reaching_the_ui`.

### A malformed push

The client rejects a malformed message, logs it without its content, and keeps reading;
the screen is not involved.

Tests: `tests/test_client.py::test_read_loop_keeps_reading_after_malformed_message`,
`test_read_loop_logs_no_content_above_debug`.

A push that parses but cannot be merged into the cache is different: it ends the
client with a `CacheError` rather than going on with a state of doubtful accuracy.
`curses.wrapper` restores the terminal and the TUI exits with code 1, naming the error
types only.

Tests: `tests/state/test_sync.py::test_a_failing_handler_ends_the_client_with_a_cache_error`;
`tests/tui/test_main.py::test_a_fatal_websocket_error_exits_1_naming_types_only`.

### Output while curses owns the terminal

`main.run` reads the secrets with a temporary handler, so the reason of a refusal does
not reach stderr, then installs the `LogBuffer` on the root logger and removes it at the
end; the lines kept are printed once the terminal is restored. The library prints
nothing. This holds by construction (no print in the library, logs go to the buffer,
`main` prints only after curses returned) and is not asserted by any test.

## Known gaps

- No test asserts that nothing reaches stderr or stdout while curses owns the
  terminal, in particular once the log handler is removed at the end of `run`. The
  guarantee rests on the construction alone.
- `tui/main.py:_wrapped` and `main` (the real terminal, `curses.wrapper`, Ctrl-C) are
  excluded from coverage and not tested.
- `LogBuffer.emit` guards against a malformed log record, but `SecretFilter.filter`
  calls `record.getMessage()` before `emit`: with the filter attached, as in `run`, a
  malformed record still raises into the logging caller. The guard covers an
  unfiltered buffer only; the MCP stderr handler has the same exposure.
- Ctrl-C while a write is in flight exits 0 and does not print the kept logs, so the
  user is not told that the outcome is unknown.

## Not verified

The TUI was not run against the real Tydom, and no heating command was sent. The tests
use injected doubles only.
