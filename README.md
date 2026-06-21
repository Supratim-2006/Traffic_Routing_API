# CrowdFlow AI — Traffic Diversion API

Event-aware traffic routing for Bengaluru. A street-network graph (OSMnx/OpenStreetMap) is enriched with live incident data so routes can be ranked and re-routed around accidents, road closures, and other disruptions instead of relying on free-flow distance alone.

## How it works

1. **Build the road graph** (`download_graph.py`) — downloads the Bengaluru drive network from OpenStreetMap via OSMnx, adds speed/travel-time attributes, snaps incident events from a CSV onto the nearest edges, and saves an enriched `.graphml` file.
2. **Serve routes** (`routing.py` + `main.py`) — loads the enriched graph (downloading it from Google Drive if not present locally), re-derives event weights from the live events CSV, and exposes three Dijkstra-based routing functions through a FastAPI app.

Every edge in the graph carries a `travel_time_weighted` value — its free-flow travel time inflated by nearby incident severity — so routing naturally avoids congested or blocked roads.

---

## Project Structure

| File | Purpose |
|---|---|
| `download_graph.py` | One-time/offline script: downloads the Bengaluru OSMnx drive network, snaps incident events from a CSV to graph edges, computes per-edge event weights, and saves the result as a `.graphml` file. |
| `routing.py` | Loads the graph at API startup, builds a simplified `DiGraph` with event-aware weights, and implements the three core routing functions. |
| `models.py` | Pydantic request/response schemas for the API. |
| `main.py` | FastAPI application — wires up the three routing endpoints. |

---

## Installation

```bash
pip install fastapi uvicorn pydantic osmnx networkx numpy pandas scipy gdown
```

> Building the graph from scratch (`download_graph.py`) requires internet access to query OpenStreetMap via OSMnx and can take a while for a city-sized network.

---

## 1. Building the Road Graph

```bash
python download_graph.py --csv data.csv --snap-dist 50 --output bengaluru_crowdflow.graphml
```

| Flag | Default | Description |
|---|---|---|
| `--csv` | `data.csv` | Path to the incident-events CSV (Astram dataset format). |
| `--snap-dist` | `50` | Max distance (metres) to snap an event to the nearest edge midpoint. |
| `--output` | `bengaluru_crowdflow.graphml` | Output GraphML filename. |

### Expected CSV columns

`latitude`, `longitude`, `event_cause`, `priority` (`High`/`Low`), `status` (`active`/`closed`), `requires_road_closure` (`True`/`False`).

### What it computes, per edge

| Attribute | Meaning |
|---|---|
| `event_count` | Total incidents snapped within `snap_dist` metres |
| `accident_count` | Subset where `event_cause == "accident"` |
| `road_closure_count` | Subset where `requires_road_closure == True` |
| `high_priority_count` | Subset where `priority == "High"` |
| `active_event_count` | Subset where `status == "active"` |
| `dominant_cause` | Most frequent `event_cause` on that edge |
| `event_weight` | Composite congestion weight (0 = clear, higher = worse) |
| `travel_time_weighted` | `travel_time × (1 + event_weight)` |

**Event weight formula** — for each event on an edge:

```
weight = CAUSE_WEIGHT[cause] × PRIORITY_WEIGHT[priority]
weight += CLOSURE_WEIGHT          (if requires_road_closure)
weight *= ACTIVE_MULTIPLIER       (if status == "active")
```

summed across all events snapped to that edge. Cause weights range from `accident` (3.0, most severe) down to `others` (0.5); `CLOSURE_WEIGHT = 4.0` and `ACTIVE_MULTIPLIER = 1.3`.

The script prints a summary of node/edge counts, the percentage of edges with at least one event, and the top-5 highest-weight edges.

---

## 2. Running the API

`routing.py` automatically downloads the enriched `.graphml` from Google Drive if it isn't found locally, then rebuilds an event-weighted `DiGraph` from the live events CSV at import time.

### Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `GRAPHML_PATH` | `bengaluru_crowdflow.graphml` | Local path to the graph file (downloaded automatically if missing). |
| `EVENTS_CSV` | `data.csv` | Path to the current incident-events CSV. |
| `SNAP_DIST_M` | `50` | Snap distance (metres) used when re-mapping events onto edges. |

### Start the server

```bash
uvicorn main:app --reload
```

---

## API Reference

### `GET /`
Health check — returns graph node/edge counts.

### `POST /api/routes/nearest-main-road`
From the user's current position, returns the **top-3 nearest main roads** (motorway/trunk/primary/secondary/tertiary), excluding the road they're currently on, ranked by event-weighted travel time. Each result includes turn-by-turn directions with per-step event alerts.

**Request**
```json
{ "current_lat": 12.9716, "current_lon": 77.5946 }
```

### `POST /api/routes/local-bypass`
Given the GPS coordinates of an accident, **removes all edges on that road** and returns the fastest bypass route to the nearest reachable main road, taking remaining live event weights into account.

**Request**
```json
{ "accident_lat": 13.0012, "accident_lon": 77.5873 }
```

### `POST /api/routes/event-aware-route`
Full origin → destination route, weighted by `travel_time_weighted` (Dijkstra). Optionally strips edges with active road closures before routing (falls back to including them if no path exists otherwise).

**Request**
```json
{
  "origin_lat": 12.9716,
  "origin_lon": 77.5946,
  "destination_lat": 13.0827,
  "destination_lon": 77.5877,
  "avoid_active_closures": true
}
```

**Response includes**
- Event-aware estimated travel time
- Free-flow baseline travel time
- Delay caused by incidents
- Number of active closures avoided / remaining on path
- Turn-by-turn directions with per-step event alerts
- List of incident edges along the path (`from_node`, `to_node`, `dominant_cause`, `event_weight`)

All three endpoints return `{"status": "success", "data": {...}}` on success, or HTTP `400` with an `{"error": "..."}` body (e.g. no road found nearby, no path within the routing cutoff) on failure.

---

## Notes

- `routing.py` collapses the raw OSMnx `MultiDiGraph` into a plain `DiGraph`, keeping only the shortest parallel edge between any two nodes, and recomputes event weights independently from `download_graph.py`'s offline enrichment — so the live `EVENTS_CSV` should be kept up to date for the API's weights to reflect current conditions.
- The "top-3 nearest main roads" and "local bypass" searches are capped at a **10-minute** travel-time horizon (`cutoff=600` seconds in `single_source_dijkstra`).
- `models.py` also defines a legacy `RouteRequest` schema kept only for backward compatibility with older API consumers; new integrations should use `EventAwareRouteRequest`.
