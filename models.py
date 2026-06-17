from pydantic import BaseModel


class RouteRequest(BaseModel):
    accident_lat: float
    accident_lon: float

    source_lat: float
    source_lon: float

    destination_lat: float
    destination_lon: float