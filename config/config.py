# config/config.py

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    # Postgres Database Configuration
    PGHOST: str
    PGUSER: str
    PGDATABASE: str
    PGPASSWORD: SecretStr
    PGPORT: int

    # SQL Database Configuration
    DB_USERNAME: str
    DB_PASSWORD: SecretStr
    DB_NAME: str
    DB_HOST: str
    DB_PORT: str

    # OpenAI Configuration
    OPENAI_API_KEY: SecretStr
    TEMPERATURE: float
    MAX_TOKENS: int

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


setting = Settings()  # type: ignore