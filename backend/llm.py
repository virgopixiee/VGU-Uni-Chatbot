import httpx

from backend.config import Settings
from backend.search import SearchResult


def _gemini_api_error_message(status: int, message: str) -> str:
    if status == 503:
        return (
            "Gemini is temporarily busy (503). Wait a minute and try again. "
            "Your indexed pages are still saved; you do not need to refresh them."
        )
    if status == 429:
        return (
            "Gemini API rate limit or quota reached (429). Wait and retry, or "
            "check your API quota and billing in Google AI Studio."
        )
    return f"Gemini API request failed ({status}): {message}"


def answer_question(
    question: str, sources: list[SearchResult], settings: Settings
) -> str:
    if not sources:
        return (
            "I couldn't find relevant information in the pages currently indexed. "
            "Try refreshing the school website index or rephrasing your question."
        )

    evidence = "\n\n".join(
        f"[{index}] {source.page.title}\n"
        f"URL: {source.page.url}\n"
        f"Page text: {source.excerpt}"
        for index, source in enumerate(sources, start=1)
    )
    prompt = (
        "Answer the user's question using only the school website excerpts below. "
        "Do not rely on outside knowledge. If the excerpts do not contain the answer, "
        "say that you could not find it. For requests to list items, include every "
        "item present in the excerpts rather than giving only examples. Preserve "
        "bullet points or numbered lists when helpful. Cite factual claims with the "
        "matching source number, like [1].\n\n"
        f"Website excerpts:\n{evidence}\n\n"
        f"Question: {question}"
    )

    if settings.provider == "ollama":
        response = httpx.post(
            f"{settings.ollama_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Ollama returned an empty answer.")
        return content.strip()

    if not settings.gemini_api_key:
        raise RuntimeError(
            "Gemini is selected, but GEMINI_API_KEY is not set in the environment."
        )
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "The Gemini SDK is not installed. Install it with "
            "'python -m pip install -r requirements-gemini.txt'."
        ) from exc

    client = genai.Client(api_key=settings.gemini_api_key)
    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
        )
    except genai.errors.APIError as exc:
        message = _gemini_api_error_message(exc.code, exc.message)
        raise RuntimeError(message) from exc
    if not response.text:
        raise RuntimeError("Gemini returned an empty answer.")
    return response.text.strip()
