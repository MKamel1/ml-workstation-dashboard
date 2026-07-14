---
name: dashboard-monitor
description: Monitor and control the ML workstation health dashboard (GPU/CPU/memory/storage/network/fan metrics, bottleneck alerts, historical data export, RGB lighting) via its MCP server or REST API. Use when asked to check the workstation's/GPU's/machine's current status or health, investigate a performance issue or bottleneck, look at or export metrics history for a time range, or turn on/off/change the RGB lighting (motherboard/GPU).
---

# Dashboard Monitor

Exposes `workstation-dashboard` (a FastAPI app monitoring this ML
workstation) to AI agents two ways: an MCP server (`mcp_server/`, 8 tools,
preferred) and, as a fallback when MCP tools aren't loaded, the dashboard's
own REST API directly over HTTP.

## Check which path is available

If tools named `get_current_metrics`, `get_history`, `set_lighting`, etc.
are already available, use them directly — their docstrings cover
parameters and behavior, don't re-derive that here. Otherwise fall back to
`curl` against the REST API; see [references/rest-api.md](references/rest-api.md)
for every endpoint, response shape, and curl examples. Both paths hit the
exact same dashboard — same data, same effects, just a different transport.

Base URL: `http://127.0.0.1:8000` if the agent runs on the workstation
itself, otherwise the workstation's Tailscale address (ask the user, or
check `tailscale ip -4` on the workstation).

### Those tools come from an MCP server, not this skill

This skill only documents how to use the dashboard — it doesn't provide
the tools itself and can't connect anything. The tools come from a
separate MCP server, `workstation-dashboard`, registered with Claude Code
at user scope (`~/.claude.json`, so available in every project, not just
this repo). Its code: `~/ai-projects/workstation-dashboard/mcp_server/`.

If the tools aren't showing up, check `claude mcp list` for
`workstation-dashboard`. If it's missing (removed, or a different
machine/account), register it — confirm with the user first, since this
edits their global Claude Code config:

```bash
claude mcp add workstation-dashboard -s user -- \
  /home/omar/ai-projects/workstation-dashboard/mcp_server/venv/bin/python \
  /home/omar/ai-projects/workstation-dashboard/mcp_server/server.py
```

A server registered this way only takes effect in a **new** Claude Code
session — not the one that ran the command. Use the REST API fallback for
the rest of the current session regardless of whether registration was
just run.

## Checking machine status / health

Call `get_current_metrics` (or `curl $BASE/api/metrics`). Read
`bottlenecks` first — it's the dashboard's own rule-based judgment of
what's actually wrong (data-preprocessing-bound, thermal throttling, swap
pressure, VRAM near-full, etc.), already correlating multiple signals so
don't re-derive "is this bad" from raw numbers alone; use raw GPU/CPU/
memory/storage/network/fan values as the supporting detail.

The `ml.active_processes` field lists what's actually running (framework,
VRAM/GPU util per process) — check this before concluding the GPU is
"idle" or "busy", since utilization alone doesn't say what's using it.

## Investigating a past issue / exporting history

A single history row (every component) is ~6KB — at 1 sample/sec that's
~20MB/hour, so there are two different tools for two different sizes of
question. Don't use one where the other fits:

1. `get_db_stats` first to see what range of history actually exists
   (`oldest_timestamp`/`newest_timestamp`) before assuming a window is
   available.
2. **A quick look** (a handful of recent samples, read directly): `get_history(start, end, limit)`.
   Returns the actual rows as the tool result. Capped at 50 rows
   server-side regardless of what's requested — if more is genuinely
   needed, that's a sign to use export_history instead, not to ask for a
   higher limit.
3. **A real export** (e.g. "what happened during last night's training
   run", anything more than a quick check): `export_history(output_path,
   start, end, components, limit)`. Writes the data to a JSON file at
   `output_path` (an absolute path you choose — e.g. your own scratch
   directory; this tool has no notion of "the current directory") and
   returns only a small summary (row count, file size, path) as the tool
   result, NOT the data. Read or grep the written file for specific
   values rather than expecting the call itself to hand back the data —
   a real range can be tens of megabytes, far too large for a tool
   result/an agent's context.
4. Narrow `components` to just what's relevant (e.g.
   `"gpu,cpu,bottlenecks"`) — the main lever for keeping an export small,
   independent of the time range.
5. Timestamps are unix seconds. Convert human time ranges ("last night",
   "the last 2 hours") to unix seconds before calling — don't pass
   natural-language strings.
6. The dashboard itself has to build the full result in memory before
   export_history can write it to a file — a 24-hour all-components
   export is ~250MB server-side (measured), not just a client-side
   concern. For a multi-day request, narrow `components` first, or pull
   it in day-sized chunks, rather than requesting the whole range with
   everything at once.

Valid components: `gpu, cpu, memory, storage, ml, fans, network,
bottlenecks, anomalies`. An unrecognized component name is silently
dropped, not an error — if the exported data is missing something
expected, double-check the spelling against this list.

## Controlling RGB lighting

This physically changes lights on the machine — reasonable to do directly
when asked ("turn the lights red", "turn them off"), but don't change
lighting as a side effect of an unrelated task.

1. Call `get_lighting_modes` first if the user names a pattern (Breathing,
   Wave, Rainbow, ...) — confirms the exact name and whether it has a
   speed control, so `set_lighting` doesn't silently fall back to Direct
   because of a name mismatch (mode names aren't case-sensitive, but must
   otherwise match exactly).
2. Call `set_lighting(power="on", mode=..., color="#rrggbb", brightness=0-100, speed=0-100)`.
   `color` must be a 6-digit hex string. `speed` only affects modes that
   support animation (see step 1's `has_speed`); harmless but ineffective
   otherwise.
3. `set_lighting(power="off")` turns everything off; the last
   mode/color/brightness/speed are remembered dashboard-side for next
   time `power="on"` is sent without specifying them again.
4. `get_lighting_state` reads back the actual current state if unsure
   whether a previous call took effect.

If `available` comes back `false` from any lighting call/state, OpenRGB
isn't reachable on the workstation — nothing to fix agent-side, report it
rather than retrying.
