import json
import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.config import load_settings
from backend.pages import Page, deserialize_pages, serialize_pages
from backend.llm import answer_question
from backend.search import search_pages
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = load_settings()
app = FastAPI(title="School Website Information Chatbot")
pages: list[Page] = []
data_path = Path(settings.data_file)
backup_path = Path("data/backup_pages.json")
backup_pages: list[Page] = []
MIN_PRIMARY_SCORE = 2.0
web_root = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


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

def load_backup_pages() -> list[Page]:
   if not backup_path.exists():
       return []
   raw = json.loads(backup_path.read_text(encoding="utf-8"))
   return deserialize_pages(raw)

@app.on_event("startup")
def load_index() -> None:
   global pages, backup_pages
   pages = load_saved_pages()
   backup_pages = load_backup_pages()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(web_root / "index.html")


@app.get("/api/status")
def status() -> dict[str, str | int]:
    return {
        "site_url": settings.site_url,
        "provider": settings.provider,
        "indexed_pages": len(pages),
        "max_pages": settings.max_pages,
    }


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, object]:
    if not pages and not backup_pages:
        raise HTTPException(
            status_code=409,
            detail="No website pages are indexed yet. Run scripts/build_pages.py first.",
        )
    sources = search_pages(request.question, pages, limit=6)
    try:
        answer = answer_question(request.question, sources, settings)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        logger.exception("The configured language model could not answer.")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "answer": answer,
        "sources": [
            {
                "title": source.page.title,
                "url": source.page.url,
                "excerpt": source.excerpt,
            }
            for source in sources
        ],
    }
