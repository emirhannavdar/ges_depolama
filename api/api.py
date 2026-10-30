from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.epias import epias
from api.visual_crossing import visual_crossing
from api.forecast import router as forecast_router
from api.pricing import router as pricing_router
app = FastAPI(
    title="Epias",
    description="Epias API",
    version="0.0.1"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    epias,
    prefix="/api/v1/epias",
    tags=["epias"]
)

app.include_router(
    visual_crossing,
    prefix="/api/v1/visual_crossing",
    tags=["visual_crossing"]
)

app.include_router(
    forecast_router
)

app.include_router(
    pricing_router
)