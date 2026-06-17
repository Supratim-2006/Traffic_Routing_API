from fastapi import HTTPException, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from routing import G, route_to_nearest_main_road, get_immediate_local_bypass

app = FastAPI(title="Traffic Diversion API")


@app.get("/")
def home():
    return {"message": "API working"}


class MainRoadRequest(BaseModel):
    current_lat: float
    current_lon: float


class BypassRequest(BaseModel):
    accident_lat: float
    accident_lon: float


@app.post("/api/routes/nearest-main-road")
def nearest_main_road_endpoint(data: MainRoadRequest):        # ✅ renamed
    result = route_to_nearest_main_road(G, data.current_lat, data.current_lon)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"status": "success", "data": result}


@app.post("/api/routes/local-bypass")
def local_bypass_endpoint(data: BypassRequest):               # ✅ renamed
    result = get_immediate_local_bypass(G, data.accident_lat, data.accident_lon)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"status": "success", "data": result}


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)