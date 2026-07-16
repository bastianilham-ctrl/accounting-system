# config/settings.py
# Semua konfigurasi dibaca dari .env

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = Field(..., env="DATABASE_URL")
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "accounting_db"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""
    

    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_SECRET_KEY: str = Field(..., env="API_SECRET_KEY")
    API_DEBUG: bool = False

    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""

    # Email
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_NAME: str = "Finance System"

    # File Storage
    UPLOAD_DIR: str = "./uploads"
    OCR_TEMP_DIR: str = "./temp/ocr"

    # Scraping
    SCRAPE_TIMEOUT: int = 30
    SCRAPE_RETRY: int = 3

    # Scheduler
    SCHEDULER_TIMEZONE: str = "Asia/Jakarta"
    DEPRECIATION_RUN_DAY: int = 1

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
