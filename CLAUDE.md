# tydomio

Async Python library to talk to a Delta Dore Tydom box (local or through
`mediation.tydom.com`), built on a typed data model. It is the base for other tools:
CLI, Home Assistant integration, MCP server.

The user is a senior developer who maintains this repository: they review every change
and take the architecture decisions. The agent's role is to propose and implement work
aligned with the practices below, not to decide the shape of the project.

## Working posture

Make the simplest change that fully solves the task.

- **Before writing.** Understand the request and the code involved. List every place the
  change must reach (callers, tests, fixtures, config) and what it could break.
- **First option that works.** Does it need to exist at all? Does the codebase already
  have it? The standard library? An installed dependency? A small need never justifies a
  new dependency. Otherwise, write the minimum code.
- **Nothing extra.** No wrapper, option, config or code "for later" that nobody asked
  for. Deleting beats adding. Keep the structure the repository already has.
- **While editing.** Moved code keeps its error handling and validation. A comment says
  only why, in one line.
- **Never cut** validation of data coming from the Tydom or the user, error handling,
  security, or anything the user asked for.
- **Close** with a line or two: what was left out or unverified, and any risk.

## Decisions belong to the user

- **Architecture.** A new module, layer, public API, abstraction, dependency, or a
  change of the module map below is a proposal first: give two or three options at most,
  their trade-offs, and a recommendation, then wait for the decision. Implement only what
  was decided.
- **Review-friendly.** The user reads every diff: small, focused changes, one subject
  per pull request, no unrelated reformatting.

## The real Tydom

- The agent may explore the real Tydom occasionally, but only with the user's
  permission, asked before every session of exploration: say what will be run, what it
  reads or writes, and why. A permission covers that exploration only, not the next one.
- Exploration is read-only by default. Each command that writes to equipment is asked
  for separately, named explicitly (device, value), and its result reported.
- Credentials come from the environment the user provides; the agent uses them without
  reading, printing or copying them. Captured messages stay outside the repository
  unless anonymized as described below.
- Writes to equipment (heating, hot water, shutters, alarm, locks) are explicit, typed,
  and never sent implicitly (no command as a side effect of a read, a refresh or a
  connection). Heating is driven by a heat pump through a Typass ATL gateway: known
  modes (away, off, eco) can put zones in frost protection, so heating commands are
  never sent by tests, examples or default routines.
- Tests run against fixtures and minimal injected doubles, never against the box.

## Secrets and personal data

- **Secrets.** The Tydom password, MAC address, IP and Delta Dore account credentials
  are secrets. Never read, print or copy a secret file (`.env`, keys, credentials), and
  never write one in code, fixtures, commands, logs or messages. Configuration comes
  from environment variables or arguments. A secret pushed, even if removed later, is
  compromised: tell the user so they rotate it.
- **Personal data.** Messages from the Tydom describe a home: device and room names,
  presence, alarm state, schedules. They are personal data.
  - Fixtures derived from real captures are anonymized (MAC, names, identifiers that
    reveal the home) and reviewed by the user before commit; otherwise test data is
    synthetic.
  - Debug dumps are written outside the package and the repository, and never committed.
  - Logs at `INFO` and above carry no secret and no message content; content goes to
    `DEBUG` only, with the `Authorization` header never logged.

## Checks

- **Never weaken a check** to make a change pass: no CI `continue-on-error`, no skipped
  or deleted test, no re-baselined reference, no lint, type or security suppression
  (`noqa`, `type: ignore`, `pylint: disable`) without a one-line reason the user
  accepts, no `--no-verify`.
- **Static analysis and types.** ruff (with its security rules), the formatter, pylint
  (a whitelist: `disable = ["all"]` plus an `enable` list of only the checks ruff and ty
  miss) and ty run in pre-commit and in the CI. Type everything; `Any` only when
  unavoidable, at the boundary with untyped libraries.

## Tests

Test what would cost if it broke: protocol parsing, the data model, the client state
machine (connection, reconnection, timeouts), and every bug fixed.

- **Orthogonal.** One behaviour per test, one reason to fail. Tests are independent: no
  shared state, no imposed order, no network: the network boundary is replaced by
  minimal injected doubles (a fake websocket replaying scripted bytes,
  `httpx.MockTransport`), never by a simulator or a fake Tydom server.
- **One level per behaviour.** A behaviour is asserted at one level only, unit or end to
  end; other tests may run it, not re-assert it. No test restates a file, a constant or
  a configuration.
- **End to end.** At least one scenario per entry point (library client, CLI), from its
  user's side, against those injected doubles.
- **Change.** An intended behaviour change edits only the tests that carry it, in the
  same pull request, with the reason in the commit. A test of another behaviour that
  breaks is a stop sign.
