"""Load, validate and flatten the VGU chatbot knowledge base.

Usage:
    python vgu_kb.py              # validates vgu_chatbot_data.json and prints a summary

    import vgu_kb
    kb = vgu_kb.load()
    docs = vgu_kb.to_documents(kb, lang="en")   # flat records for embeddings / retrieval
    text = vgu_kb.render_answer(kb, kb["entries"][0], "de")
"""
import json
from pathlib import Path

DATA_FILE = Path(__file__).with_name("vgu_chatbot_data.json")
SCOPES = {"cse", "university"}


def load(path=DATA_FILE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate(kb):
    """Return a list of problems (an empty list means the file is consistent)."""
    errors = []
    meta, langs = kb["meta"], kb["meta"]["languages"]
    seen = set()
    for e in kb["entries"]:
        i = e.get("id", "<no id>")
        if i in seen:
            errors.append(f"{i}: duplicate id")
        seen.add(i)
        if e.get("scope") not in SCOPES:
            errors.append(f"{i}: bad scope {e.get('scope')!r}")
        if not e.get("sources") or not all(s.startswith("http") for s in e["sources"]):
            errors.append(f"{i}: missing or invalid sources")
        for lang in langs:
            t = e.get("translations", {}).get(lang)
            if not t or not t.get("question", "").strip() or not t.get("answer", "").strip():
                errors.append(f"{i}: missing {lang} question/answer")
        for f in e.get("flags", []):
            if f not in meta["flags"]:
                errors.append(f"{i}: unknown flag {f!r}")
        if e.get("caveat") and e["caveat"] not in meta["caveats"]:
            errors.append(f"{i}: unknown caveat {e['caveat']!r}")
        a = e.get("applies_to")
        if a is not None and not (isinstance(a.get("year"), int) and a.get("basis") in {"intake", "document"}):
            errors.append(f"{i}: bad applies_to {a!r}")
    return errors


def notes(kb, entry, lang):
    """User-facing warnings for an entry (flags + caveat) in the given language."""
    out = [kb["meta"]["flags"][f][lang] for f in entry["flags"]]
    if entry["caveat"]:
        out.append(kb["meta"]["caveats"][entry["caveat"]][lang])
    return out


def render_answer(kb, entry, lang):
    """The answer text followed by its warnings, ready to show to the user."""
    return " ".join([entry["translations"][lang]["answer"], *notes(kb, entry, lang)])


def to_documents(kb, lang=None, scope=None):
    """Flat records, one per entry and language, for embedding or retrieval."""
    docs = []
    for e in kb["entries"]:
        if scope and e["scope"] != scope:
            continue
        for l in ([lang] if lang else kb["meta"]["languages"]):
            t = e["translations"][l]
            docs.append({
                "id": f"{e['id']}:{l}",
                "lang": l,
                "text": f"{t['question']}\n{t['answer']}",
                "answer": render_answer(kb, e, l),
                "metadata": {
                    "entry_id": e["id"],
                    "category": e["category"],
                    "scope": e["scope"],
                    "applies_to": e["applies_to"],
                    "sources": e["sources"],
                },
            })
    return docs


if __name__ == "__main__":
    kb = load()
    problems = validate(kb)
    print(f"{len(kb['entries'])} entries, languages: {', '.join(kb['meta']['languages'])}")
    print(f"{len(to_documents(kb))} flat documents")
    if problems:
        print(f"{len(problems)} problem(s):")
        print("\n".join(problems))
        raise SystemExit(1)
    print("OK: no problems found")
