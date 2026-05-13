from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_name: str = "ClinicFlow"
    environment: str = "development"
    debug: bool = False
    secret_key: str

    # Database
    database_url: str  # postgresql+asyncpg://user:pass@host/db

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # OpenAI
    openai_api_key: str = ""
    openai_sms_model: str = "gpt-4o-mini"
    openai_analysis_model: str = "gpt-4o"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_starter_price_id: str = ""
    stripe_growth_price_id: str = ""

    # Sentry
    sentry_dsn: str = ""

    # S3 / MinIO
    s3_bucket: str = "clinicflow"
    s3_region: str = "us-east-1"
    aws_access_key_id: str = "minioadmin"
    aws_secret_access_key: str = "minioadmin"
    s3_endpoint_url: str = "http://localhost:9000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