- **Coverage.** At least 85% of branches, enforced by the CI and never lowered. Only the
  thin adapter to the real network may be excluded, in the coverage configuration.
- **CI.** The tests run in the CI and block the merge.

## Architecture

Keep the shape of the whole in mind while changing a part, and follow SOLID without
excess.

- **Single responsibility.** One per module, file, class and function. A file that
  mixes responsibilities is pointed out to the user with a split proposal.
- **Open/closed, without excess.** An abstraction (protocol, base class, strategy) only
  once two concrete variants exist, never in anticipation; from then on, a further
  variant is added as new code, not by editing the existing ones.
- **Liskov substitution.** A subtype works wherever its parent is expected.
- **Interface segregation.** Small, focused interfaces; a caller depends only on what it
  uses.
- **Dependency inversion.** The data model and protocol logic depend on what they are
  given (bytes, objects, callables), never directly on the network, files or the
  terminal, so they are tested without the box.
- **No duplicated logic.** Fix a defect at its root, in the shared place, not in each
  copy.
- **Library first.** The library never prints, never calls `sys.exit`, never reads
  environment variables, and never configures logging: that belongs to its callers
  (CLI, Home Assistant, MCP).

## Secure development

- **Untrusted input.** Everything from the Tydom, the network or the user is validated
  where it enters: type, range, size, format (pydantic models at the boundary). No path
  built from received data, no `eval`, `exec`, `shell=True` or unsafe deserialization
  (`pickle`, `yaml.load`). A malformed message is rejected and logged, it does not crash
  the connection.
- **Safe defaults.** An error stops the action rather than going on with bad data. No
  debug mode on by default. No home-made cryptography: authentication uses httpx's
  digest implementation.
- **TLS.** In local access only, the Tydom needs an unverified TLS context with legacy
  renegotiation; remote access through `mediation.tydom.com` verifies the certificate
  and the hostname. This exception stays confined to the transport code; do not extend
  it to other connections and do not change it without asking.
- **Dependencies.** Few, from PyPI, maintained and widely used, locked in `uv.lock`. No
  code copied from an unknown source; protocol knowledge taken from other projects
  (tydom2mqtt) is credited in a comment. A known vulnerability that an update fixes is
  fixed at once; otherwise tell the user.
- **Logs and errors.** Exceptions raised by the library are `TydomIoError` subclasses
  with a clear message; no secret in messages.
- **Threats.** Before a feature that sends commands or accepts outside input (CLI, MCP
  tools), list what a mistake or a malicious caller could do through it, and cover each
  case with a check and a test.

## Git

- **Branches.** One short-lived branch per change, from an up-to-date `main`, named
  `type/subject` (`feat/devices-meta`). Never commit or push to `main`; changes reach it
  through a GitHub pull request.
- **Commits.** Only when the user asks. One coherent change per commit, the code
  working at each. Read the staged diff before committing: nothing unrelated, generated
  or secret. Messages follow Commitizen, `type(scope): subject`, subject and body
  together 500 characters at most.
- **History.** Rewrite only your own branch, with `--force-with-lease`; never `main`.
- **Pull requests.** One subject, a size the user can review. The description says what
  changes, why, and how it was verified (and what was not, e.g. untested against the
  real box).
- **Truth.** Documentation, commits and PRs state what was verified and how, nothing
  more.

## Stop signs

Stop, explain in plain words what was reached, and wait for the user:

- an architecture or public API decision, a new dependency;
- a secret, personal data from a real capture, or any connection to the real box;
- a command that writes to equipment, in particular heating and alarm;
- a check that blocks and seems wrong, or a test of another behaviour that breaks;
- publishing (PyPI release, public repository settings, Home Assistant submission).

## Run and test

- Python 3.14 or newer: `uv sync`.
- Checks: `uv run pre-commit run --all-files`.
- CLI: `uv run tydomio-cli run`, configured by `TYDOM_IP`, `TYDOM_PASSWORD`,
  `TYDOM_MAC_ADDRESS`. It connects to the real box: ask the user first.
- Capture: `uv run tydomio-capture [-o DIR] [--tydom-ip IP] [--tydom-mac MAC]`, the
  password from `TYDOM_PASSWORD` or a prompt. GET only, anonymized; it writes
  `./tydom-capture-<YYYYMM>-<hex>/` by default. It connects to the real box: ask the
  user first, and review the capture before sharing it.
- Catalog: `uv run python -m tydomio.capture.catalog CAPTURE_DIR tests/usages.json`
  merges the usage descriptors of a capture into the catalog the usage models are
  tested against; on a conflict it writes nothing and exits 1. Review the diff.
- Tests: `uv run coverage run -m pytest`, then `uv run coverage report`.

## Module map

Current state, kept up to date with each change.

