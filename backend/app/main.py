from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from app.config import get_settings
from app.db import init_db
from app.api import analytics, anomalies, chat, collections, forecast, ingest, reports, whatif
settings = get_settings()
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://copilot-frontend.vercel.app", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(ingest.router)
app.include_router(analytics.router)
app.include_router(forecast.router)
app.include_router(anomalies.router)
app.include_router(collections.router)
app.include_router(reports.router)
app.include_router(chat.router)
app.include_router(whatif.router)
@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "llm_configured": bool(settings.groq_api_key),
    }