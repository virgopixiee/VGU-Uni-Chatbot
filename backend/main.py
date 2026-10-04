from fastapi import FastAPI
from api.chat import router

app = FastAPI()
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}