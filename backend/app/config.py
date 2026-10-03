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
    enable_verification_pass: bool = True

    # SME panel (app/panel). The synthesis and veto rounds reason over the
    # whole transcript, so they get the stronger model; individual agent
    # turns are short and run on the faster one.
    enable_panel: bool = True
    panel_model: str = "claude-sonnet-5"
    panel_synthesis_model: str = "claude-opus-5"
    # The conversational assistant: short turns, answered many times a day,
    # so it runs on the fast tier rather than the panel's deliberation tier.
    assistant_model: str = "claude-sonnet-5"
    panel_max_seated: int = 8
    panel_concurrency: int = 6


@lru_cache
def get_settings() -> Settings:
    return Settings()
