from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost:5432/vozvrat"
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    MAIL_IMAP_HOST: str = "imap.mail.ru"
    MAIL_IMAP_PORT: int = 993
    MAIL_LOGIN: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FOLDER: str = "INBOX"
    MAIL_CHECK_INTERVAL_MINUTES: int = 5

    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 20

    DEBUG: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


settings = Settings()
