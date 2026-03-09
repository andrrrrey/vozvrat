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

    # Фильтрация входящих писем
    # Принимать только письма с XLS/XLSX вложением (рекомендуется: true)
    MAIL_REQUIRE_XLS: bool = True
    # Разрешённые домены/адреса отправителей через запятую (пусто = все)
    # Пример: "supplier.ru,example.com,specific@partner.ru"
    MAIL_ALLOWED_SENDERS: str = ""
    # Ключевые слова в теме письма через запятую (пусто = без фильтра)
    # Пример: "возврат,refund,возвр"
    MAIL_SUBJECT_KEYWORDS: str = ""
    # Максимум писем за один цикл проверки (0 = без ограничений)
    MAIL_FETCH_LIMIT: int = 50

    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 20

    DEBUG: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


settings = Settings()
