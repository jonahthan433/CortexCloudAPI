import os
from typing import Any, Dict, List, Optional
from pydantic import AnyHttpUrl, BeforeValidator, Field, PostgresDsn, RedisDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Annotated


def parse_cors(v: Any) -> List[str]:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, (list, str)):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    ENV: str = Field(default="development", env="ENV")
    PROJECT_NAME: str = "CortexCloud API"
    API_V1_STR: str = "/v1"

    # Security & Auth
    JWT_SECRET_KEY: str = Field(default="dev-jwt-secret-key-change-in-production-1234567890", env="JWT_SECRET_KEY")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 1 week
    API_KEY_SALT: str = Field(default="dev-api-key-salt-change-in-production", env="API_KEY_SALT")

    # PostgreSQL Configuration
    POSTGRES_HOST: str = Field(default="localhost", env="POSTGRES_HOST")
    POSTGRES_PORT: int = Field(default=5432, env="POSTGRES_PORT")
    POSTGRES_USER: str = Field(default="postgres", env="POSTGRES_USER")
    POSTGRES_PASSWORD: str = Field(default="postgres", env="POSTGRES_PASSWORD")
    POSTGRES_DB: str = Field(default="cortexcloud", env="POSTGRES_DB")
    DATABASE_URL: Optional[str] = Field(default=None, env="DATABASE_URL")

    # Redis Configuration
    REDIS_HOST: str = Field(default="localhost", env="REDIS_HOST")
    REDIS_PORT: int = Field(default=6379, env="REDIS_PORT")
    REDIS_DB: int = Field(default=0, env="REDIS_DB")
    REDIS_URL: Optional[str] = Field(default=None, env="REDIS_URL")

    # Celery Configuration
    CELERY_BROKER_URL: Optional[str] = Field(default=None, env="CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND: Optional[str] = Field(default=None, env="CELERY_RESULT_BACKEND")

    # CORS Origins
    BACKEND_CORS_ORIGINS: Annotated[
        List[str], BeforeValidator(parse_cors)
    ] = ["*"]

    # Provider API Keys
    OPENAI_API_KEY: Optional[str] = Field(default=None, env="OPENAI_API_KEY")
    ANTHROPIC_API_KEY: Optional[str] = Field(default=None, env="ANTHROPIC_API_KEY")
    GEMINI_API_KEY: Optional[str] = Field(default=None, env="GEMINI_API_KEY")
    GROQ_API_KEY: Optional[str] = Field(default=None, env="GROQ_API_KEY")
    XAI_API_KEY: Optional[str] = Field(default=None, env="XAI_API_KEY")
    NVIDIA_API_KEY: Optional[str] = Field(default=None, env="NVIDIA_API_KEY")
    OPENROUTER_API_KEY: Optional[str] = Field(default=None, env="OPENROUTER_API_KEY")

    # x402 Payment Configuration
    X402_ENABLED: bool = Field(default=True, env="X402_ENABLED")
    WALLET_ADDRESS: Optional[str] = Field(default=None, env="WALLET_ADDRESS")
    X402_FACILITATOR_URL: str = Field(default="https://api.cdp.coinbase.com/platform/v2/x402", env="X402_FACILITATOR_URL")
    X402_FACILITATOR_API_KEY: Optional[str] = Field(default=None, env="X402_FACILITATOR_API_KEY")
    X402_FACILITATOR_API_KEY_ID: Optional[str] = Field(default=None, env="X402_FACILITATOR_API_KEY_ID")
    X402_FACILITATOR_API_KEY_SECRET: Optional[str] = Field(default=None, env="X402_FACILITATOR_API_KEY_SECRET")
    X402_NETWORK: str = Field(default="eip155:8453", env="X402_NETWORK")
    X402_RESOURCE_BASE: str = Field(default="https://cortexcloud.org", env="X402_RESOURCE_BASE")

    @model_validator(mode="after")
    def assemble_db_connection(self) -> "Settings":
        if not self.DATABASE_URL:
            self.DATABASE_URL = f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        return self

    @model_validator(mode="after")
    def assemble_redis_connection(self) -> "Settings":
        if not self.REDIS_URL:
            self.REDIS_URL = f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        if not self.CELERY_BROKER_URL:
            self.CELERY_BROKER_URL = self.REDIS_URL
        if not self.CELERY_RESULT_BACKEND:
            self.CELERY_RESULT_BACKEND = self.REDIS_URL
        return self


settings = Settings()
