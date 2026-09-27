from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    secret_encryption_key: str
    app_password_hash: str
    database_url: str = "sqlite:///data/bot.db"
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    bot_schedule_cron: str = "*/5 9-16 * * MON-FRI"
    # IANA timezone the cron expression is interpreted in. US equity market
    # hours are quoted in Eastern, so the schedule must be evaluated there -
    # under a UTC default a "9-16" cron fires 05:00-12:00 ET and misses the
    # entire afternoon.
    bot_timezone: str = "America/New_York"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()