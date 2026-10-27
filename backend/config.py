import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    provider: str
    ollama_url: str
    ollama_model: str
    gemini_model: str
    gemini_api_key: str | None
    data_file: str


def load_settings() -> Settings:
    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    if provider not in {"ollama", "gemini"}:
        raise ValueError("LLM_PROVIDER must be either 'ollama' or 'gemini'.")

    return Settings(
        provider=provider,
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "gemma3:4b"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        data_file=os.getenv("PAGES_DATA_FILE", "data/pages.json"),
    )
