from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import init_db
from app.api import (
    analytics,
    anomalies,
    chat,
    collections,
    forecast,
    ingest,
    reports,
    whatif,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


# Create the FastAPI application FIRST
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)


# Configure CORS AFTER creating the app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Register API routers
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