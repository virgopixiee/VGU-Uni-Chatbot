#!/usr/bin/env python3
"""
VGU (Vietnamese-German University) scraper, v2: spam-resistant, JSON output.

Why v2: vgu.edu.vn currently serves injected SEO-spam pages (random-letter folders such as
/vi/wfcy/zwj/ydv/..., ";jsessionid=..." suffixes, "earn money in pakistan" slugs). v2:
  1. strips ;jsessionid and other path parameters, so one page = one URL
  2. refuses spam-looking URLs before requesting them
  3. checks page title/headings/text for spam and drops those pages
  4. NEVER follows links found on a spam page
  5. lists everything it excluded (with the reason) so you can audit it

Output (in --out folder):
  vgu_data.json    all data: meta, pages, documents, contacts, excluded_spam, failed
  vgu_pages.jsonl  crawl checkpoint (one page per line; use --resume to continue)

Install:  pip install requests beautifulsoup4 lxml pypdf
Run:      python vgu_scraper_v2.py
Test run: python vgu_scraper_v2.py --max-pages 50
Options:  --seed URL (repeatable; add known real pages)   --extract-docs   --save-html
          --no-sitemap   --extra-domain DOMAIN   --workers 6   --delay 0.3   --resume
"""
import argparse
import hashlib
import io
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib import robotparser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

ROOT_DOMAIN = "vgu.edu.vn"
SEEDS = ["https://vgu.edu.vn/", "https://vgu.edu.vn/en", "https://vgu.edu.vn/vi", "https://vgu.edu.vn/de"]
USER_AGENT = "VGU-DataCollector/2.0 (student research project)"
MAX_HTML_BYTES = 5_000_000
MAX_EXCLUDED_LISTED = 5000

SKIP_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tif", ".tiff",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".webm", ".wav",
    ".css", ".js", ".json", ".xml", ".woff", ".woff2", ".ttf", ".eot", ".map",
}
DOC_EXT = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".csv", ".txt", ".odt", ".ods",
}
TRACKING = ("utm_", "fbclid", "gclid", "mc_", "replytocom", "jsessionid")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?84|0)[\s.\-]?\(?\d{1,3}\)?(?:[\s.\-]?\d{2,4}){2,3}")

# ---------------------------------------------------------------- spam detection
# Strong terms: essentially never appear on a real university page.
SPAM_TERMS = re.compile(
    r"pakistan|online[-_ ]?earn|earn(?:ing|ings)?[-_ ](?:money|online|app|game|cash)|real[-_ ]?money|"
    r"make[-_ ]?money|casino|betting|sportsbook|jackpot|\bslots?\b|slot[-_ ]?(?:game|machine)|poker|"
    r"rummy|teen[-_ ]?patti|aviator|\bludo\b|1xbet|jazz[-_ ]?cash|easypaisa|\bapk\b|mod[-_ ]?apk|"
    r"gambl|lottery|viagra|cialis|\bporn|\bxxx\b|escort|payday[-_ ]?loan|replica[-_ ]?(?:watch|bag)|"
    r"withdraw(?:al)?[-_ ](?:money|cash)|download[-_ ]?game|game[-_ ]?download",
    re.I,
)


def gibberish_path(path):
    """Two or more consecutive vowel-less 3-5 letter folders (e.g. /wfcy/zwj/ydv/) = injected spam."""
    run = 0
    for seg in (s for s in path.split("/") if s):
        if 3 <= len(seg) <= 5 and seg.isalpha() and not re.search(r"[aeiou]", seg.lower()):
            run += 1
            if run >= 2:
                return True
        else:
            run = 0
    return False


def url_spam_reason(url):
    p = urlparse(url)
    if SPAM_TERMS.search(p.path + " " + p.query):
        return "spam keyword in URL"
    if gibberish_path(p.path):
        return "random-letter folders in URL"
    if len(p.path) > 250:
        return "abnormally long URL"
    return ""


def content_spam_reason(rec):
    head = " ".join([rec.get("title", ""), rec.get("h1", ""), rec.get("description", ""),
                     rec.get("keywords", "")])
    if SPAM_TERMS.search(head):
        return "spam keyword in title/heading/description"
    hits = {m.group(0).lower() for m in SPAM_TERMS.finditer(rec.get("text", "")[:4000])}
    if len(hits) >= 2:
        return "multiple spam keywords in text"
    return ""


# ---------------------------------------------------------------- helpers
def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_vgu_host(host, extra):
    host = (host or "").lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in (ROOT_DOMAIN, *extra))


def normalize(url):
    """Canonical URL: no fragment, no ;jsessionid / path parameters, no tracking params, no www."""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r";[^/?#]*", "", p.path or "/")
    path = re.sub(r"/{2,}", "/", path) or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                             if not k.lower().startswith(TRACKING)))
    return urlunparse(("https", host, path, "", query, ""))


