# Dashboard REST API (fallback when MCP tools aren't loaded)

All endpoints are unauthenticated HTTP on the dashboard's base URL (default
`http://127.0.0.1:8000`). Replace `$BASE` in examples with it.

## Contents
- [GET /api/metrics](#get-apimetrics) — live snapshot
- [GET /api/config](#get-apiconfig) — thresholds + update interval
- [GET /api/db/stats](#get-apidbstats) — how much history exists
- [GET /api/history](#get-apihistory) — query recent history
- [GET /api/export/history](#get-apiexporthistory) — filtered range export
- [GET /api/lighting](#get-apilighting) — current lighting state
- [GET /api/lighting/modes](#get-apilightingmodes) — supported patterns
- [POST /api/lighting](#post-apilighting) — control lighting

## GET /api/metrics

Live snapshot: `timestamp, gpu[], cpu, memory, storage, ml, fans, network,
bottlenecks[]`. No `anomalies` key — those only ride the dashboard's own
live websocket stream, not this polling endpoint.

```bash
curl -s "$BASE/api/metrics" | python3 -m json.tool
```

## GET /api/config

```json
{"update_interval": 1.0, "thresholds": {"gpu": {...}, "cpu": {...}, "memory": {...}, "storage": {...}}}
```

## GET /api/db/stats

```json
{"total_records": 71987, "oldest_timestamp": 1783825671, "newest_timestamp": 1784044699, "time_span_hours": 60.8}
```

Timestamps are unix seconds. Check this before querying a time range to
know what's actually available.

## GET /api/history

Query params: `start`, `end` (unix seconds, default last 1h/now),
`limit` (default 1000).

```bash
curl -s "$BASE/api/history?start=1784040000&end=1784043600&limit=500"
```

Returns `{"data": [...], "count": N}`, each row has every component
(`gpu, cpu, memory, storage, ml, fans, network, bottlenecks, anomalies`).

## GET /api/export/history

Same query params as `/api/history`, plus `components` (comma-separated
subset of `gpu,cpu,memory,storage,ml,fans,network,bottlenecks,anomalies`;
omit for all) and a much higher default `limit` (200000) meant for a full
range export.

```bash
curl -s "$BASE/api/export/history?start=1784040000&end=1784043600&components=gpu,cpu,bottlenecks"
```

Returns `{"export_time", "start", "end", "components": [...], "count", "data": [...]}`
— each row only has `timestamp` + the requested components.

If `components` is given but none of it matches a real component name,
this returns **400** with an error listing valid names — check the
response status, don't assume 200.

## GET /api/lighting

```json
{"available": true, "power": "off", "mode": "direct", "color": "#ffffff", "brightness": 100, "speed": 50}
```

`available: false` means OpenRGB isn't reachable on the workstation —
nothing else in this response is meaningful in that case.

## GET /api/lighting/modes

```json
{"modes": [{"name": "Direct", "has_speed": false}, {"name": "Wave", "has_speed": true}, ...]}
```

Call this before `POST /api/lighting` with a specific pattern name, to
confirm it exists and whether `speed` will have any effect.

## POST /api/lighting

Body: `{"power": "on"|"off", "mode"?, "color"?, "brightness"?, "speed"?}`.
`mode`/`color`/`brightness`/`speed` are only meaningful when `power: "on"`
(ignored otherwise). `mode` defaults to `"direct"`, `color` to `"#ffffff"`,
`brightness`/`speed` to `100`/`50` if omitted.

```bash
curl -s -X POST "$BASE/api/lighting" \
  -H "Content-Type: application/json" \
  -d '{"power":"on","mode":"wave","color":"#00ffaa","brightness":80,"speed":70}'

curl -s -X POST "$BASE/api/lighting" -H "Content-Type: application/json" -d '{"power":"off"}'
```

Returns the resulting state (same shape as `GET /api/lighting`). A `400`
means invalid input (bad hex color, brightness/speed outside 0-100, or
`power` not `"on"`/`"off"`) — the response body's `error` field says which.
