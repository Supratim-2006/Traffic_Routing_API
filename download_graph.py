
import osmnx as ox

print("Downloading road network...")

G = ox.graph_from_place(
    "Hyderabad, Telangana, India",
    network_type="drive"
)

ox.save_graphml(
    G,
    "hyderabad.graphml"
)

print("Done")