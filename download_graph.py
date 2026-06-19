"""
CrowdFlow AI — Road Graph Builder (Bengaluru)
==============================================
Downloads the Bengaluru OSMnx drive network and enriches each edge with
traffic-event attributes derived from the Astram event dataset:

  event_count          — total incidents snapped within snap_dist metres
  accident_count       — subset: event_cause == "accident"
  road_closure_count   — subset: requires_road_closure == True
  high_priority_count  — subset: priority == "High"
  active_event_count   — subset: status == "active"
  dominant_cause       — most frequent event_cause on that edge
  event_weight         — composite congestion weight (0 = clear, higher = worse)
  travel_time_weighted — free-flow travel_time * (1 + event_weight)

Usage
-----
    python build_graph.py [--csv PATH] [--snap-dist 50] [--output bengaluru.graphml]
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import osmnx as ox
import pandas as pd
from scipy.spatial import cKDTree


# ── Severity weights ────────────────────────────────────────────────────────
CAUSE_WEIGHT = {
    "accident":          3.0,
    "road_conditions":   2.5,
    "construction":      2.0,
    "water_logging":     2.0,
    "tree_fall":         1.8,
    "pot_holes":         1.5,
    "congestion":        1.5,
    "public_event":      1.2,
    "procession":        1.2,
    "vip_movement":      1.0,
    "protest":           1.0,
    "vehicle_breakdown": 0.8,
    "debris":            0.7,
    "others":            0.5,
}
PRIORITY_WEIGHT   = {"High": 1.5, "Low": 0.5}
CLOSURE_WEIGHT    = 4.0   # added if requires_road_closure == True
ACTIVE_MULTIPLIER = 1.3   # multiplier for still-active events


def load_events(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)

    # Keep only rows with valid coordinates inside greater Bengaluru
    df = df.dropna(subset=["latitude", "longitude"])
    df = df[
        df["latitude"].between(12.7, 13.35)
        & df["longitude"].between(77.25, 77.85)
    ].copy()

    # Normalise free-text fields
    df["event_cause"] = df["event_cause"].str.strip().str.lower().fillna("others")
    df["priority"]    = df["priority"].fillna("Low")
    df["status"]      = df["status"].fillna("closed")
    df["requires_road_closure"] = df["requires_road_closure"].astype(str).str.lower() == "true"

    print(f"  Loaded {len(df):,} valid events from {csv_path}")
    return df


def compute_event_weight(group: pd.DataFrame) -> float:
    """Aggregate multiple events on one edge into a single float weight."""
    weight = 0.0
    for _, row in group.iterrows():
        cause   = str(row.get("event_cause", "others")).lower()
        w       = CAUSE_WEIGHT.get(cause, 0.5)
        w      *= PRIORITY_WEIGHT.get(row.get("priority", "Low"), 0.5)
        if row.get("requires_road_closure"):
            w += CLOSURE_WEIGHT
        if str(row.get("status", "closed")).lower() == "active":
            w *= ACTIVE_MULTIPLIER
        weight += w
    return round(weight, 4)


def snap_events_to_edges(G, df: pd.DataFrame, snap_dist: float):
    """
    For every event point find the nearest graph edge within `snap_dist` metres.
    Returns a dict: (u, v, key) → list[row index].
    """
    # Build a KD-tree over edge midpoints
    edges = [(u, v, k, d) for u, v, k, d in G.edges(keys=True, data=True)]
    mid_lats, mid_lons = [], []
    for u, v, k, d in edges:
        n1 = G.nodes[u]
        n2 = G.nodes[v]
        mid_lats.append((n1["y"] + n2["y"]) / 2)
        mid_lons.append((n1["x"] + n2["x"]) / 2)

    # Approximate metres-per-degree (Bengaluru latitude ~13°)
    lat0   = np.mean(mid_lats)
    m_lat  = 111_320.0
    m_lon  = 111_320.0 * np.cos(np.radians(lat0))

    # KD-tree coords in metres
    tree_xy = np.column_stack([
        np.array(mid_lats) * m_lat,
        np.array(mid_lons) * m_lon,
    ])
    tree = cKDTree(tree_xy)

    event_xy = np.column_stack([
        df["latitude"].values  * m_lat,
        df["longitude"].values * m_lon,
    ])

    dists, idxs = tree.query(event_xy, k=1, workers=-1)

    edge_events: dict = {}
    for evt_i, (dist, edge_i) in enumerate(zip(dists, idxs)):
        if dist <= snap_dist:
            key = (edges[edge_i][0], edges[edge_i][1], edges[edge_i][2])
            edge_events.setdefault(key, []).append(evt_i)

    snapped = sum(len(v) for v in edge_events.values())
    print(f"  Snapped {snapped:,} / {len(df):,} events to edges "
          f"(snap_dist={snap_dist} m)")
    return edge_events


def enrich_graph(G, df: pd.DataFrame, edge_events: dict):
    """Write per-edge event attributes into the graph in-place."""
    # Default values for edges with no events
    defaults = dict(
        event_count=0,
        accident_count=0,
        road_closure_count=0,
        high_priority_count=0,
        active_event_count=0,
        dominant_cause="none",
        event_weight=0.0,
        travel_time_weighted=None,
    )

    for u, v, k, data in G.edges(keys=True, data=True):
        edge_key = (u, v, k)
        if edge_key not in edge_events:
            for attr, val in defaults.items():
                data[attr] = val
            data["travel_time_weighted"] = data.get("travel_time", 0.0)
            continue

        rows = df.iloc[edge_events[edge_key]]

        data["event_count"]         = len(rows)
        data["accident_count"]      = int((rows["event_cause"] == "accident").sum())
        data["road_closure_count"]  = int(rows["requires_road_closure"].sum())
        data["high_priority_count"] = int((rows["priority"] == "High").sum())
        data["active_event_count"]  = int((rows["status"] == "active").sum())

        cause_counts = rows["event_cause"].value_counts()
        data["dominant_cause"] = cause_counts.index[0] if len(cause_counts) else "none"

        w = compute_event_weight(rows)
        data["event_weight"] = w

        base_tt = data.get("travel_time", 0.0) or 0.0
        data["travel_time_weighted"] = round(base_tt * (1 + w), 4)

    print("  Edge enrichment complete.")
    return G


def print_summary(G):
    impacted = sum(
        1 for _, _, d in G.edges(data=True) if d.get("event_count", 0) > 0
    )
    total = G.number_of_edges()
    print(f"\n  Graph summary")
    print(f"  {'Nodes':<30} {G.number_of_nodes():>8,}")
    print(f"  {'Edges':<30} {total:>8,}")
    print(f"  {'Edges with ≥1 event':<30} {impacted:>8,}  ({100*impacted/total:.1f}%)")

    top_edges = sorted(
        [(d.get("event_weight", 0), d.get("dominant_cause", ""), u, v)
         for u, v, d in G.edges(data=True)],
        reverse=True,
    )[:5]
    print(f"\n  Top 5 highest-weight edges:")
    for w, cause, u, v in top_edges:
        print(f"    u={u}  v={v}  weight={w:.2f}  cause={cause}")


def main():
    parser = argparse.ArgumentParser(description="Build CrowdFlow AI road graph")
    parser.add_argument(
        "--csv",
        default="data.csv",
        help="Path to Astram event CSV",
    )
    parser.add_argument(
        "--snap-dist",
        type=float,
        default=50.0,
        help="Max metres to snap an event to an edge midpoint (default: 50)",
    )
    parser.add_argument(
        "--output",
        default="bengaluru_crowdflow.graphml",
        help="Output GraphML filename",
    )
    args = parser.parse_args()

    # ── 1. Download road network ────────────────────────────────────────────
    print("\n[1/4] Downloading Bengaluru road network …")
    G = ox.graph_from_place(
        "Bengaluru, Karnataka, India",
        network_type="drive",
        simplify=True,
    )
    print(f"  Downloaded: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")

    # ── 2. Add basic travel-time attribute ─────────────────────────────────
    print("\n[2/4] Adding speed / travel-time attributes …")
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)

    # ── 3. Load and snap events ────────────────────────────────────────────
    print(f"\n[3/4] Loading events from {args.csv} …")
    df = load_events(args.csv)
    edge_events = snap_events_to_edges(G, df, snap_dist=args.snap_dist)

    # ── 4. Enrich graph ────────────────────────────────────────────────────
    print("\n[4/4] Enriching graph edges …")
    G = enrich_graph(G, df, edge_events)
    print_summary(G)

    # ── Save ───────────────────────────────────────────────────────────────
    out = Path(args.output)
    ox.save_graphml(G, str(out))
    print(f"\n✓  Saved enriched graph → {out.resolve()}\n")


if __name__ == "__main__":
    main()