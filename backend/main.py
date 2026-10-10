import json
import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import load_settings
from app.crawler import Page, crawl_site, deserialize_pages, serialize_pages
from app.llm import answer_question
from app.search import search_pages


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = load_settings()
app = FastAPI(title="School Website Information Chatbot")
pages: list[Page] = []
data_path = Path(settings.data_file)
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


@app.on_event("startup")
def load_index() -> None:
    global pages
    pages = load_saved_pages()


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


@app.post("/api/crawl")
def refresh_site_index() -> dict[str, int | list[str]]:
    global pages
    try:
        new_pages, failures = crawl_site(settings.site_url, settings.max_pages)
        if not new_pages:
            raise HTTPException(
                status_code=502,
                detail="The crawl found no readable pages. Check the site URL and robots.txt.",
            )
        save_pages(new_pages)
        pages = new_pages
    except HTTPException:
        raise
    except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
        logger.exception("Could not refresh the school website index.")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "indexed_pages": len(pages),
        "failed_pages": len(failures),
        "failures": failures[:10],
    }


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, object]:
    if not pages:
        raise HTTPException(
            status_code=409,
            detail="No website pages are indexed yet. Click “Refresh website index” first.",
        )
    sources = search_pages(request.question, pages)
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
