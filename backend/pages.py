from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str
    lang: str = ""


def serialize_pages(pages: list[Page]) -> list[dict[str, str]]:
    return [asdict(page) for page in pages]


def deserialize_pages(items: list[dict[str, str]]) -> list[Page]:
    pages: list[Page] = []
    for item in items:
        url, title, text = item.get("url"), item.get("title"), item.get("text")
        if isinstance(url, str) and isinstance(title, str) and isinstance(text, str):
            lang = item.get("lang")
            pages.append(
                Page(url=url, title=title, text=text, lang=lang if isinstance(lang, str) else "")
            )
    return pages
