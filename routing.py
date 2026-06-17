import osmnx as ox
import networkx as nx
import os
import gdown

# 1. Load the raw OSMnx MultiDiGraph
GRAPHML_PATH="hyderabad.graphml"

if not os.path.exists(GRAPHML_PATH):
    print("Downloading hyderabad.graphml from Google Drive...")
    gdown.download(
        "https://drive.google.com/file/d/1sWO1EAA_Il9kjVuKBlhD4Hy_lgeqEpKz/view?usp=sharing",
        GRAPHML_PATH,
        quiet=False
)

raw_G = ox.load_graphml(GRAPHML_PATH)

# 2. Convert to clean DiGraph
G = nx.DiGraph()
for u, v, data in raw_G.edges(data=True):
    edge_length = data.get("length", 1.0)
    edge_name = data.get("name", "")
    edge_highway = data.get("highway", "residential")

    if G.has_edge(u, v):
        if edge_length < G[u][v]["length"]:
            G[u][v]["length"] = edge_length
            G[u][v]["name"] = edge_name
            G[u][v]["highway"] = edge_highway
    else:
        G.add_edge(u, v, length=edge_length, name=edge_name, highway=edge_highway)

for node, data in raw_G.nodes(data=True):
    G.add_node(node, **data)


# ── Helper ────────────────────────────────────────────────────────────────────
def build_directions(path, graph):
    """Convert a node path into turn-by-turn direction steps."""
    directions = []
    current_street = None
    street_distance = 0.0

    for i in range(len(path) - 1):
        edge_data = graph.get_edge_data(path[i], path[i + 1]) or {}
        street_name = edge_data.get("name", "")
        if isinstance(street_name, list):
            street_name = " / ".join(street_name) if street_name else "Local Road"
        if not street_name:
            street_name = "Local Road"

        seg_length = edge_data.get("length", 0)

        if street_name != current_street:
            if current_street is not None:
                directions.append({
                    "instruction": f"Continue onto {street_name}",
                    "street": street_name,
                    "distance_meters": round(seg_length, 2)
                })
            else:
                directions.append({
                    "instruction": f"Head along {street_name}",
                    "street": street_name,
                    "distance_meters": round(seg_length, 2)
                })
            current_street = street_name
            street_distance = seg_length
        else:
            street_distance += seg_length
            directions[-1]["distance_meters"] = round(street_distance, 2)

    return directions


def get_current_road_name(graph, lon, lat):
    """Returns the name of the road the given coordinate is on."""
    u, v, _ = ox.distance.nearest_edges(raw_G, lon, lat)
    edge_data = graph.get_edge_data(u, v) or {}
    name = edge_data.get("name", "")
    if isinstance(name, list):
        name = name[0] if name else ""
    return name


def build_main_road_node_map(graph, exclude_road_name=None):
    """
    Returns a dict of { node_id: road_name } for all main road nodes.
    Optionally excludes a specific road by name.
    """
    main_road_types = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary'}
    node_map = {}

    for u, v, data in graph.edges(data=True):
        highway = data.get("highway", "")
        if isinstance(highway, list):
            is_main = any(h in main_road_types for h in highway)
        else:
            is_main = highway in main_road_types

        if not is_main:
            continue

        road_name = data.get("name", "")
        if isinstance(road_name, list):
            road_name = road_name[0] if road_name else ""
        if not road_name:
            road_name = "Unnamed Main Road"

        if exclude_road_name and road_name == exclude_road_name:
            continue

        if u not in node_map:
            node_map[u] = road_name
        if v not in node_map:
            node_map[v] = road_name

    return node_map


