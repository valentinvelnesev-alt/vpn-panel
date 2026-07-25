from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки процесса — только инфраструктура.

    Всё, что пользователь меняет в работе (Remnawave, бот, цены, платёжки),
    живёт в БД и правится через панель, а не через .env.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    deploy_mode: Literal["domain", "ip"] = "ip"
    panel_domain: str = ""
    panel_port: int = 4250

    database_url: str
    redis_url: str = "redis://redis:6379/0"

    secret_key: str
    encryption_key: str
    access_token_ttl_minutes: int = 60
    refresh_token_ttl_days: int = 14

    tz: str = "Europe/Moscow"
    log_level: str = "INFO"

    backup_dir: str = "/app/backups"
    upload_dir: str = "/app/uploads"
    openapi_spec_path: str = Field(
        default="/app/shared/openapi/remnawave-2.8.1.json",
        description="Спека Remnawave, по которой сгенерирован клиент.",
    )

    @property
    def https_enabled(self) -> bool:
        """В режиме ip трафик идёт по HTTP — cookie нельзя помечать Secure."""
        return self.deploy_mode == "domain"

    @property
    def public_url(self) -> str:
        if self.deploy_mode == "domain":
            return f"https://{self.panel_domain}"
        return f"http://localhost:{self.panel_port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
