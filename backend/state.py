import json
import logging
from pathlib import Path

from app.config import load_settings
from app.crawler import Page, deserialize_pages, serialize_pages

logger = logging.getLogger(__name__)
settings = load_settings()
data_path = Path(settings.data_file)
pages: list[Page] = []


def load_saved_pages() -> list[Page]:
    if not data_path.exists():
        return []
    try:
        raw = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Could not read the saved page index.")
        raise RuntimeError(f"Could not read saved page index: {exc}") from exc
    if not isinstance(raw, list):
        raise RuntimeError("Saved page index must contain a JSON list.")
    return deserialize_pages(raw)


def save_pages(new_pages: list[Page]) -> None:
    data_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = data_path.with_suffix(data_path.suffix + ".tmp")
    try:
        temporary_path.write_text(
            json.dumps(serialize_pages(new_pages), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(data_path)
    except OSError as exc:
        logger.exception("Could not save the page index.")
        raise RuntimeError(f"Could not save page index: {exc}") from exc


def init_pages() -> None:
    global pages
    pages = load_saved_pages()


def set_pages(new_pages: list[Page]) -> None:
    global pages
    save_pages(new_pages)
    pages = new_pages