def ext_of(url):
    m = re.search(r"(\.[a-z0-9]{2,5})$", urlparse(url).path.lower())
    return m.group(1) if m else ""


def clean_text(s):
    return re.sub(r"\s+", " ", s or "").strip()


class Robots:
    def __init__(self, session):
        self.session, self.cache = session, {}

    def _load(self, host):
        if host in self.cache:
            return self.cache[host]
        rp, sitemaps = robotparser.RobotFileParser(), []
        try:
            r = self.session.get(f"https://{host}/robots.txt", timeout=15)
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
                sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
            else:
                rp.parse([])
        except requests.RequestException:
            rp.parse([])
        self.cache[host] = (rp, sitemaps)
        return self.cache[host]

    def allowed(self, url):
        return self._load(urlparse(url).netloc)[0].can_fetch(USER_AGENT, url)

    def sitemaps(self, host):
        return self._load(host)[1]


def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en,de;q=0.8,vi;q=0.7"})
    ad = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=2)
    s.mount("https://", ad)
    s.mount("http://", ad)
    return s


def read_sitemap(session, url, seen=None, depth=0):
    seen = seen if seen is not None else set()
    if url in seen or depth > 3:
        return []
    seen.add(url)
    urls = []
    try:
        r = session.get(url, timeout=20)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError):
        return []
    for el in root.iter():
        if el.tag.split("}")[-1] == "loc" and el.text:
            loc = el.text.strip()
            if loc.lower().endswith(".xml") or "sitemap" in loc.lower():
                urls.extend(read_sitemap(session, loc, seen, depth + 1))
            else:
                urls.append(loc)
    return urls


# ---------------------------------------------------------------- page parsing
def parse_page(final_url, html, extra):
    soup = BeautifulSoup(html, "lxml")
    out = {}

    def meta(*names):
        for n in names:
            tag = soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"property": n})
            if tag and tag.get("content"):
                return clean_text(tag["content"])
        return ""

    out["title"] = clean_text(soup.title.string) if soup.title and soup.title.string else ""
    out["description"] = meta("description", "og:description")
    out["keywords"] = meta("keywords")
    out["og_image"] = meta("og:image")
    out["published"] = meta("article:published_time", "date", "DC.date.issued")
    out["modified"] = meta("article:modified_time", "last-modified")
    if not out["published"]:
        t = soup.find("time", attrs={"datetime": True})
        out["published"] = t["datetime"] if t else ""
    canon = soup.find("link", rel="canonical")
    out["canonical"] = canon["href"] if canon and canon.get("href") else ""
    out["lang"] = (soup.html.get("lang") if soup.html else "") or ""

    internal, external, documents = [], [], []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absu = urljoin(final_url, href)
        p = urlparse(absu)
        if p.scheme not in ("http", "https"):
            continue
        text = clean_text(a.get_text())[:200]
        clean = (normalize(absu) or absu.split("#")[0])
        if ext_of(absu) in DOC_EXT:
            documents.append({"url": clean, "text": text})
        elif is_vgu_host(p.netloc, extra):
            internal.append(absu.split("#")[0])
        else:
            external.append({"url": absu.split("#")[0], "text": text})

    page_text = soup.get_text(" ")
    emails = set(m.lower() for m in EMAIL_RE.findall(page_text))
    phones = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.startswith("mailto:"):
            emails.add(h[7:].split("?")[0].strip().lower())
        elif h.startswith("tel:"):
            phones.add(clean_text(h[4:]))
    for m in PHONE_RE.findall(page_text):
        if 9 <= len(re.sub(r"\D", "", m)) <= 12:
            phones.add(clean_text(m))

    images = []
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src")
        if src and not src.startswith("data:"):
            images.append({"src": urljoin(final_url, src), "alt": clean_text(img.get("alt"))})

    tables = []
    for tbl in soup.find_all("table")[:30]:
        rows = []
        for tr in tbl.find_all("tr"):
            cells = [clean_text(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)

    headings = [{"level": int(h.name[1]), "text": clean_text(h.get_text())}
                for h in soup.find_all(re.compile(r"^h[1-6]$")) if clean_text(h.get_text())]

    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        tag.decompose()
    for tag in soup.find_all(["nav", "header", "footer", "aside"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = clean_text(main.get_text(" "))

    out.update({
        "headings": headings,
        "h1": next((h["text"] for h in headings if h["level"] == 1), ""),
        "text": text, "word_count": len(text.split()),
        "tables": tables, "images": images,
        "internal_links": sorted(set(internal)), "external_links": external, "documents": documents,
        "emails": sorted(emails), "phones": sorted(phones),
        "content_hash": hashlib.md5(text.encode("utf-8")).hexdigest() if text else "",
    })
    return out


def fetch_page(session, robots, url, depth, delay, extra, save_html_dir):
    rec = {"url": url, "depth": depth, "crawled_at": now_iso(), "status": None, "error": ""}
    if not robots.allowed(url):
        rec["error"] = "blocked by robots.txt"
        return rec
    time.sleep(delay)
    try:
        r = session.get(url, timeout=25, stream=True, allow_redirects=True)
        rec["status"], rec["final_url"] = r.status_code, normalize(r.url) or r.url
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        rec["content_type"] = ctype
        if not is_vgu_host(urlparse(r.url).netloc, extra):
            rec["error"] = "redirected off-domain"
            r.close()
            return rec
        # a redirect can land on a spam URL
        reason = url_spam_reason(rec["final_url"])
        if reason:
            rec["spam_reason"] = reason
            r.close()
            return rec
        if r.status_code != 200:
            r.close()
            return rec
        if "html" not in ctype:
            rec["is_document"] = True
            r.close()
            return rec
        html = r.raw.read(MAX_HTML_BYTES, decode_content=True).decode(r.encoding or "utf-8", errors="replace")
        rec.update(parse_page(r.url, html, extra))
        reason = content_spam_reason(rec)
        if reason:
            return {"url": url, "depth": depth, "crawled_at": rec["crawled_at"],
                    "status": 200, "error": "", "spam_reason": reason, "title": rec.get("title", "")}
        if save_html_dir:
            name = hashlib.md5(url.encode()).hexdigest() + ".html"
            (save_html_dir / name).write_text(html, encoding="utf-8")
            rec["html_file"] = name
    except requests.RequestException as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:300]
    return rec


def extract_pdf_text(session, url):
    try:
        from pypdf import PdfReader
        r = session.get(url, timeout=40)
        if r.status_code != 200 or len(r.content) > 30_000_000:
            return ""
        return clean_text(" ".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(r.content)).pages))
    except Exception:
        return ""


