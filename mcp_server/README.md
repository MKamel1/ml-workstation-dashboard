# Dashboard MCP Server

Exposes the ML workstation dashboard to Claude and other MCP-compatible AI
agents as 8 tools. It's a thin client: every tool is a direct HTTP call to
the dashboard's existing REST API (`app.py`) — no metrics collection,
database, or hardware-control logic lives here. If the dashboard's API
changes, update `server.py`'s calls, not the other way around.

## Tools

| Tool | Wraps | Purpose |
|---|---|---|
| `get_current_metrics` | `GET /api/metrics` | Live snapshot: GPU/CPU/memory/storage/network/fans/ML + bottleneck alerts |
| `get_dashboard_config` | `GET /api/config` | Update interval + alert thresholds |
| `get_db_stats` | `GET /api/db/stats` | How much history is persisted |
| `get_history` | `GET /api/history` | Query persisted history for a time range (small, returned inline, capped at 50 rows) |
| `export_history` | `GET /api/export/history` | Filtered range export, written to a file on disk (returns a summary, not the data) |
| `get_lighting_state` | `GET /api/lighting` | Current RGB power/pattern/color/brightness/speed |
| `get_lighting_modes` | `GET /api/lighting/modes` | Which patterns the hardware supports, and which animate (have a speed) |
| `set_lighting` | `POST /api/lighting` | Turn RGB on/off, set pattern/color/brightness/speed |

`get_current_metrics` deliberately excludes anomaly alerts — those depend on
a rolling window fed only by the dashboard's own live websocket stream, and
an agent polling this tool must not perturb that baseline (same reasoning
`/api/metrics` already uses internally; see `app.py`'s `collect_raw_metrics`).

`export_history` writes to a file rather than returning data directly,
unlike every other tool: a single history row (every component) is ~6KB,
so even one hour of full-range history is ~20MB — far too large for a
tool result an agent would otherwise have to hold in context. `get_history`
stays inline but is capped at 50 rows for the same reason; anything larger
should go through `export_history` instead.

## Setup

```bash
cd mcp_server
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Configuration (environment variables, both optional):

- `DASHBOARD_URL` — base URL of the dashboard. Default `http://127.0.0.1:8000`.
  Set this if the agent runs somewhere other than the workstation itself
  (e.g. `http://100.90.56.75:8000` over Tailscale).
- `DASHBOARD_TIMEOUT` — HTTP request timeout in seconds. Default `10`.

## Registering with an MCP client

**Claude Code** (this machine, all projects):
```bash
claude mcp add workstation-dashboard -s user -- \
  /path/to/mcp_server/venv/bin/python /path/to/mcp_server/server.py
```

**Claude Desktop** — add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "workstation-dashboard": {
      "command": "/path/to/mcp_server/venv/bin/python",
      "args": ["/path/to/mcp_server/server.py"]
    }
  }
}
```

**Any other MCP client**: same stdio command/args pattern — it's a
standard MCP server over stdio, nothing Claude-specific about it.

To reach the dashboard remotely (agent not running on the workstation
itself), add an `env` block with `DASHBOARD_URL` pointing at the
workstation's Tailscale address instead of localhost.

## Verifying it works

```bash
venv/bin/python test_server.py
```

Spawns the server as a real subprocess and drives it over the actual MCP
stdio protocol (not just calling the Python functions directly), the same
way a real client would. Requires the dashboard to actually be running.
