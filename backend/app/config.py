from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    extraction_model: str = "claude-sonnet-5"
    rxnorm_base_url: str = "https://rxnav.nlm.nih.gov/REST"
    db_path: str = "./medconcierge.sqlite3"
    review_confidence_threshold: float = 0.6
    pdf_render_dpi: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
