from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
import asyncio
import json
import time
from typing import Set
import config

# Import metrics collectors
from metrics.gpu_metrics import get_gpu_metrics
from metrics.cpu_metrics import get_cpu_metrics
from metrics.memory_metrics import get_memory_metrics
from metrics.storage_metrics import get_storage_metrics
from metrics.ml_metrics import get_ml_metrics
from metrics.fan_metrics import get_system_fan_metrics
from metrics.network_metrics import get_network_metrics

# Import detectors
from detection.bottleneck_detector import detect_bottlenecks
from detection.anomaly_detector import get_anomaly_detector

# Import database
from database import get_database

# Import lighting control
from lighting_control import get_lighting_controller

# Import fan profile control
from fan_profile_control import get_fan_profile_controller


app = FastAPI(title="Workstation Health Dashboard")


@app.middleware("http")
async def no_cache_for_dashboard_assets(request, call_next):
    """Force the browser to always revalidate index.html/static assets.

    Without this, StaticFiles serves no Cache-Control header at all, which
    lets browsers apply their own heuristic caching -- so a normal refresh
    can silently serve a stale cached dashboard.js/index.html after a
    deploy, with no visible error, while /api/* and /ws (never cached
    anyway) look completely fine. `no-cache` (not `no-store`) still allows
    the existing ETag/Last-Modified conditional-request support to return a
    cheap 304 when nothing changed -- this only forces the revalidation
    round-trip to happen every time, not a full re-download every time.
    """
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Track active WebSocket connections
active_connections: Set[WebSocket] = set()

# Get database instance
db = get_database()


@app.on_event("startup")
async def startup_event():
    """Run on application startup."""
    # Cleanup old metrics (keep last 7 days)
    deleted = db.cleanup_old_metrics(retention_days=7)
    print(f"Cleaned up {deleted} old metric records")
    asyncio.create_task(periodic_cleanup())


async def periodic_cleanup():
    """Delete old metrics once a day so long-running uptimes don't let the table grow unbounded."""
    while True:
        await asyncio.sleep(86400)
        deleted = db.cleanup_old_metrics(retention_days=7)
        print(f"Cleaned up {deleted} old metric records")


@app.on_event("shutdown")
async def shutdown_event():
    """Stop accepting new writes, drain the queued ones, and close the database connection."""
    await asyncio.to_thread(db.close)


@app.get("/")
async def read_root():
    """Serve the main dashboard page."""
    try:
        with open("static/index.html", "r") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Dashboard UI coming soon...</h1>")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time metrics streaming."""
    await websocket.accept()
    active_connections.add(websocket)
    
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    try:
        while True:
            # Collect all metrics with error handling
            try:
                metrics = collect_all_metrics()
                consecutive_errors = 0  # Reset on success
            except Exception as e:
                consecutive_errors += 1
                print(f"[ERROR] Failed to collect metrics (attempt {consecutive_errors}/{max_consecutive_errors}): {e}")
                
                # Send error state to client
                error_metrics = {
                    "timestamp": time.time(),
                    "error": True,
                    "error_message": f"Metrics collection failed: {str(e)}",
                    "gpu": [],
                    "cpu": {},
                    "memory": {},
                    "storage": {},
                    "ml": {},
                    "fans": {},
                    "network": {},
                    "bottlenecks": [],
                    "anomalies": []
                }
                metrics = error_metrics
                
                # If too many consecutive errors, break to trigger reconnection
                if consecutive_errors >= max_consecutive_errors:
                    print(f"[CRITICAL] {max_consecutive_errors} consecutive errors, closing connection")
                    break
            
            # Keep the most recent sample around so /api/export can serve it on demand
            global latest_metrics
            latest_metrics = metrics.copy()
            
            # Store in database with retry logic
            try:
                db.insert_metrics(metrics)
            except Exception as e:
                # Log database errors but don't kill the session
                print(f"[WARNING] Database insert failed: {e}")
                import traceback
                traceback.print_exc()
                
                # Optionally notify client of persistence issue
                if "error" not in metrics:
                    metrics["db_warning"] = "Metrics not persisted to database"
            
            # Send to client
            await websocket.send_text(json.dumps(metrics))
            
            # Wait for next update interval
            await asyncio.sleep(config.UPDATE_INTERVAL)
            
    except WebSocketDisconnect:
        active_connections.remove(websocket)
    except Exception as e:
        print(f"[ERROR] WebSocket error: {e}")
        import traceback
        traceback.print_exc()
        if websocket in active_connections:
            active_connections.remove(websocket)