# ── Endpoint 1: Find top 3 nearest main roads ─────────────────────────────────
def route_to_nearest_main_road(graph, current_lat, current_lon):
    """
    From the user's current location, find the top 3 nearest main roads
    (excluding the road they are currently on) with turn-by-turn directions.
    """
    try:
        current_road_name = get_current_road_name(graph, current_lon, current_lat)
        main_road_node_names = build_main_road_node_map(graph, exclude_road_name=current_road_name)

        if not main_road_node_names:
            return {"error": "No other main roads found nearby."}

        start_node = ox.distance.nearest_nodes(raw_G, current_lon, current_lat)

        # Single Dijkstra outward from current location
        distances, paths = nx.single_source_dijkstra(
            graph,
            source=start_node,
            weight="length",
            cutoff=5000  # 5km radius
        )

        # Collect top 3 unique road names by shortest distance
        seen_roads = {}
        for node, dist in sorted(distances.items(), key=lambda x: x[1]):
            if node not in main_road_node_names:
                continue
            road_name = main_road_node_names[node]
            if road_name in seen_roads:
                continue
            seen_roads[road_name] = {"distance": dist, "path": paths[node]}
            if len(seen_roads) == 3:
                break

        if not seen_roads:
            return {"error": "No main road reachable within 5km."}

        # Build result list with directions for each road
        results = []
        for road_name, info in seen_roads.items():
            path = info["path"]
            directions = build_directions(path, graph)
            directions.append({
                "instruction": f"Arrive at {road_name}",
                "street": road_name,
                "distance_meters": 0
            })

            results.append({
                "target_main_road": road_name,
                "distance_meters": round(info["distance"], 2),
                "directions": directions,
                "nodes": path
            })

        results.sort(key=lambda x: x["distance_meters"])

        return {
            "currently_on": current_road_name or "Unnamed/Unknown Road",
            "nearest_roads": results
        }

    except Exception as e:
        return {"error": str(e)}


# ── Endpoint 2: Accident bypass ───────────────────────────────────────────────
def get_immediate_local_bypass(graph, accident_lat, accident_lon):
    """
    Accident on a road — block that entire road from the graph, then find
    the nearest main road with turn-by-turn directions avoiding the blocked road.
    """
    try:
        # Step 1: Identify the accident road
        blocked_road_name = get_current_road_name(graph, accident_lon, accident_lat)

        # Step 2: Copy graph and remove all edges of the blocked road
        local_graph = graph.copy()
        edges_to_remove = []

        for u, v, data in local_graph.edges(data=True):
            road_name = data.get("name", "")
            if isinstance(road_name, list):
                road_name = road_name[0] if road_name else ""
            if blocked_road_name and road_name == blocked_road_name:
                edges_to_remove.append((u, v))

        for u, v in edges_to_remove:
            if local_graph.has_edge(u, v):
                local_graph.remove_edge(u, v)

        # Step 3: Build main road node map excluding blocked road
        main_road_node_names = build_main_road_node_map(
            local_graph,
            exclude_road_name=blocked_road_name
        )

        if not main_road_node_names:
            return {"error": "No accessible main road found after blocking the accident road."}

        # Step 4: Single Dijkstra from accident point
        start_node = ox.distance.nearest_nodes(raw_G, accident_lon, accident_lat)

        distances, paths = nx.single_source_dijkstra(
            local_graph,
            source=start_node,
            weight="length",
            cutoff=5000
        )

        # Step 5: Pick the closest reachable main road
        best = None
        for node, dist in sorted(distances.items(), key=lambda x: x[1]):
            if node in main_road_node_names:
                best = (node, dist, paths[node])
                break

        if not best:
            return {"error": "No main road reachable within 5km after blocking accident road."}

        target_node, distance, path = best
        target_road_name = main_road_node_names[target_node]

        # Step 6: Build directions
        directions = build_directions(path, local_graph)
        directions.append({
            "instruction": f"Arrive at {target_road_name}",
            "street": target_road_name,
            "distance_meters": 0
        })

        total_distance = sum(
            local_graph.get_edge_data(path[i], path[i + 1], {}).get("length", 0)
            for i in range(len(path) - 1)
        )

        return {
            "blocked_road": blocked_road_name or "Unknown Road",
            "target_main_road": target_road_name,
            "total_distance_meters": round(total_distance, 2),
            "directions": directions,
            "nodes": path
        }

    except nx.NetworkXNoPath:
        return {"error": "No route found. The accident road may have caused a dead end."}
    except Exception as e:
        return {"error": str(e)}