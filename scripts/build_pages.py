import csv
import json
import re
from pathlib import Path

CURATED_DIR = Path("data/curated")
PRIMARY_OUT = Path("data/pages.json")

# Languages to load from the knowledge base. The search matches English words,
# so only English is used for now. Add "vi" later if needed.
LANGS = ("en",)
REQUIRED_COLUMNS = ["id", "category", "language", "question", "answer", "source"]


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # 2026.0 -> 2026
    return str(value).strip()


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV or XLSX file into a list of {column: text} dicts."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return [
                {(k or "").strip(): (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(f)
            ]
    from openpyxl import load_workbook

    ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [str(c).strip() if c is not None else "" for c in next(rows, [])]
    result = []
    for values in rows:
        if not any(v is not None and str(v).strip() for v in values):
            continue
        result.append(
            {
                header[i]: _cell_text(v)
                for i, v in enumerate(values)
                if i < len(header) and header[i]
            }
        )
    return result


def sheet_pages() -> list[dict] | None:
    """Pages from the data team's spreadsheet. None if no sheet file exists."""
    files = sorted(
        p
        for p in CURATED_DIR.glob("*")
        if p.suffix.lower() in {".csv", ".xlsx"} and not p.name.startswith("~$")
    )
    if not files:
        return None
    pages, seen_ids = [], set()
    problems = 0
    for path in files:
        rows = read_rows(path)
        if not rows:
            print(f"{path.name}: no rows found")
            continue
        missing = [c for c in REQUIRED_COLUMNS if c not in rows[0]]
        if missing:
            raise SystemExit(f"{path.name}: missing column(s): {', '.join(missing)}")
        kept = 0
        for n, row in enumerate(rows, start=2):  # row 1 is the header
            rid = row.get("id", "")
            lang = row.get("language", "").lower()
            if any(not row.get(c) for c in REQUIRED_COLUMNS):
                print(f"  skipped {path.name} row {n} ({rid or 'no id'}): empty required cell")
                problems += 1
                continue
            if rid in seen_ids:
                print(f"  skipped {path.name} row {n} ({rid}): duplicate id")
                problems += 1
                continue
            seen_ids.add(rid)
            if lang not in LANGS:
                continue
            lines = [row["question"], row["answer"]]
            category = row["category"].replace("_", " ")
            lines.append(f"Topic: {category}")
            if row.get("year"):
                lines.append(f"Applies to: {row['year']}")
            note = row.get("note", "")
            status = row.get("status", "").lower()
            if status == "needs_confirmation" or "confirm" in note.lower():
                lines.append(f"Note: {note or 'Not confirmed. Check with VGU.'}")
            pages.append(
                {"url": row["source"], "title": row["question"], "text": "\n".join(lines)}
            )
            kept += 1
        print(f"Spreadsheet {path.name}: {kept} pages ({', '.join(LANGS)})")
    if problems:
        print(f"  {problems} row(s) skipped, please tell the data team")
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
    print(f"Curated text files: {len(pages)} sections")
    return pages


def build_primary() -> None:
    # The .xlsx/.csv files in data/curated/ are the only data source.
    pages = sheet_pages()
    if pages is None:
        raise SystemExit("No .xlsx or .csv file found in data/curated/. Add the spreadsheet first.")
    pages += curated_pages()
    PRIMARY_OUT.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Primary: wrote {len(pages)} pages to {PRIMARY_OUT}")


if __name__ == "__main__":
    build_primary()
