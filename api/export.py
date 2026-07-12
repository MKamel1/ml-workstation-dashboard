"""
Export current dashboard metrics as JSON for reporting and debugging.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
import json
from datetime import datetime

router = APIRouter()

# Store latest metrics (updated by WebSocket)
latest_metrics = {}

def update_latest_metrics(metrics):
    """Called by WebSocket to store latest metrics."""
    global latest_metrics
    latest_metrics = metrics

@router.get("/api/export")
async def export_metrics():
    """Export current metrics as downloadable JSON."""
    if not latest_metrics:
        return JSONResponse(
            content={"error": "No metrics available yet"},
            status_code=503
        )
    
    # Add export metadata
    export_data = {
        "export_time": datetime.now().isoformat(),
        "export_timestamp": latest_metrics.get("timestamp"),
        "metrics": latest_metrics
    }
    
    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": f"attachment; filename=dashboard_metrics_{int(latest_metrics.get('timestamp', 0))}.json"
        }
    )
