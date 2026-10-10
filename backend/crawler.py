from collections import deque
import ast
from dataclasses import asdict, dataclass
import re
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup


USER_AGENT = "SchoolInfoBot/0.1 (public-information chatbot)"
MAX_PAGE_TEXT = 50_000
CONTENT_BLOCKS = ("h1", "h2", "h3", "h4", "p", "li", "tr")
URL_TOPIC_GROUPS = (
    ("facility", "facilities", "infrastructure", "laborator", "library", "campus"),
    ("tuition", "fee", "scholarship", "financing"),
    ("student", "accommodation", "housing", "dormitory", "service"),
    ("academic", "faculty", "research"),
)
PROGRAM_LANDING_PATHS = {
    "/study-program",
    "/study-programs/bachelor-programs",
    "/study-programs/master-programs",
}
EMBEDDED_ARTICLE_RE = re.compile(
    r"\btxtnoidung\s*:\s*'((?:\\.|[^'\\])*)'",
    re.DOTALL,
)
ADMISSION_LANDING_PATHS = {
    "/admission",
    "/admission/bachelor-admission",
    "/admission/master-admission",
    "/study-admission",
}


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str


def _normalize_url(url: str) -> str | None:
    url, _fragment = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    path = re.sub(r";jsessionid=[^/;]+", "", parsed.path, flags=re.IGNORECASE)
    return urlunparse((parsed.scheme, parsed.netloc, path or "/", "", "", ""))


def _is_indexable_url(url: str) -> bool:
    path_segments = urlparse(url).path.casefold().split("/")
    return "video" not in path_segments


def _extract_embedded_articles(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    article_texts: list[str] = []
    article_links: list[str] = []
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text()
        for match in EMBEDDED_ARTICLE_RE.finditer(script_text):
            try:
                article_html = ast.literal_eval(f"'{match.group(1)}'")
            except (SyntaxError, ValueError) as exc:
                raise ValueError("Could not decode an embedded article payload.") from exc

            article_soup = BeautifulSoup(article_html, "html.parser")
            for element in article_soup(["script", "style", "noscript", "svg"]):
                element.decompose()
            article_text = " ".join(article_soup.stripped_strings)
            if article_text:
                article_texts.append(article_text)
            article_links.extend(
                link["href"]
                for link in article_soup.find_all("a", href=True)
                if isinstance(link.get("href"), str)
            )
    return article_texts, article_links


def _extract_page(url: str, html: str) -> tuple[Page, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    article_texts, article_links = _extract_embedded_articles(soup)
    links = [
        link["href"]
        for link in soup.find_all("a", href=True)
        if isinstance(link.get("href"), str)
    ]
    for element in soup(
        ["script", "style", "noscript", "svg", "nav", "header", "footer", "aside"]
    ):
        element.decompose()
    text_root = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id=re.compile(r"content|main", re.IGNORECASE))
        or soup.body
        or soup
    )
    blocks = [
        " ".join(block.stripped_strings)
        for block in text_root.find_all(CONTENT_BLOCKS)
        if block.get_text(" ", strip=True)
    ]
    text = "\n".join(blocks or [" ".join(text_root.stripped_strings)])
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    for article_text in article_texts:
        if article_text not in text:
            text = f"{text}\n{article_text}".strip()
    links.extend(article_links)
    return Page(url=url, title=title[:300], text=text[:MAX_PAGE_TEXT]), links


def _sitemap_urls(client: httpx.Client, robots: RobotFileParser, start_url: str) -> list[str]:
    start_parts = urlparse(start_url)
    sitemap_urls = robots.site_maps() or [
        f"{start_parts.scheme}://{start_parts.netloc}/sitemap.xml"
    ]
    host = start_parts.hostname
    discovered: list[str] = []
    visited_sitemaps: set[str] = set()

    while sitemap_urls and len(visited_sitemaps) < 10:
        sitemap_url = sitemap_urls.pop(0)
        normalized_sitemap = _normalize_url(sitemap_url)
        if (
            normalized_sitemap is None
            or urlparse(normalized_sitemap).hostname != host
            or normalized_sitemap in visited_sitemaps
        ):
            continue
        visited_sitemaps.add(normalized_sitemap)
        response = client.get(normalized_sitemap)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        for element in root.iter():
            if not element.tag.endswith("loc") or not element.text:
                continue
            location = element.text.strip()
            if root.tag.endswith("sitemapindex"):
                sitemap_urls.append(location)
                continue
            normalized = _normalize_url(location)
            if normalized and urlparse(normalized).hostname == host:
                discovered.append(normalized)

    return discovered


