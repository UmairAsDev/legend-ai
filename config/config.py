from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    
    #Postgres Database Configuration
    
    PGHOST : str
    PGUSER : str
    PGDATABASE : str
    PGPASSWORD : str
    PGPORT : int
    
    # Sql Database Configuration
    
    DB_USERNAME : str
    DB_PASSWORD : str
    DB_NAME : str
    DB_HOST : str
    DB_PORT : str
    
    #Open AI Configuration
    
    OPENAI_API_KEY : str
    TEMPERATURE : float
    MAX_TOKENS : int
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )
    
    
setting = Settings() #type:ignore