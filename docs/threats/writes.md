# Threats: writes to equipment

A write changes a device, a group of devices or an area of the home. There are three
requests:

| Request | Sends |
| --- | --- |
| `WriteEndpoint(ref, write)` | `PUT /devices/{device}/endpoints/{endpoint}/data` |
| `WriteGroup(ref, write)` | `PUT /groups/{group}/data` |
| `WriteArea(ref, write)` | `PUT /areas/{area}/data` |

This page lists what a mistake or a malicious caller could do through them, and the
check and the test that stop each case.

## Callers

- CLI: a person types the command, with typos and wrong ids.
- Home Assistant: automations may fire often, or at the wrong time.
- MCP server: an LLM picks the target and the values. Its input is untrusted: it can
  give any id, any field name, any value, and repeat itself.

## What a write can break

- Heating (`boiler`, areas, `re2020ControlBoiler`). The heat pump is driven through a
  Typass ATL gateway, and some modes (away, off, anti-frost) put zones in frost
  protection. A wrong write leaves the home cold, possibly for days before anyone
  notices.
- Pairing and configuration (`modeAsso`, `localisation`, `sendAsso`,
  `sendNetRemoteAssoMode`, `recFav`, the probe's `name`). A write can unpair a device
  or change the installation, and putting it back may need the installer.
- The `reset` command of `conso`, which erases the values the meter recorded.
- Shutters. A moving shutter is seen and can be moved back, but it can trap or hit
  something, and an open shutter can matter for security.

## Threats

### Wrong target

A heating write sent to a shutter, or to the right endpoint id on the wrong device.

`Installation` indexes the endpoints from `/configs/file` by (device id, endpoint id),
and hands out an `EndpointRef[U]` only when the endpoint's usage is `U`.
`WriteEndpoint` takes a `UsageWrite[U]`, which ty checks, and at runtime it checks that
the write model is the one `WRITES` registers for the ref's usage.

Tests: `test_installation.py::test_endpoint_id_shared_by_two_devices_resolves_per_device`,
`test_endpoint_of_another_usage_is_refused`, `test_unknown_endpoint_is_refused_without_its_ids`;
`test_requests.py::test_write_of_another_usage_is_refused`,
`test_write_endpoint_puts_to_the_device_and_endpoint_of_the_ref`.

### One write moves several devices

A group write repeats a mistake on every device of the group.

`Installation` indexes the groups by id with their usage, from `/configs/file`, and
hands out a `GroupRef[U]` only for a group of usage `U`. `WriteGroup` takes a
`UsageWrite[U]` and goes through everything an endpoint write does: the runtime check,
revalidation, JSON body, acknowledgement and logs. Only a usage with a write model can
be written, today the shutter groups.

Tests: `test_installation.py::test_unknown_group_is_refused_without_its_id`,
`test_group_of_another_usage_is_refused`, `test_group_reference_refuses_an_id_that_is_not_an_int`;
`test_requests.py::test_write_group_puts_the_body_to_the_group_of_the_ref`,
`test_write_of_another_usage_is_refused_for_a_group`.

### Out-of-range, unknown or non-finite values

The write models are strict pydantic models. They refuse a value outside the catalog
bounds (`ge`, `le`, the step), NaN and infinity, any type conversion, an unknown enum
value, an unknown field, and a write with no field at all. The request validates the
model again, so `model_construct` or `model_copy` cannot skip the checks. A refused
model builds no request, so nothing is sent.

Tests: `test_usage_writes.py`, one per case;
`test_requests.py::test_write_skipping_validation_is_validated_again`.

### Implicit or repeated writes

On its own the client only sends pings, and `send` sends a write once. When the
connection is lost, the pending write fails with `CommunicationError` and is forgotten.
On a timeout it is abandoned and fails. Nothing is ever sent again.

Tests: `test_client.py::test_connection_reconnection_and_pushes_send_nothing_but_pings`,
`test_write_lost_with_the_connection_is_not_resent`,
`test_write_timeout_raises_after_a_single_request`.

### Injection in the url or the body

The ids come from the refs and must be `int`: a `str`, a `float` or a `bool` is
refused. `json.dumps` builds the body from validated values.

Tests: `test_installation.py::test_reference_refuses_an_id_that_is_not_an_int`;
`test_requests.py::test_write_content_is_the_json_body_of_the_fields_set`.

### Errors leaking the home's data

Any response other than an empty 200 raises `InvalidResponse` with the status only. A
validation error names the model's own fields, never the values given nor an unknown
name.

Tests: `test_response.py::test_ok_error_carries_no_content`;
`test_usage_writes.py::test_error_names_the_field_without_its_value`,
`test_error_does_not_echo_an_unknown_field_name`.

### Logs leaking values

Building a write logs the usage and the field names at INFO; the values go to DEBUG
only.

Tests: `test_requests.py::test_write_logs_no_value_above_debug`,
`test_write_logs_its_values_at_debug`.

## Not exposed

`NOT_WRITTEN` (`tydomio/model/usages/__init__.py`) lists each field left out with its
reason, and the alignment tests fail when a writable catalog field is in neither a
write model nor that list. Left out:

- the pairing and configuration fields above;
- the `conso` `reset` command;
- `ambientTemperature` of a `boiler`: it is a measure, and writing it lies to the
  regulation.

## Left to the callers

The library sends every write it is given. A caller that lets an LLM or an automation
write must limit how often, and ask a person before a heating write.

A write whose connection is lost, or that times out, may still have been applied. The
library does not send it again: the caller reads the state back before deciding.

## Not verified

Heating writes are never sent to the real box, not by tests, examples or default
routines, and tests use injected doubles only. The acknowledgement (status 200, no
content) was checked on the real box twice: a shutter `positionCmd` `DOWN`, which
moved the shutter, and a shutter group `positionCmd` `UP`, which opened all its
shutters. It is not verified for heating writes.