def _sitemap_priority(url: str) -> tuple[int, int, int, str]:
    path = urlparse(url).path.casefold()
    is_vietnamese = int(path.startswith("/vi/"))
    language_path = path[3:] if path.startswith("/vi/") else path
    path_parts = [part for part in language_path.split("/") if part]

    if not _is_indexable_url(url):
        rank = 100
    elif language_path in PROGRAM_LANDING_PATHS:
        rank = 0
    elif language_path in ADMISSION_LANDING_PATHS:
        rank = 1
    elif any(
        term in language_path
        for term in ("facility", "facilities", "infrastructure", "laborator", "library", "campus")
    ):
        rank = 2
    elif language_path.startswith("/study-programs/") and len(path_parts) <= 4:
        rank = 3
    elif any(
        term in language_path
        for term in ("tuition", "fee", "scholarship", "financing")
    ):
        rank = 4
    elif any(
        term in language_path
        for term in ("admission", "requirement", "eligibility", "application")
    ):
        rank = 5
    elif language_path.startswith("/study-programs/"):
        rank = 6
    elif any(
        term in language_path
        for group in URL_TOPIC_GROUPS
        for term in group
    ):
        rank = 6
    else:
        rank = 10

    return rank, is_vietnamese, len(path_parts), language_path


def crawl_site(start_url: str, max_pages: int) -> tuple[list[Page], list[str]]:
    start = _normalize_url(start_url)
    if start is None:
        raise ValueError("The configured school URL is not a valid HTTP(S) URL.")

    host = urlparse(start).hostname
    assert host is not None
    robots_url = f"{urlparse(start).scheme}://{urlparse(start).netloc}/robots.txt"
    pages: list[Page] = []
    failures: list[str] = []
    queue = deque([start])
    queued = {start}

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=15.0,
        follow_redirects=True,
    ) as client:
        robots_response = client.get(robots_url)
        if robots_response.status_code == 404:
            robots = RobotFileParser()
            robots.parse([])
        else:
            robots_response.raise_for_status()
            robots = RobotFileParser()
            robots.parse(robots_response.text.splitlines())

        try:
            sitemap_pages = _sitemap_urls(client, robots, start)
        except (httpx.HTTPError, ElementTree.ParseError) as exc:
            failures.append(f"Sitemap: {exc}")
            sitemap_pages = []
        sitemap_pages.sort(key=_sitemap_priority)
        for url in sitemap_pages:
            if (
                url not in queued
                and _is_indexable_url(url)
                and robots.can_fetch(USER_AGENT, url)
            ):
                queued.add(url)
                queue.append(url)

        while queue and len(pages) < max_pages:
            url = queue.popleft()
            if not robots.can_fetch(USER_AGENT, url):
                continue

            try:
                response = client.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type:
                    continue
                if urlparse(str(response.url)).hostname != host:
                    failures.append(f"{url}: redirected outside the configured host")
                    continue
                page, links = _extract_page(url, response.text)
            except httpx.HTTPError as exc:
                failures.append(f"{url}: {exc}")
                continue
            except ValueError as exc:
                failures.append(f"{url}: {exc}")
                continue

            if page.text:
                pages.append(page)
            for href in links:
                target = _normalize_url(urljoin(url, href))
                if (
                    target is None
                    or target in queued
                    or not _is_indexable_url(target)
                ):
                    continue
                target_parts = urlparse(target)
                if target_parts.hostname != host:
                    continue
                queued.add(target)
                queue.append(target)

    return pages, failures


def serialize_pages(pages: list[Page]) -> list[dict[str, str]]:
    return [asdict(page) for page in pages]


def deserialize_pages(items: list[dict[str, str]]) -> list[Page]:
    pages: list[Page] = []
    for item in items:
        url, title, text = item.get("url"), item.get("title"), item.get("text")
        if isinstance(url, str) and isinstance(title, str) and isinstance(text, str):
            pages.append(Page(url=url, title=title, text=text))
    return pages
