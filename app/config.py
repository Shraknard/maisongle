import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.parent


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/maisongle"

    # Georisques API
    georisques_api_base_url: str = "https://georisques.gouv.fr/api/v1"

    # Leboncoin (DataDome-protected JSON API). Disabled until a valid datadome
    # cookie is supplied: the cookie is minted by the DataDome JS in a real
    # browser and is IP-bound, so paste one from your own browser session (same
    # residential IP the app runs on). The User-Agent must match that session.
    leboncoin_enabled: bool = False
    leboncoin_datadome: str = ""
    leboncoin_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
