import json
import re
from pathlib import Path

BACKUP_SRC = Path("data/raw/EVERYTHING DATA/EVERYTHING_DATA.jsonl")
BACKUP_OUT = Path("data/backup_pages.json")
KB_FILE = Path("data/processed/vgu_chatbot_data.json")
CURATED_DIR = Path("data/curated")
PRIMARY_OUT = Path("data/pages.json")
MIN_WORDS = 30
MAX_TEXT = 50_000


def build_backup() -> None:
    if not BACKUP_SRC.exists():
        print(f"Backup source not found: {BACKUP_SRC}")
        return
    pages, seen = [], set()
    total = skipped = 0
    with BACKUP_SRC.open(encoding="utf-8") as f:
        for line in f:
            total += 1
            rec = json.loads(line)
            text = (rec.get("text") or "").strip()
            content_type = rec.get("content_type") or ""
            if rec.get("status") != 200 or "html" not in content_type:
                skipped += 1
                continue
            if len(text.split()) < MIN_WORDS:
                skipped += 1
                continue
            content_hash = rec.get("content_hash")
            if content_hash:
                if content_hash in seen:
                    skipped += 1
                    continue
                seen.add(content_hash)
            url = rec.get("final_url") or rec.get("url") or ""
            pages.append(
                {
                    "url": url,
                    "title": (rec.get("title") or url)[:300],
                    "text": text[:MAX_TEXT],
                }
            )
    BACKUP_OUT.parent.mkdir(parents=True, exist_ok=True)
    BACKUP_OUT.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    print(f"Backup: kept {len(pages)} of {total} records ({skipped} skipped)")


def kb_pages() -> list[dict]:
    if not KB_FILE.exists():
        print(f"Knowledge base not found: {KB_FILE}")
        return []
    kb = json.loads(KB_FILE.read_text(encoding="utf-8"))
    pages = []
    entries = kb.get("entries", [])
    for entry in entries:
        sources = entry.get("sources") or []
        url = sources[0] if sources else entry.get("id", "knowledge-base")
        category = (entry.get("category") or "").replace("_", " ")
        more = f"\nMore sources: {', '.join(sources[1:])}" if len(sources) > 1 else ""
        for lang, t in (entry.get("translations") or {}).items():
            question = (t.get("question") or "").strip()
            answer = (t.get("answer") or "").strip()
            if not question or not answer:
                continue
            pages.append(
                {
                    "url": url,
                    "title": question,
                    "text": f"{question}\n{answer}\nTopic: {category}{more}",
                }
            )
    print(f"Knowledge base: {len(entries)} entries -> {len(pages)} pages")
    return pages


def split_sections(file_title: str, text: str) -> list[tuple[str, str]]:
    """Split Markdown text at headings; otherwise keep it as one section."""
    parts = re.split(r"^(#{1,3} .+)$", text, flags=re.MULTILINE)
    sections = []
    if parts[0].strip():
        sections.append((file_title, parts[0].strip()))
    for heading, body in zip(parts[1::2], parts[2::2]):
        if body.strip():
            sections.append(
                (f"{file_title} - {heading.lstrip('# ').strip()}", body.strip())
            )
    return sections


def curated_pages() -> list[dict]:
    CURATED_DIR.mkdir(parents=True, exist_ok=True)
    pages = []
    for path in sorted(CURATED_DIR.rglob("*")):
        if path.suffix.lower() not in {".txt", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        title = path.stem.replace("_", " ").replace("-", " ")
        for section_title, body in split_sections(title, text):
            pages.append(
                {"url": path.as_posix(), "title": section_title, "text": body}
            )
    print(f"Curated files: {len(pages)} sections")
    return pages


def build_primary() -> None:
    pages = kb_pages() + curated_pages()
    PRIMARY_OUT.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Primary: wrote {len(pages)} pages to {PRIMARY_OUT}")


if __name__ == "__main__":
    build_backup()
    build_primary()
