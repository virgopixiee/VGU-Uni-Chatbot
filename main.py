import json
import logging
import re
import threading
import unicodedata
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.config import load_settings
from backend.pages import Page, deserialize_pages
from backend.llm import answer_question
from backend.search import SearchResult, search_pages
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = load_settings()
app = FastAPI(title="School Website Information Chatbot")
pages: list[Page] = []
pages_by_lang: dict[str, list[Page]] = {"en": [], "vi": []}
data_path = Path(settings.data_file)
web_root = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    message: str | None = Field(default=None, min_length=2, max_length=1000)
    question: str | None = Field(default=None, min_length=2, max_length=1000)  # old name
    session_id: str | None = Field(default=None, max_length=64)


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


@app.on_event("startup")
def load_index() -> None:
    global pages, pages_by_lang
    pages = load_saved_pages()
    # rows without a language tag (curated text files) are searched in both
    pages_by_lang = {
        lang: [page for page in pages if page.lang in (lang, "")] for lang in ("en", "vi")
    }


def _warm_up_model() -> None:
    """Load the model into memory once at startup so the first question is fast."""
    try:
        httpx.post(
            f"{settings.ollama_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "keep_alive": "30m",
                "options": {"num_predict": 1},
            },
            timeout=300.0,
        )
        logger.info("Model warmed up.")
    except httpx.HTTPError as exc:
        logger.warning("Could not warm up the model: %s", exc)


@app.on_event("startup")
def start_warm_up() -> None:
    if settings.provider == "ollama":
        threading.Thread(target=_warm_up_model, daemon=True).start()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(web_root / "index.html")


@app.get("/api/status")
def status() -> dict[str, str | int]:
    return {
        "provider": settings.provider,
        "indexed_pages": len(pages),
    }


VIETNAMESE_ONLY_LETTERS = set(
    "ăâđêôơưĩũạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"
)
VIETNAMESE_HINT_WORDS = {
    "la", "bao", "nhieu", "khong", "cua", "toi", "minh", "gio",
    "hoc", "phi", "nao", "sao", "dang", "ky", "may",
}


def _plain_words(text: str) -> set[str]:
    folded = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return set(re.findall(r"[^\W_]+", folded))


def find_sources(question: str) -> tuple[str, list[SearchResult]]:
    """Return (language, sources), searching only the question's language."""
    forced = "vi" if any(ch in VIETNAMESE_ONLY_LETTERS for ch in question.casefold()) else None
    best_lang, best = None, []
    for lang in [forced] if forced else ["en", "vi"]:
        hits = search_pages(question, pages_by_lang[lang], limit=6)
        if hits and (not best or hits[0].score > best[0].score):
            best_lang, best = lang, hits
    if best_lang is None:  # nothing matched: only guess the language of the reply
        vietnamese = forced or len(_plain_words(question) & VIETNAMESE_HINT_WORDS) >= 2
        best_lang = "vi" if vietnamese else "en"
    return best_lang, best


@app.post("/chat")
def chat(request: ChatRequest) -> dict[str, object]:
    text = request.message or request.question
    if not text:
        raise HTTPException(status_code=422, detail="Send your question in the 'message' field.")
    if not pages:
        raise HTTPException(
            status_code=409,
            detail="No knowledge base is loaded yet. Run scripts/build_pages.py first.",
        )
    language, sources = find_sources(text)
    try:
        answer = answer_question(text, sources, settings, language)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        logger.exception("The configured language model could not answer.")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "answer": answer,
        "language": language,
        "sources": [
            {
                "title": source.page.title,
                "url": source.page.url,
                "excerpt": source.excerpt,
            }
            for source in sources
        ],
    }