# ---------------------------------------------------------------- crawl
def crawl(args):
    extra = tuple(d.lower() for d in args.extra_domain)
    session = make_session()
    robots = Robots(session)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "vgu_pages.jsonl"
    html_dir = None
    if args.save_html:
        html_dir = out_dir / "vgu_html"
        html_dir.mkdir(exist_ok=True)

    seen, pages, excluded = set(), [], []
    if args.resume and jsonl_path.exists():
        with jsonl_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pages.append(rec)
                seen.add(normalize(rec["url"]))
        print(f"Resumed {len(pages)} pages from checkpoint")
    elif jsonl_path.exists():
        jsonl_path.unlink()

    frontier = []

    def exclude(url, reason):
        if len(excluded) < MAX_EXCLUDED_LISTED:
            excluded.append({"url": url, "reason": reason})

    def enqueue(url, depth):
        n = normalize(url)
        if not n or n in seen:
            return
        seen.add(n)
        p = urlparse(n)
        if not is_vgu_host(p.netloc, extra) or ext_of(n) in SKIP_EXT or ext_of(n) in DOC_EXT:
            return
        reason = url_spam_reason(n)
        if reason:
            exclude(n, reason)
            return
        frontier.append((n, depth))

    for s in [*SEEDS, *args.seed]:
        enqueue(s, 0)
    if not args.no_sitemap:
        for host in (ROOT_DOMAIN, "www." + ROOT_DOMAIN, *extra):
            for sm in robots.sitemaps(host) + [f"https://{host}/sitemap.xml", f"https://{host}/sitemap_index.xml"]:
                for u in read_sitemap(session, sm):
                    enqueue(u, 1)
    print(f"Seeded {len(frontier)} clean URLs ({len(excluded)} spam URLs already rejected)")

    with jsonl_path.open("a", encoding="utf-8") as jf:
        while frontier and len(pages) < args.max_pages:
            wave, frontier = frontier[: args.max_pages - len(pages)], []
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = [ex.submit(fetch_page, session, robots, u, d, args.delay, extra, html_dir) for u, d in wave]
                for fut in as_completed(futs):
                    rec = fut.result()
                    pages.append(rec)
                    jf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    jf.flush()
                    if rec.get("spam_reason"):
                        exclude(rec["url"], rec["spam_reason"])
                        continue                      # never follow links from spam pages
                    if rec.get("status") == 200 and rec["depth"] < args.max_depth:
                        for link in rec.get("internal_links", []):
                            enqueue(link, rec["depth"] + 1)
                    if len(pages) % 25 == 0:
                        print(f"  crawled {len(pages)} pages, {len(frontier)} queued, {len(excluded)} spam excluded")

    for p in pages:  # spam recs restored from a resumed checkpoint
        if p.get("spam_reason") and not any(e["url"] == p["url"] for e in excluded):
            exclude(p["url"], p["spam_reason"])

    documents = {}
    for p in pages:
        if p.get("spam_reason"):
            continue
        for d in p.get("documents", []):
            if url_spam_reason(d["url"]):
                continue
            documents.setdefault(d["url"], {"url": d["url"], "text": d["text"], "found_on": []})["found_on"].append(p["url"])
    if args.extract_docs:
        pdfs = [u for u in documents if ext_of(u) == ".pdf"]
        print(f"Extracting text from {len(pdfs)} PDFs...")
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for u, txt in zip(pdfs, ex.map(lambda x: extract_pdf_text(session, x), pdfs)):
                documents[u]["extracted_text"] = txt
    return pages, documents, excluded


