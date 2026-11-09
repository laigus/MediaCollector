from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    max_concurrent_downloads: int = 5
    port: int = 8000
    db_path: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def get_db_path(self) -> str:
        if self.db_path:
            return self.db_path
        return str(Path(__file__).parent.parent / "data" / "media_collector.db")


settings = Settings()
