import yaml
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import BaseModel


class Settings(BaseSettings):
    database_url: str = "postgresql://hardware:hardware123@localhost:5433/hardware_deals"
    proxy_list: str = ""
    scrape_interval_minutes: int = 60
    api_host: str = "0.0.0.0"
    api_port: int = 8001
    discord_webhook_url: str = ""

    class Config:
        env_file = ".env"


settings = Settings()


class ItemSpecs(BaseModel):
    vram_gb: int | None = None
    tdp_w: int | None = None
    cuda_cores: int | None = None
    cores: int | None = None
    threads: int | None = None
    base_clock_ghz: float | None = None
    boost_clock_ghz: float | None = None
    capacity_gb: int | None = None
    type: str | None = None


class SearchItem(BaseModel):
    name: str
    keywords: list[str]
    max_price: int
    category: str
    specs: ItemSpecs = ItemSpecs()


class SourceConfig(BaseModel):
    enabled: bool = True
    rate_limit_seconds: float = 2.0
    max_pages: int = 5
    search_paths: list[str] = ["/informatica"]
    min_price: int = 50


class AppConfig(BaseModel):
    items: list[SearchItem]
    exclude_keywords: list[str] = []
    sources: dict[str, SourceConfig] = {}


def load_config() -> AppConfig:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        data = yaml.safe_load(f)
    return AppConfig(**data)


config = load_config()