def collect_raw_metrics() -> dict:
    """Collect raw system metrics and run (pure) bottleneck detection.

    Safe to call from anywhere, any number of times -- unlike
    collect_all_metrics(), this does not touch the stateful anomaly
    detector's rolling window.
    """
    metrics = {
        "timestamp": time.time(),  # Unix epoch timestamp for database queries
        "gpu": get_gpu_metrics(),
        "cpu": get_cpu_metrics(),
        "memory": get_memory_metrics(),
        "storage": get_storage_metrics(),
        "ml": get_ml_metrics(),
        "fans": get_system_fan_metrics(),  # System fans (motherboard sensors)
        "network": get_network_metrics(),
    }
    metrics["bottlenecks"] = detect_bottlenecks(metrics)
    return metrics


def collect_all_metrics() -> dict:
    """Collect all system metrics and run detection algorithms, including the
    stateful anomaly detector.

    This feeds the live anomaly detector's rolling window (see
    detection/anomaly_detector.py), so it must only be called from the real
    periodic tick -- currently the /ws streaming loop. Any other caller that
    just wants current metrics should use collect_raw_metrics() instead, so
    it doesn't skew the live anomaly baseline.
    """
    metrics = collect_raw_metrics()
    metrics["anomalies"] = get_anomaly_detector().update(metrics)
    return metrics


@app.get("/api/metrics")
async def get_current_metrics():
    """REST endpoint to get current metrics (for debugging).

    Does not run anomaly detection: anomalies depend on the live rolling
    window driven by the /ws stream, and an ad-hoc debug poll here must not
    perturb that window. Anomalies are only available via the /ws stream.
    """
    return collect_raw_metrics()


@app.get("/api/config")
async def get_config():
    """Get current configuration."""
    return {
        "update_interval": config.UPDATE_INTERVAL,
        "thresholds": config.THRESHOLDS,
    }


@app.get("/api/lighting")
async def get_lighting():
    """Get the current RGB lighting state (power + mode + color + brightness)."""
    try:
        return get_lighting_controller().get_state()
    except Exception as e:
        return JSONResponse(content={"error": f"Lighting state read failed: {e}"}, status_code=500)


@app.get("/api/lighting/modes")
async def get_lighting_modes():
    """List of RGB pattern names the hardware actually supports."""
    try:
        return {"modes": get_lighting_controller().get_available_modes()}
    except Exception as e:
        return JSONResponse(content={"error": f"Reading lighting modes failed: {e}"}, status_code=500)


