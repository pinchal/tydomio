# Extending the datamodel with new equipment

tydomio types the data of each Tydom usage (`shutter`, `boiler`, `conso`...) and of the
heating areas. A model describes a type of equipment, never a home: how many shutters
you have, their names and their ids appear nowhere in it.

The models are tested against `tests/usages.json`, the catalog of the fields and
commands of each usage seen on real Tydoms. Equipment the catalog does not know has to
be seen on a box that has it, so everything starts with a capture of that box. Then
either you write the model yourself (path A, the capture stays on your machine), or you
send the capture to a maintainer who writes it (path B).

## Capture your box

```
uv run tydomio-capture -o ./my-capture
```

The tool only reads: GET requests on a fixed list of endpoints, nothing is ever written
to the box. It takes the Tydom password from `TYDOM_PASSWORD`, or asks for it, and the
MAC address from `--tydom-mac` or `TYDOM_MAC_ADDRESS`. With `--tydom-ip` (or
`TYDOM_IP`) it connects on the local network; without it, through the Delta Dore remote
access.

What it writes is anonymized. Ids are renumbered, names, MAC and IP addresses replaced,
and any value its rules do not know is redacted. If it still finds the password, the
MAC or the IP address, it writes nothing. You get one JSON file per endpoint and a
`manifest.json` that gives the outcome of each endpoint, the values redacted as unknown
(`unclassified`) and the keys renamed.

It exits with 0 when the capture is complete, 1 when some endpoints failed and the
others were written, 2 when nothing was written.

The anonymization only removes what its rules recognize. The capture still describes
your installation: the kinds of equipment, the scenarios, the groups, the heating
settings. Read it before it leaves your machine.

## Path A: you write the model

### 1. Merge the capture into the catalog

```
uv run python -m tydomio.capture.catalog ./my-capture tests/usages.json
```

The catalog keeps, per usage, the name, type, permission, bounds, step and enum values
of each field and command. No id, count, name or value goes into it.

- 0: the catalog is written; new enum values are added to the known ones.
- 1: a conflict. A field already in the catalog is described differently by your box
  (another type, other bounds...). Each conflict is printed and nothing is written; see
  [Conflicts](#conflicts).
- 2: the capture or the catalog cannot be read.

### 2. Run the alignment tests

```
uv run pytest tests/test_usage_alignment.py
```

They fail until the models match the catalog, and the failing test says what is
missing:

| Failure | What to do |
| --- | --- |
| `test_usage_has_a_model` | A new usage: write its model (step 3) |
| `test_field_type_is_compatible` | A readable field is missing from the model, or has another type |
| `test_enum_values_are_members` | A value is missing from the field's `TydomEnum` |
| `test_writable_field_is_written_or_not_written_with_a_reason` | A writable field is neither in the write model nor in `NOT_WRITTEN` |
| `test_writable_command_is_not_written_with_a_reason` | A command is not in `NOT_WRITTEN` |
| `test_write_bounds_are_the_catalog_range`, `test_write_step_is_the_catalog_step`, `test_write_enum_values_are_the_catalog_values` | The write model does not match the catalog |

### 3. Write the model

Each usage has its module in `tydomio/model/usages/`, named after it (`sensor_sun.py`
for `sensorSun`). `shutter.py` is a complete example:

```python
class Shutter(UsageModel):
    """Roller shutter."""

    position: Reading[float] | None = Field(default=None, alias="position")
    on_fav_pos: Reading[bool] | None = Field(default=None, alias="onFavPos")
```

The read model subclasses `UsageModel`. Each readable field is `Reading[T] | None`, with
`alias` set to its Tydom name; `Reading` keeps the value and its validity. A field the
model does not know lands in `unknown`, so a new firmware does not break the parsing.

An enum subclasses `TydomEnum` with the catalog values, and its field is annotated
`Reading[MyEnum | str]`: a value outside the enum is logged and kept as a plain `str`.

A numeric field also carries the catalog `unit`, as `json_schema_extra={"unit": "degC"}`
(`NA` and `boolean` are not carried; a test compares every unit to the catalog), and read
back through `field_meta()`. Add a `description="..."` only when the meaning is known for
sure, as for the shutter `position` ("0 = closed, 100 = open"): a guess is worse than none.

Only the fields meant to be written go into a write model, a subclass of
`UsageWrite[MyUsage]`. Each field is `X | None` with the catalog bounds (`ge`, `le`,
`multiple_of` for the step), and an enum field is `Annotated[MyEnum, member_of(MyEnum)]`.
The writable fields left out (pairing, installation set-up, measures, commands that
erase data...) are listed in `NOT_WRITTEN`, each with the reason.

Register the read model in `USAGES`, under its Tydom `last_usage` name, and the write
model in `WRITES`; both are in `tydomio/model/usages/__init__.py`. Usages that share
fields share a base class, as `boiler` and the areas share `ThermicZone` in
`thermic.py`.

### 4. Check and open the pull request

```
uv run pytest
uv run pre-commit run --all-files
```

The pull request holds `tests/usages.json`, the model, its registration and the tests
it needs. Never the capture.

## Path B: a maintainer writes the model

1. Capture your box, as above.
2. Read every file of the capture, `manifest.json` included, and remove or replace
   whatever you do not want to share. The catalog only needs five files:
   `configs_file.json`, for the usage of each endpoint, and the `*_meta.json` and
   `*_cmeta.json` files of the devices and areas.
3. Open an issue that describes the equipment (brand, model, what it does), and ask a
   maintainer where to send the capture. A private channel is better than an attachment
   on a public issue.
4. The maintainer follows path A with your capture, then deletes it.

## Rules

Model only what the box exposes. A field, a link or a behaviour that no endpoint of
the box shows is not modelled, even if the Delta Dore app knows about it.

A write model may expose a field nobody has tried on a real box; say so in the pull
request. [`docs/threats/writes.md`](threats/writes.md) records which writes were
checked on a real box and which were not. A heating write is never tried by a test, an
example or a default routine.

Tests never connect to a box. Only the capture tool does, run by hand.

### Conflicts

Two boxes can describe the same field differently, usually because of their firmware.
The catalog tool stops rather than choose, and the pull request settles it:

- Bounds or step: take the widest range that covers both boxes, edit
  `tests/usages.json` by hand, and say in the pull request which box gave which bounds.
- Type, permission or anything else: open an issue with the conflict the tool printed.
  The model needs a decision.
