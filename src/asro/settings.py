from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    sec_user_agent: str = "AI-Systemic-Risk-Observatory/0.1 contact@example.com"
    database_path: Path = Path("data/monitor.db")
    # The packaged default; point ASRO_CONFIG_PATH at your own copy to customise.
    config_path: Path = Path(str(files("asro") / "default.toml"))
    poll_interval_minutes: int = 60
    openai_api_key: str = ""
    # FRED API key. Required only for genuine ALFRED vintages: without it the control
    # layer can still fetch series, but every revised series is today's revision and the
    # calibration gate blocks on it.
    fred_api_key: str = ""
    review_model: str = "gpt-4.1-mini"

    model_config = SettingsConfigDict(
        env_prefix="ASRO_",
        env_file=".env",
        extra="ignore",
    )


def load_project_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)