@app.post("/api/lighting")
async def set_lighting(payload: dict):
    """Set RGB lighting. Body: {"power": "on"|"off", "mode"?: str, "color"?: "#rrggbb", "brightness"?: 0-100, "speed"?: 0-100}."""
    power = payload.get("power")
    if power == "off":
        try:
            return get_lighting_controller().turn_off()
        except Exception as e:
            return JSONResponse(content={"error": f"Turning off lighting failed: {e}"}, status_code=500)
    elif power == "on":
        try:
            return get_lighting_controller().set_state(
                mode=payload.get("mode", "direct"),
                color_hex=payload.get("color", "#ffffff"),
                brightness=payload.get("brightness", 100),
                speed=payload.get("speed", 50),
            )
        except ValueError as e:
            return JSONResponse(content={"error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse(content={"error": f"Setting lighting failed: {e}"}, status_code=500)
    else:
        return JSONResponse(content={"error": "payload.power must be 'on' or 'off'"}, status_code=400)


@app.get("/api/fans/profile")
async def get_fan_profile():
    """Get the current fan profile mode (quiet/performance/mixed/unknown)."""
    try:
        return get_fan_profile_controller().get_state()
    except Exception as e:
        return JSONResponse(content={"error": f"Reading fan profile failed: {e}"}, status_code=500)


@app.post("/api/fans/profile")
async def set_fan_profile(payload: dict):
    """Set the fan profile. Body: {"mode": "quiet"|"performance"}."""
    mode = payload.get("mode")
    if mode not in ("quiet", "performance"):
        return JSONResponse(content={"error": "mode must be 'quiet' or 'performance'"}, status_code=400)
    try:
        return get_fan_profile_controller().set_profile(mode)
    except Exception as e:
        return JSONResponse(content={"error": f"Setting fan profile failed: {e}"}, status_code=500)


@app.get("/api/history")
async def get_history(start: int = None, end: int = None, limit: int = 1000):
    """Get historical metrics."""
    try:
        metrics = db.query_metrics(start_time=start, end_time=end, limit=limit)
        return JSONResponse(content={"data": metrics, "count": len(metrics)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# Holds the last metrics sample sent over the websocket, for /api/export
latest_metrics = {}

@app.get("/api/db/stats")
async def get_db_stats():
    """Get database statistics."""
    try:
        stats = db.get_stats()
        return JSONResponse(content=stats)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/export")
def export_current_metrics():
    """Export the most recent metrics sample as a downloadable JSON file."""
    from datetime import datetime
    
    if not latest_metrics:
        return JSONResponse(
            content={"error": "No metrics available yet. Please wait for first update."},
            status_code=503
        )
    
    export_data = {
        "export_time": datetime.now().isoformat(),
        "export_timestamp": latest_metrics.get("timestamp"),
        "dashboard_version": "1.0.0",
        "metrics": latest_metrics
    }
    
    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": f"attachment; filename=dashboard_metrics_{int(latest_metrics.get('timestamp', 0))}.json"
        }
    )


# Every component key a persisted history row can have -- see
# database/__init__.py's query_metrics() for where each one comes from.
_EXPORTABLE_COMPONENTS = {"gpu", "cpu", "memory", "storage", "ml", "fans", "network", "bottlenecks", "anomalies"}


def select_export_components(components_param):
    """Parse a comma-separated components query param into a validated set.

    Returns None if the param was omitted entirely, meaning "export
    everything" -- distinct from being given but matching nothing real
    (e.g. a typo), which returns an empty set so the caller can reject that
    as a 400 rather than silently exporting no components at all.
    """
    if not components_param:
        return None
    requested = {c.strip().lower() for c in components_param.split(",") if c.strip()}
    return requested & _EXPORTABLE_COMPONENTS


@app.get("/api/export/history")
async def export_history(start: int = None, end: int = None, components: str = None, limit: int = 200000):
    """Download historical metrics for a time range as a JSON file, limited
    to the requested components. Body/query: start/end (unix seconds,
    default last 1h/now like /api/history), components (comma-separated
    subset of gpu,cpu,memory,storage,ml,fans,network,bottlenecks,anomalies;
    omit for all).
    """
    from datetime import datetime

    selected = select_export_components(components)
    if selected is None:
        selected = set(_EXPORTABLE_COMPONENTS)
    elif not selected:
        return JSONResponse(
            content={"error": f"No valid components in {components!r}; choose from {sorted(_EXPORTABLE_COMPONENTS)}"},
            status_code=400,
        )

    try:
        rows = db.query_metrics(start_time=start, end_time=end, limit=limit)
    except Exception as e:
        return JSONResponse(content={"error": f"History query failed: {e}"}, status_code=500)

    filtered_rows = [
        {"timestamp": row["timestamp"], **{key: row[key] for key in selected}}
        for row in rows
    ]

    export_data = {
        "export_time": datetime.now().isoformat(),
        "start": start,
        "end": end,
        "components": sorted(selected),
        "count": len(filtered_rows),
        "data": filtered_rows,
    }

    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": f"attachment; filename=dashboard_history_export_{int(time.time())}.json"
        }
    )


# Mount static files
try:
    import os
    static_dir = "static"
    if not os.path.exists(static_dir):
        print(f"[WARNING] Static directory '{static_dir}' not found. Dashboard UI will not be available.")
    else:
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        print(f"[INFO] Static files mounted successfully from '{static_dir}'")
except Exception as e:
    print(f"[ERROR] Failed to mount static files: {e}")
    import traceback
    traceback.print_exc()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host=config.HOST, 
        port=config.PORT,
        log_level="info"
    )
