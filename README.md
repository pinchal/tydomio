# tydomio

Your equipment is not typed yet? See
[Extending the datamodel with new equipment](docs/extending-usages.md).

## MCP server

`tydomio-mcp` lets an MCP client, such as Claude Code, read the state of the home's
Tydom devices and heating areas. The client launches it and talks to it over stdin and
stdout; it has no network port. It keeps one websocket open to the Tydom for the
session, and its `get_state` tool answers from a cache kept up to date by the Tydom's
pushes. On each connection, once it has read the state, it sends `POST /refresh/all`:
without it the Tydom pushes no instantaneous electricity values (`conso`), and the
pushes it starts last as long as that connection. It stops cleanly when the client
closes its stdin.

Install it with the `mcp` extra (`tydomio[mcp]`). It reads `TYDOM_IP`,
`TYDOM_MAC_ADDRESS` and `TYDOM_PASSWORD` from the environment, never from arguments,
and refuses to start without them. Register it at user scope, never project scope (a
project's `.mcp.json` can be committed); the secrets stay in your env file, out of the
Claude config:

```
claude mcp add --scope user tydom -- uv run --env-file <path to your env file> tydomio-mcp
```

Options, fixed for the life of the process:

- `--writes {none,shutters,all}`, default `none`: the write tools to register. `none`
  gives the read-only server. `shutters` adds `set_shutter`, which moves one shutter or
  a shutter group, by its id from `get_state`, with a command (`UP`, `DOWN`, `STOP`...)
  or a position (100 = open, 0 = closed); `get_state` called without `ids` then lists
  the shutter groups too. Each call sends one command, once, never resent, and only
  while the cache is in sync with the Tydom; the tool asks Claude Code to prompt you on
  every call. `all` also adds `set_heating`, which changes the setpoint or mode of one
  heating zone (a `boiler` or `re2020ControlBoiler` endpoint, or a heating area), by its
  id from `get_state`; groups are refused, and so are areas whose usage is not
  `re2020ControlBoiler`.
- `--write-rate N`, default `1`: the most writes per second, a number in `(0, 10]`. A
  value out of range stops the server at start. A write that comes sooner is refused,
  with the time to wait; this delays a `STOP` too.

**Heating confirmation.** The server has no confirmation step of its own. The
`set_heating` description tells the model to state the target, its current and new
values, and to wait for your explicit agreement, flagging the modes (`ANTI_FROST`,
`STOP`, `ABSENCE`) and low setpoints that can leave the home unheated; the tool also
asks Claude Code to prompt you on every call. The description is an instruction to
the model, not a check, and a client that ignores the prompt request shows only its
usual permission prompt. A wrong heating command can leave the home cold: keep
`--writes all` off unless you want it. One supervised `set_heating` test, a setpoint
change and back, has been run against a real Tydom.

**Personal data.** Tool results send the names of the equipment and their states
(shutter positions, heating modes, which can reveal presence) to the MCP client, and on
to the provider of the model it uses. Nothing leaves unless the client calls a tool.
Devices whose usage tydomio does not type, which may include an alarm, are listed by
id, name and kind only, without their state.
