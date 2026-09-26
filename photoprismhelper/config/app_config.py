from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    app_name: str
    debug: bool
    database_url: str
    photoprism_base_url: str
    photoprism_username: str
    photoprism_password: str
    photoprism_api_token: str
    photoprism_verify_ssl: bool
    photoprism_timeout_seconds: float
    photoprism_originals_path: str
