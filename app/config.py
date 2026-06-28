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

    # Leboncoin (DataDome-protected JSON API). Disabled until a transport is
    # configured. Two transports (see scrapers/transport.py):
    #   "scrapfly" — route through Scrapfly's Web Unlocker (residential IP + ASP
    #                solves DataDome). Automated, home IP never exposed. Needs
    #                scrapfly_api_key. Recommended.
    #   "cookie"   — curl_cffi with a datadome cookie pasted from your own
    #                browser (IP-bound, manual refresh). Fallback.
    leboncoin_enabled: bool = False
    leboncoin_transport: str = "scrapfly"  # "scrapfly" | "cookie"
    # cookie transport
    leboncoin_datadome: str = ""
    leboncoin_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # Scrapfly Web Unlocker (shared by any DataDome source). Get a key at
    # scrapfly.io; residential pool + asp clears DataDome for ~cents at this
    # throttled volume.
    scrapfly_api_key: str = ""
    scrapfly_country: str = "fr"
    scrapfly_proxy_pool: str = "public_residential_pool"
    scrapfly_render_js: bool = False

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
