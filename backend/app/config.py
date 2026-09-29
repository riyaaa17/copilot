from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "CFO Copilot"
    database_url: str = "sqlite:///./copilot.db"
    groq_api_key: str = ""
    model_fast: str = "openai/gpt-oss-20b"       # extraction, cheap tasks
    model_smart: str = "openai/gpt-oss-120b"   # orchestration, reporting
    cors_origins: list[str] = ["http://localhost:5173"]
    forecast_weeks: int = 13
    monte_carlo_runs: int = 1000


@lru_cache
def get_settings() -> Settings:
    return Settings()