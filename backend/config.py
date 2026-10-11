import os
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    site_url: str
    provider: str
    ollama_url: str
    ollama_model: str
    gemini_model: str
    gemini_api_key: str | None
    max_pages: int
    data_file: str


def load_settings() -> Settings:
    site_url = os.getenv("SCHOOL_SITE_URL", "https://vgu.edu.vn/").strip()
    parsed = urlparse(site_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("SCHOOL_SITE_URL must be a valid http or https URL.")
    if parsed.username or parsed.password:
        raise ValueError("SCHOOL_SITE_URL must not contain credentials.")

    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    if provider not in {"ollama", "gemini"}:
        raise ValueError("LLM_PROVIDER must be either 'ollama' or 'gemini'.")

    try:
        max_pages = int(os.getenv("MAX_CRAWL_PAGES", "1000"))
    except ValueError as exc:
        raise ValueError("MAX_CRAWL_PAGES must be an integer.") from exc
    if not 1 <= max_pages <= 5000:
        raise ValueError("MAX_CRAWL_PAGES must be between 1 and 5000.")

    return Settings(
        site_url=site_url,
        provider=provider,
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "gemma3:4b"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        max_pages=max_pages,
        data_file=os.getenv("PAGES_DATA_FILE", "data/pages.json"),
    )
