"""
main.py — CrowdFlow AI Traffic Diversion API
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models import MainRoadRequest, BypassRequest, EventAwareRouteRequest
from routing import G, route_to_nearest_main_road, get_immediate_local_bypass, get_event_aware_route

app = FastAPI(
    title="CrowdFlow AI — Traffic Diversion API",
    description=(
        "Event-aware traffic routing for Bengaluru. "
        "Edge weights are inflated by live incident severity from the Astram dataset."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
def home():
    return {
        "message": "CrowdFlow AI Traffic Diversion API is running",
        "graph_nodes": G.number_of_nodes(),
        "graph_edges": G.number_of_edges(),
    }


# ── Endpoint 1: Nearest main road ─────────────────────────────────────────────
@app.post("/api/routes/nearest-main-road", tags=["Routing"])
def nearest_main_road_endpoint(data: MainRoadRequest):
    """
    From the user's current position, return the top-3 nearest main roads
    (excluding the road they are on) ranked by event-weighted travel time.

    Each result includes turn-by-turn directions with per-step event alerts.
    """
    result = route_to_nearest_main_road(G, data.current_lat, data.current_lon)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"status": "success", "data": result}


# ── Endpoint 2: Local bypass after accident ───────────────────────────────────
@app.post("/api/routes/local-bypass", tags=["Routing"])
def local_bypass_endpoint(data: BypassRequest):
    """
    Given the GPS coordinates of an accident, block the entire accident road
    and return the fastest bypass route to the nearest main road, taking all
    other live event weights into account.
    """
    result = get_immediate_local_bypass(G, data.accident_lat, data.accident_lon)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"status": "success", "data": result}


# ── Endpoint 3: Full event-aware A→B route ────────────────────────────────────
@app.post("/api/routes/event-aware-route", tags=["Routing"])
def event_aware_route_endpoint(data: EventAwareRouteRequest):
    """
    Route from origin to destination using Dijkstra weighted by
    `travel_time_weighted` (free-flow travel time × event severity multiplier).

    Optionally strips edges with active road closures before routing.

    Response includes:
    - Event-aware estimated travel time
    - Free-flow baseline travel time
    - Delay caused by incidents
    - Per-step event alerts
    - List of incident edges along the path
    """
    result = get_event_aware_route(
        G,
        data.origin_lat,
        data.origin_lon,
        data.destination_lat,
        data.destination_lon,
        avoid_active_closures=data.avoid_active_closures,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"status": "success", "data": result}