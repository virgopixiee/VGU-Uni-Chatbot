import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app import state
from app.api.admin import router as admin_router
from app.api.chat import router as chat_router

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="School Website Information Chatbot")
app.include_router(chat_router)
app.include_router(admin_router)
web_root = Path(__file__).parent / "static"


@app.on_event("startup")
def load_index() -> None:
    state.init_pages()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(web_root / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