| Module | Responsibility | May depend on |
|---|---|---|
| `tydomio/client.py` | `AsyncTydomClient`: websocket loop over an injected authorization provider, connection factory and sleep, ping, sending requests, routing pushed requests to the handlers subscribed to their type or a base class | `network` (defaults), `pending_requests`, `protocol`, `requests`, `response`, `error`, websockets (exception types only) |
| `tydomio/protocol.py` | Conversion of the bytes received on the websocket into messages: prefix, start line, headers, chunked content | `requests`, `response`, `error` |
| `tydomio/pending_requests.py` | `PendingRequests`: futures of the requests waiting for a response, matched by transaction id | `response` |
| `tydomio/network.py` | Thin adapters to the real network: TLS context, digest authorization (httpx), websocket connection (websockets); excluded from coverage | `error`, httpx, websockets |
| `tydomio/requests.py` | Requests sent by the client, with their serialization to bytes, including the typed writes `WriteEndpoint`, `WriteGroup` and `WriteArea` (target and write model checked, revalidated, JSON body, names-only INFO log when sent, through `ClientRequest.log_sending`), and requests pushed by the Tydom | `response`, `installation`, `model`, `error`, `globals` |
| `tydomio/response.py` | Responses from the Tydom, parsed by type | `model`, `error` |
| `tydomio/installation.py` | `Installation` from `/configs/file`: usage per (device id, endpoint id) and per group id, and area ids; hands out `EndpointRef[U]`, `GroupRef[U]` and `AreaRef` (int ids, usage checked), and types one endpoint or area of pushed data | `model`, `error` |
| `tydomio/model/config.py` | pydantic model of `/configs/file` | pydantic |
| `tydomio/model/data.py` | pydantic model of `/devices/data` and `/areas/data` | pydantic |
| `tydomio/model/usages/__init__.py` | `USAGES` registry, usage name to model, and `parse_usage` from the generic `Data` list (`None` for an unknown usage); `WRITES`, read model to its write model, and `NOT_WRITTEN`, the writable catalog fields and commands left out with why | `model.data`, `model.usages.*` |
| `tydomio/model/usages/base.py` | `Reading[T]` (value and validity), `TydomEnum` (unknown values kept as `str`, logged), `UsageModel` base with `from_data` and its `unknown` fields; `UsageWrite[U]` base of the write models (strict, bounds, at least one field, `InvalidWrite` without values, Tydom body) and `member_of`, the strict enum validator | `model.data`, `error`, pydantic, pydantic-core |
| `tydomio/model/usages/thermic.py` | `ThermicZone` and `ThermicWrite`, the regulation fields read and written, and their enums, shared by a `boiler` heating zone and an area | `model.usages.base`, pydantic |
| `tydomio/model/usages/<usage>.py` | Typed read model of one usage (`shutter`, `boiler`, `re2020_control_boiler`, `conso`, `sensor_sun`, `weather`) or of areas (`area`), with its enums, and its write model when it may be written (`ShutterWrite`, `BoilerWrite`, `Re2020ControlBoilerWrite`, `AreaWrite`) | `model.usages.base`, pydantic; `boiler` and `area` also `model.usages.thermic` |
| `tydomio/error.py` | Exception hierarchy (`TydomIoError`) | — |
| `tydomio/globals.py` | `DEV_MODE` flag from the environment | — |
| `tydomio/cli.py` | Temporary debug CLI to try the library on a real Tydom; not to be extended, the real CLI will be designed later | `client`, `requests` |
| `tydomio/capture/jsonvalue.py` | `JsonValue` and the strict JSON parser (UTF-8, no NaN or Infinity, depth 64) | `error` |
| `tydomio/capture/fetch.py` | Endpoint table, read-only `Fetcher` connection routine, `run_capture` with its deadline | `client`, `requests`, `response`, `error`, `capture.jsonvalue` |
| `tydomio/capture/policy.py` | Anonymization rules per endpoint, pure data, and their path parser | — |
| `tydomio/capture/anonymize.py` | Pure anonymization of the bodies by the policy: id map, names, keys, keep constraints, report of redacted leaves and renamed keys | `capture.policy`, `capture.jsonvalue` |
| `tydomio/capture/store.py` | What is written: manifest and envelopes, secrets scan (password, MAC, IP) and their scrubbing, output directory, owner-only files | `capture.fetch`, `capture.anonymize`, `capture.policy`, `capture.jsonvalue` |
| `tydomio/capture/catalog.py` | Catalog of the field and command descriptors per usage, extracted from a capture and merged into `tests/usages.json` (enum values unioned, other differences are conflicts); `python -m` entry point | `error`, `capture.jsonvalue`, pydantic |
| `tydomio/capture/cli.py` | `tydomio-capture` entry point: arguments, password, logging with its secret filter, DEV_MODE guard, refusal to write on a secret found, exit codes, summary | `client`, `globals`, `capture.*` |