# ---------------------------------------------------------------- output
def build_json(pages, documents, excluded, out_dir):
    ok = [p for p in pages if p.get("status") == 200 and not p.get("is_document") and not p.get("spam_reason")]

    by_hash, dups = {}, {}
    for p in ok:
        h = p.get("content_hash")
        if h and h in by_hash:
            dups.setdefault(by_hash[h], []).append(p["url"])
        elif h:
            by_hash[h] = p["url"]

    ok.sort(key=lambda x: x["url"])
    out_pages = []
    for i, p in enumerate(ok, 1):
        u = urlparse(p["url"])
        out_pages.append({
            "id": i, "url": p["url"], "final_url": p.get("final_url", p["url"]),
            "domain": u.netloc, "path": u.path, "depth": p["depth"], "lang": p.get("lang", ""),
            "title": p.get("title", ""), "h1": p.get("h1", ""), "description": p.get("description", ""),
            "keywords": p.get("keywords", ""), "published": p.get("published", ""),
            "modified": p.get("modified", ""), "canonical": p.get("canonical", ""),
            "og_image": p.get("og_image", ""), "word_count": p.get("word_count", 0),
            "is_duplicate": bool(p.get("content_hash")) and by_hash.get(p["content_hash"]) != p["url"],
            "duplicate_urls": dups.get(p["url"], []), "content_hash": p.get("content_hash", ""),
            "headings": p.get("headings", []), "text": p.get("text", ""),
            "tables": p.get("tables", []), "images": p.get("images", []),
            "emails": p.get("emails", []), "phones": p.get("phones", []),
            "internal_links": p.get("internal_links", []), "external_links": p.get("external_links", []),
            "documents": [d["url"] for d in p.get("documents", [])], "crawled_at": p["crawled_at"],
        })

    contacts = {}
    for p in out_pages:
        for e in p["emails"]:
            contacts.setdefault(("email", e), set()).add(p["url"])
        for ph in p["phones"]:
            contacts.setdefault(("phone", ph), set()).add(p["url"])

    domains = {}
    for p in out_pages:
        d = domains.setdefault(p["domain"], {"pages": 0, "words": 0})
        d["pages"] += 1
        d["words"] += p["word_count"]

    failed = [{"url": p["url"], "status": p.get("status"), "error": p.get("error", "")}
              for p in pages if (p.get("status") != 200 or p.get("error")) and not p.get("spam_reason")]

    data = {
        "meta": {
            "source": "Official VGU websites (*.vgu.edu.vn), spam-filtered",
            "crawled_at": now_iso(), "pages_ok": len(out_pages), "pages_failed": len(failed),
            "spam_excluded": len(excluded), "documents_found": len(documents),
            "unique_emails": sum(1 for k in contacts if k[0] == "email"),
            "unique_phones": sum(1 for k in contacts if k[0] == "phone"),
            "domains": domains,
        },
        "pages": out_pages,
        "documents": list(documents.values()),
        "contacts": [{"type": k[0], "value": k[1], "found_on": sorted(v)} for k, v in sorted(contacts.items())],
        "excluded_spam": excluded,
        "failed": failed,
    }
    path = Path(out_dir) / "vgu_data.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data["meta"], path


def main():
    ap = argparse.ArgumentParser(description="Scrape official VGU websites into one JSON file (spam-filtered)")
    ap.add_argument("--out", default=".", help="output folder")
    ap.add_argument("--max-pages", type=int, default=5000)
    ap.add_argument("--max-depth", type=int, default=8)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--seed", action="append", default=[], help="extra known-real start URL (repeatable)")
    ap.add_argument("--extra-domain", action="append", default=[])
    ap.add_argument("--no-sitemap", action="store_true", help="do not seed from sitemaps")
    ap.add_argument("--extract-docs", action="store_true", help="extract text from linked PDFs")
    ap.add_argument("--save-html", action="store_true")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    pages, documents, excluded = crawl(args)
    meta, path = build_json(pages, documents, excluded, args.out)
    print(f"\nDone: {meta['pages_ok']} clean pages, {meta['spam_excluded']} spam excluded, "
          f"{meta['pages_failed']} failed, {meta['documents_found']} documents")
    print(f"Saved: {path}")


if __name__ == "__main__":
    main()